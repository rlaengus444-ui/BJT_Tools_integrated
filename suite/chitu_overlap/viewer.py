"""Standalone, offline 3D inspection of the placed meshes and failed pairs."""

from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np

from .geometry import MeshObject
from .display_preview import PreviewUnavailable


# Bound both arrays: a triangle budget alone would still allow millions of
# unused vertices to become Python floats during JSON serialization.
MAX_EXACT_FACES = 200_000
MAX_EXACT_VERTICES = 120_000
_CHUNK_ROWS = 65_536
_SMALL_FACES = 4_096
_RESERVE_ROWS = 256


def _shape_ok(values) -> bool:
    return getattr(values, "ndim", 0) == 2 and tuple(values.shape[1:]) == (3,)


def _report_bounds(record) -> list | None:
    if record is None or record.get("bounds") is None:
        return None
    try:
        bounds = np.asarray(record["bounds"], dtype=float)
    except (TypeError, ValueError):
        return None
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[0] > bounds[1]):
        return None
    return bounds.tolist()


def _checked_bounds(vertices, faces) -> list | None:
    """Validate in small chunks without constructing mesh caches."""
    lower = np.full(3, np.inf)
    upper = np.full(3, -np.inf)
    for start in range(0, len(vertices), _CHUNK_ROWS):
        part = np.asarray(vertices[start:start + _CHUNK_ROWS])
        if not np.isfinite(part).all():
            return None
        lower = np.minimum(lower, part.min(axis=0))
        upper = np.maximum(upper, part.max(axis=0))
    for start in range(0, len(faces), _CHUNK_ROWS):
        part = np.asarray(faces[start:start + _CHUNK_ROWS])
        if not np.issubdtype(part.dtype, np.integer) or np.any(part < 0) or np.any(part >= len(vertices)):
            return None
    return [lower.tolist(), upper.tolist()]


def _box_trace(bounds, index, label, face_count):
    lower, upper = bounds
    corners = [[upper[axis] if bit & (1 << axis) else lower[axis] for axis in range(3)]
               for bit in range(8)]
    edges = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3),
             (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))
    points = [corners[end] if end is not None else None
              for edge in edges for end in (*edge, None)]
    return {
        "type": "scatter3d", "mode": "lines", "name": label,
        "meta": {"object_index": index, "preview_kind": "bounds", "bounds": bounds},
        **{axis: [point[number] if point is not None else None for point in points]
           for number, axis in enumerate(("x", "y", "z"))},
        "line": {"width": 4, "color": "#9caec0"}, "showlegend": False,
        "hovertemplate": label + f"<br>외곽박스 미리보기 · 원본 {face_count:,}개 삼각형"
                         + "<br>X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z %{z:.2f} mm<extra></extra>",
    }


def _preview_plan(objects, report):
    """Reserve a fair shape budget before granting selected exact detail."""
    selected = set(report.get("selected_pair") or ())
    failed = {pair[key] for pair in report.get("violations", [])
              for key in ("index_a", "index_b")}
    entries = []
    for index, obj in enumerate(objects):
        vertices, faces = obj.mesh.vertices, obj.mesh.faces
        drawable = _shape_ok(vertices) and _shape_ok(faces) and bool(len(vertices)) and bool(len(faces))
        entries.append((index, vertices, faces, drawable))
    priorities = sorted((entry for entry in entries if entry[3]),
                        key=lambda item: (0 if item[0] in selected else 1 if item[0] in failed else 2,
                                          len(item[2]), len(item[1]), item[0]))
    capacity = {index: (len(faces), len(vertices)) for index, vertices, faces, _ in priorities}
    limits = (MAX_EXACT_FACES, MAX_EXACT_VERTICES)
    if all(sum(values[axis] for values in capacity.values()) <= limits[axis] for axis in (0, 1)):
        return entries, capacity
    allocations = {index: [0, 0] for index in capacity}
    remaining = list(limits)
    # Tiny meshes remain exact. If even minimum reservations cannot fit, the
    # selected objects win and every omitted object gets an explicit fallback.
    for index, _vertices, faces, _ in priorities:
        raw = capacity[index]
        reservation = raw if len(faces) <= _SMALL_FACES else tuple(min(value, _RESERVE_ROWS) for value in raw)
        if all(reservation[axis] <= remaining[axis] for axis in (0, 1)):
            allocations[index] = list(reservation)
            for axis in (0, 1):
                remaining[axis] -= reservation[axis]
    for index in sorted(selected):
        if index not in capacity or not all(allocations[index]):
            continue
        extras = [capacity[index][axis] - allocations[index][axis] for axis in (0, 1)]
        if all(extras[axis] <= remaining[axis] for axis in (0, 1)):
            allocations[index] = list(capacity[index])
            for axis in (0, 1):
                remaining[axis] -= extras[axis]
    for axis in (0, 1):
        active = [index for index in capacity if all(allocations[index]) and allocations[index][axis] < capacity[index][axis]]
        while active and remaining[axis]:
            share = max(1, remaining[axis] // len(active))
            for index in active:
                take = min(share, capacity[index][axis] - allocations[index][axis], remaining[axis])
                allocations[index][axis] += take
                remaining[axis] -= take
            active = [index for index in active if allocations[index][axis] < capacity[index][axis]]
    return entries, allocations


def _safe_json(value: object) -> str:
    # HTML's script parser does not respect JSON string quoting. Escaping '<'
    # prevents user-controlled object names from terminating this script block.
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def write_html_report(objects: list[MeshObject], report: dict, path: str | Path, *, cancel_event=None) -> None:
    """Write an offline report with bounded display geometry and inline JS.

    No server, account, CDN, or Internet connection is needed to open the file.
    Small scenes retain every original triangle. Larger scenes share bounded,
    topology-checked approximate display meshes. Explicit bounds are the
    fallback when safe simplification cannot fit; inspection data stays raw.
    """
    from plotly.offline import get_plotlyjs

    traces = []
    skipped = []
    previews = []
    records = {item["index"]: item for item in report.get("objects", []) if "index" in item}
    entries, allocations = _preview_plan(objects, report)
    for index, raw_vertices, raw_faces, drawable in entries:
        obj = objects[index]
        if not drawable:
            skipped.append({"index": index, "name": obj.name})
            continue
        label = html.escape(f"[{index}] {obj.name}", quote=True)
        face_budget, vertex_budget = allocations[index]
        vertices, faces = raw_vertices, raw_faces
        mode = "exact"
        reason = None
        approximation = None
        if len(raw_faces) > face_budget or len(raw_vertices) > vertex_budget:
            mode = "approximate"
            try:
                from .display_preview import MAX_INPUT_FACES, MAX_INPUT_VERTICES, MAX_INPUT_COPY_BYTES, simplify
                if (len(raw_faces) > MAX_INPUT_FACES or len(raw_vertices) > MAX_INPUT_VERTICES
                        or 2 * (len(raw_vertices) * 24 + len(raw_faces) * 32) > MAX_INPUT_COPY_BYTES):
                    raise PreviewUnavailable("source_memory_limit")
                if cancel_event is not None and cancel_event.is_set():
                    raise PreviewUnavailable("cancelled")
                if not face_budget or not vertex_budget:
                    raise PreviewUnavailable("display_budget")
                if _checked_bounds(raw_vertices, raw_faces) is None:
                    raise PreviewUnavailable("invalid_source_geometry")
                vertices, faces, approximation = simplify(raw_vertices, raw_faces, face_budget, vertex_budget, cancel_event=cancel_event)
                # Recheck the returned arrays independently of the VTK adapter.
                if len(vertices) > vertex_budget or len(faces) > face_budget or _checked_bounds(vertices, faces) is None:
                    raise PreviewUnavailable("invalid_simplified_geometry")
            except Exception as exc:
                mode = "bounds"
                reason = str(exc) if isinstance(exc, PreviewUnavailable) else "simplification_failed"
        if mode == "bounds":
            bounds = _report_bounds(records.get(index))
            if bounds is None:
                try:
                    bounds = _checked_bounds(raw_vertices, raw_faces)
                except Exception:
                    bounds = None
            if bounds is None:
                skipped.append({"index": index, "name": obj.name})
                continue
            traces.append(_box_trace(bounds, index, label, len(raw_faces)))
            previews.append({"index": index, "name": obj.name, "mode": "bounds",
                             "reason": reason, "original_faces": len(raw_faces),
                             "original_vertices": len(raw_vertices), "displayed_faces": 0,
                             "displayed_vertices": 0,
                             "bounds": bounds})
            continue
        vertices = np.asarray(vertices)
        faces = np.asarray(faces)
        bounds = _checked_bounds(vertices, faces)
        if bounds is None:
            skipped.append({"index": index, "name": obj.name})
            continue
        traces.append(
            {
                "type": "mesh3d",
                "name": label,
                "meta": {"object_index": index, "preview_kind": mode, "bounds": bounds},
                "x": vertices[:, 0].tolist(),
                "y": vertices[:, 1].tolist(),
                "z": vertices[:, 2].tolist(),
                "i": faces[:, 0].tolist(),
                "j": faces[:, 1].tolist(),
                "k": faces[:, 2].tolist(),
                "flatshading": True,
                "colorbar": {"tickformat": ".2f"},
                "showlegend": False,
                "hovertemplate": label + ("<br>근사 미리보기 · 검사는 원본 형상 사용" if mode == "approximate" else "<br>원본 삼각형 표시")
                    + "<br>X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z %{z:.2f} mm<extra></extra>",
                "lighting": {"ambient": 0.65, "diffuse": 0.75, "specular": 0.12},
            }
        )
        previews.append({"index": index, "name": obj.name, "mode": mode,
                         "method": approximation["method"] if mode == "approximate" else "original",
                         "approximation": approximation,
                         "original_faces": len(raw_faces), "original_vertices": len(raw_vertices),
                         "displayed_faces": len(faces), "displayed_vertices": len(vertices), "bounds": bounds})
    preview = {"objects": previews, "uses_bounds_proxies": any(item["mode"] == "bounds" for item in previews),
               "uses_approximate_meshes": any(item["mode"] == "approximate" for item in previews),
               "exact_faces": sum(item["displayed_faces"] for item in previews if item["mode"] == "exact"),
               "exact_vertices": sum(item["displayed_vertices"] for item in previews if item["mode"] == "exact"),
               "displayed_faces": sum(item["displayed_faces"] for item in previews),
               "displayed_vertices": sum(item["displayed_vertices"] for item in previews),
               "max_displayed_faces": MAX_EXACT_FACES, "max_displayed_vertices": MAX_EXACT_VERTICES,
               "max_exact_faces": MAX_EXACT_FACES, "max_exact_vertices": MAX_EXACT_VERTICES}
    payload = {"traces": traces, "report": report, "skipped": skipped,
               "object_count": len(objects), "preview": preview}
    # Bundle exactly the Plotly version installed with this application.
    page = _HTML.replace("__PLOTLY_BUNDLE__", get_plotlyjs()).replace("__VIEWER_PAYLOAD__", _safe_json(payload))
    Path(path).write_text(page, encoding="utf-8")


_HTML = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chitu 3D 간격 검사</title>
<style>
:root{color-scheme:light;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#172534;background:#eef2f6}
*{box-sizing:border-box}body{margin:0}main{display:grid;grid-template-columns:350px minmax(0,1fr);height:100vh}
aside{background:#fff;border-right:1px solid #d9e1e8;padding:24px;overflow:auto;z-index:1}h1{font-size:21px;margin:0 0 5px;letter-spacing:-.6px}
.subtle{font-size:13px;color:#66788a;line-height:1.6}#summary{white-space:pre-line}.card{border:1px solid #d9e1e8;border-radius:10px;padding:14px;margin:20px 0;background:#f8fafc}
.status{font-weight:750;font-size:19px;margin:0 0 6px}.bad{color:#c02b3e}.good{color:#08745b}.unknown{color:#946200}
label{display:block;font-weight:650;font-size:14px;margin:20px 0 8px}select,button{font:inherit}select{width:100%;padding:10px 8px;border:1px solid #c6d1dc;border-radius:6px;background:#fff;font-size:13px}
button{border:1px solid #c6d1dc;border-radius:6px;background:#fff;color:#223b51;padding:9px 12px;cursor:pointer;font-size:13px}button:hover{background:#f0f5fa}button:disabled{opacity:.45;cursor:default}
.buttons{display:flex;gap:8px;margin:12px 0}.details{font-size:13px;line-height:1.7;overflow-wrap:anywhere;margin:14px 0;white-space:pre-line}.checkbox{font-weight:400;font-size:13px;display:flex;gap:8px;align-items:center;margin:14px 0}
.legend{display:flex;gap:13px;flex-wrap:wrap;font-size:12px;color:#4f6375;margin:18px 0}.dot{display:inline-block;border-radius:50%;width:9px;height:9px;margin-right:5px}.warning{font-size:12px;color:#775000;background:#fff8df;border:1px solid #efdfa6;padding:10px;border-radius:6px;white-space:pre-line;overflow-wrap:anywhere}
.preview-note{font-size:12px;color:#315d7c;background:#edf6fc;border:1px solid #c6deef;padding:10px;border-radius:6px;line-height:1.6;white-space:pre-line}
#view{min-width:0;position:relative}#plot{width:100%;height:100%}.hint{position:absolute;bottom:18px;left:20px;pointer-events:none;background:#ffffffdc;padding:7px 10px;border-radius:5px;font-size:12px;color:#577086}
@media(max-width:760px){main{grid-template-columns:1fr;grid-template-rows:auto 65vh;height:auto}aside{border-right:0;border-bottom:1px solid #d9e1e8;padding:18px}#view{min-height:400px}.card{margin:14px 0}}
</style>
<script>__PLOTLY_BUNDLE__</script>
</head>
<body>
<main>
<aside>
<h1 id="heading">Chitu 3D 간격 검사</h1>
<div id="scope" class="subtle">원본 배치 좌표 · 단위 mm · 표면 접촉 금지</div>
<section class="card"><div id="status" class="status"></div><div id="summary" class="subtle"></div></section>
<label for="pair">오브젝트 쌍 · 검사 결과</label>
<select id="pair"><option value="">전체 배치</option></select>
<div class="buttons"><button id="full">전체 보기</button><button id="focus" disabled>선택 쌍 확대</button></div>
<label class="checkbox"><input type="checkbox" id="background" checked> 다른 오브젝트도 표시</label>
<div class="legend"><span><i class="dot" style="background:#e63950"></i>오브젝트 A</span><span><i class="dot" style="background:#f99b2e"></i>오브젝트 B</span><span><i class="dot" style="background:#9caec0"></i>나머지</span><span><i class="dot" style="background:#9b6acc"></i><span id="unknown-legend">미판정</span></span></div>
<div id="preview-note" class="preview-note" hidden></div>
<div id="details" class="details"></div>
<div id="warning" class="warning" hidden></div>
<p class="subtle">쌍을 선택하면 두 오브젝트를 강조합니다. 초록 선은 원본 표면에서 계산한 최근접점입니다. 색상은 오브젝트 전체를 강조합니다.</p>
</aside>
<section id="view"><div id="plot"></div><div class="hint">드래그: 회전 · 스크롤: 확대/축소 · 오른쪽 드래그: 이동</div></section>
</main>
<script id="viewer-data" type="application/json">__VIEWER_PAYLOAD__</script>
<script>
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("viewer-data").textContent);
  const report = data.report;
  const surfaceOnly=report.inspection_scope==="surface-clearance";
  const scopeText="삼각형 표면 간격 검사; 부피 겹침·완전 내부 포함은 검사하지 않음";
  const displayNote="수치는 소수 둘째 자리로 표시하며, 판정은 반올림 전 거리로 합니다.";
  if(surfaceOnly){document.getElementById("heading").textContent="Chitu 표면 간격 검사";document.getElementById("scope").textContent=scopeText+" · 원본 배치 좌표 · 단위 mm\n"+displayNote;document.getElementById("unknown-legend").textContent="거리 미판정";}
  const violations = report.violations || [];
  const pairChoices=[...violations];
  const pairKeys=new Set(pairChoices.map(pair=>`${pair.index_a}:${pair.index_b}`));
  (report.pairs || []).forEach(pair=>{const key=`${pair.index_a}:${pair.index_b}`;if(!pairKeys.has(key)){pairKeys.add(key);pairChoices.push(pair);}});
  const objects = report.objects || [];
  const labels = surfaceOnly ? {contact_or_intersection:"FAIL · 표면 접촉/교차",too_close:"FAIL · 표면 간격 미달",surface_gap_satisfied:"표면 간격 조건 충족",inconclusive:"거리 미판정"} : {overlap:"부피 겹침",touching:"표면 접촉",too_close:"최소 간격 미달",clear:"합격",inconclusive:"판정 불가"};
  const selector = document.getElementById("pair");
  const plot = document.getElementById("plot");
  const focus = document.getElementById("focus");
  const background = document.getElementById("background");
  const failed = new Set(violations.flatMap(pair => [pair.index_a,pair.index_b]));
  const invalid = new Set(objects.filter(obj => surfaceOnly ? obj.query_available===false : !obj.valid).map(obj => obj.index));
  const format = value => {
    if(!Number.isFinite(value)) return "판정 불가";
    if(value!==0 && (Number(value.toFixed(2))===0 || Math.abs(value)>=1e21)) return value.toExponential(2);
    const rendered=value.toFixed(2);return Number(rendered)===0 ? "0.00" : rendered;
  };
  function formatLowerBound(value){
    if(!Number.isFinite(value)) return "판정 불가";
    // Floor the actual IEEE-754 value with integer arithmetic. Multiplying a
    // float by 100 can round upward at the exact boundary of a guarantee.
    const view=new DataView(new ArrayBuffer(8));view.setFloat64(0,value,false);
    const hi=view.getUint32(0,false),lo=view.getUint32(4,false),exponent=(hi>>>20)&2047;
    let mantissa=(BigInt(hi&1048575)<<32n)|BigInt(lo);
    if(exponent) mantissa+=1n<<52n;
    const power=exponent ? exponent-1075 : -1074;
    let numerator=mantissa*100n,denominator=1n;
    if(power>=0) numerator<<=BigInt(power);else denominator<<=BigInt(-power);
    if(hi>>>31) numerator=-numerator;
    let units=numerator/denominator;
    if(numerator<0n && numerator%denominator!==0n) units-=1n;
    const sign=units<0n ? "-" : "",magnitude=units<0n ? -units : units;
    return `${sign}${magnitude/100n}.${(magnitude%100n).toString().padStart(2,"0")}`;
  }
  const gapText = pair => Number.isFinite(pair.distance_mm) ? `${format(pair.distance_mm)} mm` : !surfaceOnly && Number.isFinite(pair.distance_lower_bound_mm) ? `≥ ${formatLowerBound(pair.distance_lower_bound_mm)} mm (보장 하한)` : surfaceOnly ? "거리 미판정" : "판정 불가";
  const placementClear = !surfaceOnly && report.placement_status==="clear" && report.placement_complete===true && !report.cancelled && report.status!=="violation" && !violations.length;
  const placementText = pair => {
    if(surfaceOnly) return pair.aabb_relation==="overlapping" && Number.isFinite(pair.distance_mm) && pair.distance_mm>0 ? "내부 포함 여부 미검사" : "";
    if(!pair.placement_status) return "";
    if(pair.placement_status==="clear" && !["overlap","touching","too_close"].includes(pair.status)){
      const bound=pair.placement_distance_lower_bound_mm;
      return "배치 분리 확인" + (!Number.isFinite(pair.distance_mm) && Number.isFinite(bound) ? ` · ≥ ${formatLowerBound(bound)} mm (보장 하한)` : "") + (pair.status==="inconclusive" ? " · 형상 미검증" : "");
    }
    return pair.placement_status==="violation" ? "배치 간격 조건 미달" : "배치 분리 판정 불가";
  };
  const previewObjects=(data.preview && data.preview.objects) || [];
  const proxies=previewObjects.filter(obj=>obj.mode==="bounds");
  const approximations=previewObjects.filter(obj=>obj.mode==="approximate");
  const previewMessages=[];
  if(approximations.length) previewMessages.push(`근사 미리보기: ${approximations.length}개 오브젝트의 표시용 삼각형을 줄였습니다. 거리 계산과 판정에는 이 단순화 형상을 사용하지 않습니다.`);
  if(proxies.length) previewMessages.push(`대용량 미리보기: ${proxies.length}개 오브젝트는 외곽박스로 표시합니다. 안전한 형상 단순화가 불가능하거나 표시 한도·중단 조건에 해당합니다.`);
  if(report.pair_distances_complete===false) previewMessages.push(surfaceOnly ? "일부 오브젝트 쌍의 실제 표면 거리가 미판정입니다." : "일부 분리된 쌍은 보장된 거리 하한(≥)으로 표시합니다.");
  if(previewMessages.length){const note=document.getElementById("preview-note");note.hidden=false;note.textContent=previewMessages.join("\n");}
  const status = document.getElementById("status");
  if(surfaceOnly){
    if(report.status==="surface_gap_satisfied" && report.complete && !report.cancelled){status.textContent="표면 간격 조건 충족";status.classList.add("good");}
    else if(report.status==="violation"){status.textContent="FAIL · 표면 간격 위반"+(report.complete?"":" · 검사 미완료");status.classList.add("bad");}
    else{status.textContent=report.cancelled?"중단 · 표면 거리 검사 미완료":"표면 거리 미판정";status.classList.add("unknown");}
  } else if(report.status === "violation"){
    status.textContent = "불합격" + (report.complete ? "" : " · 검사 미완료"); status.classList.add("bad");
  } else if(report.status === "clear" && report.complete){
    status.textContent = "합격"; status.classList.add("good");
  } else {status.textContent = "판정 불가";status.classList.add("unknown");}
  document.getElementById("summary").textContent = `오브젝트 ${data.object_count}개 · ${surfaceOnly?"표면 간격 위반":"불합격"} ${violations.length}쌍\n요구 최소 간격 ${format(report.min_gap_mm || 0)} mm` + (surfaceOnly ? `\n실제 거리 측정 ${pairChoices.filter(pair=>Number.isFinite(pair.distance_mm)).length}/${report.total_pair_count ?? pairChoices.length}쌍` : placementClear ? "\n배치 분리 확인" + (report.complete && report.status==="clear" ? "" : " · 형상 미검증") : report.placement_status ? "\n배치 분리: " + (report.placement_status==="violation" ? "간격 조건 미달" : "판정 불가") : "");
  pairChoices.forEach((pair, index) => {
    const option=document.createElement("option"); option.value=String(index);
    option.textContent=`[${pair.index_a}] ↔ [${pair.index_b}] ${labels[pair.status] || pair.status}` + (!surfaceOnly && pair.placement_status==="clear" && pair.status==="inconclusive" ? " · 배치 분리 확인" : "");
    selector.appendChild(option);
  });
  if(Array.isArray(report.selected_pair)){
    const initial=pairChoices.findIndex(pair => pair.index_a===report.selected_pair[0] && pair.index_b===report.selected_pair[1]);
    if(initial>=0) selector.value=String(initial);
  }
  selector.disabled = !pairChoices.length;
  const warnings=[];
  if(!report.complete) warnings.push(surfaceOnly ? "일부 표면 거리를 측정하지 못했습니다. 미판정 오브젝트와 쌍을 확인하세요." : "검사하지 못한 항목이 있습니다. 이 화면은 전체 배치의 합격을 보증하지 않습니다.");
  if(report.error) warnings.push(report.error);
  if(surfaceOnly){objects.filter(obj=>obj.query_available===false && obj.query_error).forEach(obj=>warnings.push(`[${obj.index}] ${obj.name}: 표면 거리 계산 불가 · ${obj.query_error}`));}
  else{
    objects.filter(obj => !obj.valid).forEach(obj => warnings.push(`[${obj.index}] ${obj.name}: ${(obj.errors || []).join(" / ")}`));
    objects.filter(obj => obj.normalization).forEach(obj => warnings.push(`[${obj.index}] ${obj.name}: 원본 형상을 유지한 검사용 연결면 정리 완료 (정점 좌표 변경 없음).`));
  }
  if(data.skipped.length) warnings.push("표시할 수 없는 메쉬: " + data.skipped.map(obj => `[${obj.index}] ${obj.name}`).join(", "));
  if(warnings.length){const warning=document.getElementById("warning");warning.hidden=false;warning.textContent=warnings.join("\n\n");}
  const traces = data.traces;
  const meshCount=traces.length;
  traces.push({type:"scatter3d",mode:"lines+markers",x:[],y:[],z:[],name:"최근접점",line:{color:"#039c80",width:7},marker:{color:"#039c80",size:4},showlegend:false,hovertemplate:"최근접점<br>X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z %{z:.2f} mm<extra></extra>"});
  const layout={margin:{l:0,r:0,t:0,b:0},paper_bgcolor:"#eef2f6",showlegend:false,uirevision:"chitu-placement",scene:{aspectmode:"data",bgcolor:"#eef2f6",camera:{eye:{x:1.5,y:-1.7,z:1.3},up:{x:0,y:0,z:1}},xaxis:{title:{text:"X (mm)"},tickformat:".2f",hoverformat:".2f",gridcolor:"#d2dce6",zerolinecolor:"#99aebe"},yaxis:{title:{text:"Y (mm)"},tickformat:".2f",hoverformat:".2f",gridcolor:"#d2dce6",zerolinecolor:"#99aebe"},zaxis:{title:{text:"Z (mm)"},tickformat:".2f",hoverformat:".2f",gridcolor:"#d2dce6",zerolinecolor:"#99aebe"}}};
  const selectedPair=()=>selector.value === "" ? null : pairChoices[Number(selector.value)];
  function refresh(){
    const pair=selectedPair();focus.disabled=!pair;
    const colors=[], opacities=[], visible=[];
    for(let n=0;n<meshCount;n++){
      const index=traces[n].meta.object_index;
      const chosen=pair && (index===pair.index_a || index===pair.index_b);
      colors.push(pair ? (index===pair.index_a ? "#e63950" : index===pair.index_b ? "#f99b2e" : invalid.has(index) ? "#9b6acc" : "#9caec0") : (failed.has(index)?"#e63950":invalid.has(index)?"#9b6acc":"#9caec0"));
      opacities.push(pair ? (chosen ? .82 : .12) : failed.has(index) ? .82 : .52);
      visible.push(!pair || chosen || background.checked);
    }
    for(const type of ["mesh3d","scatter3d"]){
      const indices=Array.from({length:meshCount},(_,n)=>n).filter(n=>traces[n].type===type);
      if(indices.length){
        const update={opacity:indices.map(n=>opacities[n]),visible:indices.map(n=>visible[n])};
        update[type==="mesh3d"?"color":"line.color"]=indices.map(n=>colors[n]);
        Plotly.restyle(plot,update,indices);
      }
    }
    const points=pair && pair.status!=="overlap" && pair.nearest_points_mm;
    const validPoints=Array.isArray(points) && points.length===2 && points.every(point=>Array.isArray(point) && point.length===3 && point.every(Number.isFinite));
    Plotly.restyle(plot,{x:[validPoints?points.map(p=>p[0]):[]],y:[validPoints?points.map(p=>p[1]):[]],z:[validPoints?points.map(p=>p[2]):[]]},[meshCount]);
    const proxyDescription=pair ? previewObjects.filter(obj=>(obj.index===pair.index_a || obj.index===pair.index_b) && obj.mode!=="exact").map(obj=>`[${obj.index}] 표시: ${obj.mode==="approximate" ? "근사 미리보기" : "외곽박스"} (원본 ${obj.original_faces.toLocaleString("ko-KR")}개 삼각형${obj.mode==="approximate" ? " → "+obj.displayed_faces.toLocaleString("ko-KR")+"개" : ""})`).join("\n") : "";
    document.getElementById("details").textContent=pair ? `[${pair.index_a}] ${pair.object_a}\n[${pair.index_b}] ${pair.object_b}\n\n${surfaceOnly?"표면 간격 판정":"형상 판정"}: ${labels[pair.status] || pair.status}\n간격: ${gapText(pair)}${placementText(pair)?"\n"+placementText(pair):""}${surfaceOnly && pair.error?"\n거리 계산 오류: "+pair.error:""}${!surfaceOnly && Number.isFinite(pair.intersection_volume)?"\n교차 부피: "+format(pair.intersection_volume)+" mm³":""}${proxyDescription?"\n\n"+proxyDescription:""}` : surfaceOnly ? report.status==="surface_gap_satisfied" && report.complete ? "측정한 모든 쌍의 표면 간격 조건을 충족했습니다. 목록에서 각 쌍의 실제 간격을 확인하세요." : "목록에서 오브젝트 쌍을 선택해 실제 표면 간격과 미판정 원인을 확인하세요." : violations.length ? "목록에서 불합격 쌍을 선택하세요. 전체 보기에서 빨간색은 불합격에 포함된 오브젝트입니다." : report.complete && report.status==="clear" ? "검사한 모든 오브젝트 쌍이 간격 조건을 충족했습니다." : placementClear ? "배치의 간격 조건은 분리 확인되었습니다. 형상 유효성은 미검증이므로 전체 형상 합격을 뜻하지 않습니다." : "확인된 불합격 쌍은 없지만, 판정하지 못한 항목을 확인해야 합니다.";
  }
  function fit(pair){
    const chosen=data.traces.slice(0,meshCount).filter(trace=>!pair || trace.meta.object_index===pair.index_a || trace.meta.object_index===pair.index_b);
    const ranges={};
    for(const axis of ["x","y","z"]){
      let lo=Infinity,hi=-Infinity;
      chosen.forEach(trace=>trace[axis].forEach(value=>{if(Number.isFinite(value)){lo=Math.min(lo,value);hi=Math.max(hi,value);}}));
      if(Number.isFinite(lo) && Number.isFinite(hi)){const pad=Math.max((hi-lo)*.12,.2);ranges[`scene.${axis}axis.range`]=[lo-pad,hi+pad];}
    }
    Plotly.relayout(plot,ranges);
  }
  Plotly.newPlot(plot,traces,layout,{responsive:true,displaylogo:false,scrollZoom:true}).then(()=>{refresh();fit(selectedPair());});
  selector.addEventListener("change",refresh);
  background.addEventListener("change",refresh);
  focus.addEventListener("click",()=>fit(selectedPair()));
  document.getElementById("full").addEventListener("click",()=>{selector.value="";refresh();fit(null);});
})();
</script>
</body>
</html>
'''
