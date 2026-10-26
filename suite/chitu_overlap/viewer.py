"""Standalone, offline 3D inspection of the placed meshes and failed pairs."""

from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np

from .geometry import MeshObject


# Bound both arrays: a triangle budget alone would still allow millions of
# unused vertices to become Python floats during JSON serialization.
MAX_EXACT_FACES = 200_000
MAX_EXACT_VERTICES = 120_000
_CHUNK_ROWS = 65_536


def _shape_ok(values) -> bool:
    return getattr(values, "ndim", 0) == 2 and tuple(values.shape[1:]) == (3,)


def _report_bounds(record) -> list | None:
    if record is None or record.get("bounds") is None:
        return None
    bounds = np.asarray(record["bounds"], dtype=float)
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
                         + "<br>X %{x:.4f} mm<br>Y %{y:.4f} mm<br>Z %{z:.4f} mm<extra></extra>",
    }


def _preview_plan(objects, report):
    """Reserve exact geometry for selected/failed small objects first."""
    selected = set(report.get("selected_pair") or ())
    failed = {pair[key] for pair in report.get("violations", [])
              for key in ("index_a", "index_b")}
    entries = []
    for index, obj in enumerate(objects):
        vertices, faces = obj.mesh.vertices, obj.mesh.faces
        drawable = _shape_ok(vertices) and _shape_ok(faces) and bool(len(vertices)) and bool(len(faces))
        entries.append((index, vertices, faces, drawable))
    remaining_faces, remaining_vertices = MAX_EXACT_FACES, MAX_EXACT_VERTICES
    priorities = sorted((entry for entry in entries if entry[3]),
                        key=lambda item: (0 if item[0] in selected else 1 if item[0] in failed else 2,
                                          len(item[2]), len(item[1]), item[0]))
    exact = set()
    for index, vertices, faces, _ in priorities:
        if len(faces) <= remaining_faces and len(vertices) <= remaining_vertices:
            exact.add(index)
            remaining_faces -= len(faces)
            remaining_vertices -= len(vertices)
    return entries, exact


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


def write_html_report(objects: list[MeshObject], report: dict, path: str | Path) -> None:
    """Write an offline report with bounded display geometry and inline JS.

    No server, account, CDN, or Internet connection is needed to open the file.
    Small scenes retain every original triangle. Over-budget objects use
    explicitly labeled wire bounds; inspection data and source meshes remain
    unchanged. Bounds from the inspection avoid scanning giant arrays again.
    """
    from plotly.offline import get_plotlyjs

    traces = []
    skipped = []
    previews = []
    records = {item["index"]: item for item in report.get("objects", []) if "index" in item}
    entries, exact = _preview_plan(objects, report)
    for index, raw_vertices, raw_faces, drawable in entries:
        obj = objects[index]
        if not drawable:
            skipped.append({"index": index, "name": obj.name})
            continue
        label = html.escape(f"[{index}] {obj.name}", quote=True)
        if index not in exact:
            bounds = _report_bounds(records.get(index))
            if bounds is None:
                bounds = _checked_bounds(raw_vertices, raw_faces)
            if bounds is None:
                skipped.append({"index": index, "name": obj.name})
                continue
            traces.append(_box_trace(bounds, index, label, len(raw_faces)))
            previews.append({"index": index, "name": obj.name, "mode": "bounds",
                             "reason": "display_budget", "original_faces": len(raw_faces),
                             "original_vertices": len(raw_vertices), "displayed_faces": 0,
                             "bounds": bounds})
            continue
        vertices = np.asarray(raw_vertices)
        faces = np.asarray(raw_faces)
        bounds = _checked_bounds(vertices, faces)
        if bounds is None:
            skipped.append({"index": index, "name": obj.name})
            continue
        traces.append(
            {
                "type": "mesh3d",
                "name": label,
                "meta": {"object_index": index, "preview_kind": "exact", "bounds": bounds},
                "x": vertices[:, 0].tolist(),
                "y": vertices[:, 1].tolist(),
                "z": vertices[:, 2].tolist(),
                "i": faces[:, 0].tolist(),
                "j": faces[:, 1].tolist(),
                "k": faces[:, 2].tolist(),
                "flatshading": True,
                "showlegend": False,
                "hovertemplate": label + "<br>X %{x:.4f} mm<br>Y %{y:.4f} mm<br>Z %{z:.4f} mm<extra></extra>",
                "lighting": {"ambient": 0.65, "diffuse": 0.75, "specular": 0.12},
            }
        )
        previews.append({"index": index, "name": obj.name, "mode": "exact",
                         "original_faces": len(faces), "original_vertices": len(vertices),
                         "displayed_faces": len(faces), "bounds": bounds})
    preview = {"objects": previews, "uses_bounds_proxies": any(item["mode"] == "bounds" for item in previews),
               "exact_faces": sum(item["displayed_faces"] for item in previews),
               "exact_vertices": sum(item["original_vertices"] for item in previews if item["mode"] == "exact"),
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
<h1>Chitu 3D 간격 검사</h1>
<div class="subtle">원본 배치 좌표 · 단위 mm · 표면 접촉 금지</div>
<section class="card"><div id="status" class="status"></div><div id="summary" class="subtle"></div></section>
<label for="pair">불합격 오브젝트 쌍</label>
<select id="pair"><option value="">전체 배치</option></select>
<div class="buttons"><button id="full">전체 보기</button><button id="focus" disabled>선택 쌍 확대</button></div>
<label class="checkbox"><input type="checkbox" id="background" checked> 다른 오브젝트도 표시</label>
<div class="legend"><span><i class="dot" style="background:#e63950"></i>오브젝트 A</span><span><i class="dot" style="background:#f99b2e"></i>오브젝트 B</span><span><i class="dot" style="background:#9caec0"></i>나머지</span><span><i class="dot" style="background:#9b6acc"></i>미판정</span></div>
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
  const violations = report.violations || [];
  const objects = report.objects || [];
  const labels = {overlap:"부피 겹침",touching:"표면 접촉",too_close:"최소 간격 미달",clear:"합격",inconclusive:"판정 불가"};
  const selector = document.getElementById("pair");
  const plot = document.getElementById("plot");
  const focus = document.getElementById("focus");
  const background = document.getElementById("background");
  const failed = new Set(violations.flatMap(pair => [pair.index_a,pair.index_b]));
  const invalid = new Set(objects.filter(obj => !obj.valid).map(obj => obj.index));
  const format = value => Number.isFinite(value) ? value.toLocaleString("ko-KR", {maximumFractionDigits:8}) : "판정 불가";
  const gapText = pair => Number.isFinite(pair.distance_mm) ? `${format(pair.distance_mm)} mm` : Number.isFinite(pair.distance_lower_bound_mm) ? `≥ ${format(pair.distance_lower_bound_mm)} mm (보장 하한)` : "판정 불가";
  const previewObjects=(data.preview && data.preview.objects) || [];
  const proxies=previewObjects.filter(obj=>obj.mode==="bounds");
  const previewMessages=[];
  if(proxies.length) previewMessages.push(`대용량 미리보기: ${proxies.length}개 오브젝트는 외곽박스로 표시합니다. 거리와 판정은 원본 형상으로 계산했습니다.`);
  if(report.pair_distances_complete===false) previewMessages.push("일부 분리된 쌍은 보장된 거리 하한(≥)으로 표시합니다.");
  if(previewMessages.length){const note=document.getElementById("preview-note");note.hidden=false;note.textContent=previewMessages.join("\n");}
  const status = document.getElementById("status");
  if(report.status === "violation"){
    status.textContent = "불합격" + (report.complete ? "" : " · 검사 미완료"); status.classList.add("bad");
  } else if(report.status === "clear" && report.complete){
    status.textContent = "합격"; status.classList.add("good");
  } else {status.textContent = "판정 불가";status.classList.add("unknown");}
  document.getElementById("summary").textContent = `오브젝트 ${data.object_count}개 · 불합격 ${violations.length}쌍\n요구 최소 간격 ${format(report.min_gap_mm || 0)} mm`;
  violations.forEach((pair, index) => {
    const option=document.createElement("option"); option.value=String(index);
    option.textContent=`[${pair.index_a}] ↔ [${pair.index_b}] ${labels[pair.status] || pair.status}`;
    selector.appendChild(option);
  });
  if(Array.isArray(report.selected_pair)){
    const initial=violations.findIndex(pair => pair.index_a===report.selected_pair[0] && pair.index_b===report.selected_pair[1]);
    if(initial>=0) selector.value=String(initial);
  }
  selector.disabled = !violations.length;
  const warnings=[];
  if(!report.complete) warnings.push("검사하지 못한 항목이 있습니다. 이 화면은 전체 배치의 합격을 보증하지 않습니다.");
  if(report.error) warnings.push(report.error);
  objects.filter(obj => !obj.valid).forEach(obj => warnings.push(`[${obj.index}] ${obj.name}: ${(obj.errors || []).join(" / ")}`));
  objects.filter(obj => obj.normalization).forEach(obj => warnings.push(`[${obj.index}] ${obj.name}: 원본 형상을 유지한 검사용 연결면 정리 완료 (정점 좌표 변경 없음).`));
  if(data.skipped.length) warnings.push("표시할 수 없는 메쉬: " + data.skipped.map(obj => `[${obj.index}] ${obj.name}`).join(", "));
  if(warnings.length){const warning=document.getElementById("warning");warning.hidden=false;warning.textContent=warnings.join("\n\n");}
  const traces = data.traces;
  const meshCount=traces.length;
  traces.push({type:"scatter3d",mode:"lines+markers",x:[],y:[],z:[],name:"최근접점",line:{color:"#039c80",width:7},marker:{color:"#039c80",size:4},showlegend:false,hovertemplate:"최근접점<br>X %{x:.5f} mm<br>Y %{y:.5f} mm<br>Z %{z:.5f} mm<extra></extra>"});
  const layout={margin:{l:0,r:0,t:0,b:0},paper_bgcolor:"#eef2f6",showlegend:false,uirevision:"chitu-placement",scene:{aspectmode:"data",bgcolor:"#eef2f6",camera:{eye:{x:1.5,y:-1.7,z:1.3},up:{x:0,y:0,z:1}},xaxis:{title:{text:"X (mm)"},gridcolor:"#d2dce6",zerolinecolor:"#99aebe"},yaxis:{title:{text:"Y (mm)"},gridcolor:"#d2dce6",zerolinecolor:"#99aebe"},zaxis:{title:{text:"Z (mm)"},gridcolor:"#d2dce6",zerolinecolor:"#99aebe"}}};
  const selectedPair=()=>selector.value === "" ? null : violations[Number(selector.value)];
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
    const proxyDescription=pair ? proxies.filter(obj=>obj.index===pair.index_a || obj.index===pair.index_b).map(obj=>`[${obj.index}] 표시: 외곽박스 (원본 ${obj.original_faces.toLocaleString("ko-KR")}개 삼각형)`).join("\n") : "";
    document.getElementById("details").textContent=pair ? `[${pair.index_a}] ${pair.object_a}\n[${pair.index_b}] ${pair.object_b}\n\n판정: ${labels[pair.status] || pair.status}\n간격: ${gapText(pair)}${Number.isFinite(pair.intersection_volume)?"\n교차 부피: "+format(pair.intersection_volume)+" mm³":""}${proxyDescription?"\n\n"+proxyDescription:""}` : violations.length ? "목록에서 불합격 쌍을 선택하세요. 전체 보기에서 빨간색은 불합격에 포함된 오브젝트입니다." : report.complete && report.status==="clear" ? "검사한 모든 오브젝트 쌍이 간격 조건을 충족했습니다." : "확인된 불합격 쌍은 없지만, 판정하지 못한 항목을 확인해야 합니다.";
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
