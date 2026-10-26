"""Explicit presentation translations for the oven simulator.

Only user-facing text enters this module. Numerical keys, CSV column names,
material identifiers, paths and saved input values remain in their original form.
Widgets are updated in place so changing language never rebuilds an input form.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

MODEL_INFO_EN = (
    "Manual\n"
    "  Uses one constant k_eff throughout the bed and temperature range.\n\n"
    "PSD estimate\n"
    "  Uses measured bed density + D10/D50/D90 + temperature-dependent\n"
    "  gas/solid conductivity + a packing/contact interpolation + a small\n"
    "  PSD-informed radiative term. k_eff is evaluated locally for every cell.\n\n"
    "Calibrated\n"
    "  Uses the same PSD model, then fits a multiplicative k_eff scale and\n"
    "  oven convection coefficient h against a center thermocouple history.\n\n"
    "D10/D50/D90 improve the physical model, but measured bed density and\n"
    "thermocouple calibration remain more important than PSD alone."
)


TEXT_KO = {
    MODEL_INFO_EN: (
        "직접 입력\n"
        "  입력한 유효 열전도율을 전체 분말층과 온도 범위에 일정하게 적용합니다.\n\n"
        "입도 기반 추정\n"
        "  실측 충전 밀도, D10·D50·D90, 기체와 고체 열전도율의 온도 변화,\n"
        "  충전·접촉 상태의 보간, 입도로 추정한 작은 복사 항을 사용합니다.\n"
        "  각 격자의 온도에 따라 유효 열전도율을 계산합니다.\n\n"
        "실측 보정\n"
        "  같은 입도 기반 모델을 사용하며, 중심 열전대의 온도 이력에 맞춰\n"
        "  유효 열전도율 배율과 오븐 대류계수 h를 보정합니다.\n\n"
        "D10·D50·D90도 모델에 반영되지만, 실제 충전 밀도를 측정하고\n"
        "중심 열전대 실측으로 보정하는 것이 더 중요합니다."
    ),
    "Oven thermal diffusion simulator": "오븐 열확산 시뮬레이터",
    "Language": "언어",
    "ko": "한국어", "en": "English",
    "Simulation inputs": "시뮬레이션 입력값",
    "입력값 설명서 / Input Guide": "입력값 설명서",
    "Input guide (Korean)": "입력값 설명서",
    "Lx": "X 방향 길이", "Ly": "Y 방향 길이", "Lz": "Z 방향 길이",
    "box k": "벽면 열전도율", "box density": "벽면 밀도", "box cp": "벽면 비열",
    "side thickness": "측벽 두께", "bottom thickness": "바닥판 두께", "bottom gap": "바닥판 아래 공기층 두께",
    "bed density": "충전 밀도", "true density": "진밀도", "cp": "분말 비열",
    "k_eff": "유효 열전도율", "h_conv": "대류 열전달계수 h", "max_dt": "최대 시간 간격",
    "record interval": "결과 기록 간격", "3D snapshot interval": "3차원 결과 저장 간격",
    "top thickness": "상부 덮개 두께", "solid SiC k": "SiC 고체 열전도율",
    "pore gas k": "기공 가스 열전도율", "solid-k temperature exponent": "고체 열전도율의 온도 지수",
    "contact blend": "입자 접촉 혼합계수", "calibration k scale": "보정한 열전도율 배율",
    "calibrated h": "보정한 대류계수 h", "initial temperature": "초기 온도",
    "emissivity": "방사율", "floor emissivity": "오븐 바닥 방사율",
    "bottom view factor": "오븐 바닥과의 형상계수",
    "ambient O2": "외부 산소 농도", "initial pore O2": "기공 내 초기 산소 농도",
    "O2 diffusivity": "산소 확산계수", "O2 temperature exponent": "산소 확산계수의 온도 지수",
    "O2 tortuosity": "산소 확산 굴곡도", "nx": "X 방향 격자 수 nx",
    "ny": "Y 방향 격자 수 ny", "nz": "Z 방향 격자 수 nz",
    "1) Powder-filled region": "1) 분말 충전 영역",
    "2) Build box": "2) 빌드박스",
    "Material": "재질",
    "Wall k [W/mK]": "벽면 열전도율 [W/mK]",
    "Wall density [kg/m³]": "벽면 밀도 [kg/m³]",
    "Wall cp [J/kgK]": "벽면 비열 [J/kgK]",
    "Side thickness [mm]": "측벽 두께 [mm]",
    "Bottom thickness [mm]": "바닥판 두께 [mm]",
    "Top/lid thickness [mm]": "상부 덮개 두께 [mm]",
    "Under-plate gap [mm]": "바닥판 아래 공기층 두께 [mm]",
    "Oven-floor emissivity": "오븐 바닥 방사율",
    "Floor view factor [0–1]": "오븐 바닥과의 형상계수 [0–1]",
    "Top is open to oven air": "상부를 오븐 공기에 개방",
    "Bottom: side-wall metal conduction + floor radiation + natural air-layer convection/conduction. Forced convection is excluded.":
        "바닥판에는 측벽의 금속 열전도, 오븐 바닥의 복사, 공기층의 자연대류·열전도를 적용합니다. 강제대류는 제외합니다.",
    "3) SiC powder bed / k_eff model": "3) SiC 분말층과 유효 열전도율",
    "SiC preset": "SiC 물성 기본값",
    "Bed density [g/cc]": "충전 밀도 [g/cc]",
    "True density [g/cc]": "진밀도 [g/cc]",
    "cp [J/kgK]": "분말 비열 [J/kgK]",
    "Solid SiC k @25°C [W/mK]": "25°C SiC 고체 열전도율 [W/mK]",
    "Pore-gas k @25°C [W/mK]": "25°C 기공 가스 열전도율 [W/mK]",
    "k_eff mode": "유효 열전도율 계산 방식",
    "Solid-k T exponent n": "고체 열전도율의 온도 지수 n",
    "Contact blend [0–1]": "입자 접촉 혼합계수 [0–1]",
    "Manual k_eff [W/mK]": "직접 입력할 유효 열전도율 [W/mK]",
    "Preview k_eff(T)": "온도별 유효 열전도율 확인",
    "Model info": "계산 모델 설명",
    "Porosity: -": "기공률: -",
    "PSD: -": "입도 분포: -",
    "PSD mode uses D10/D50/D90 to estimate breadth and characteristic pore length.\nBed density still carries most packing information; calibration is preferred.":
        "입도 기반 모델은 D10·D50·D90으로 분포 폭과 대표 기공 길이를 추정합니다.\n충전 상태에는 실제 충전 밀도가 더 중요하므로 열전대 실측으로 보정하는 것이 좋습니다.",
    "4) Oven heat transfer": "4) 오븐 열전달",
    "Convection h [W/m²K]": "대류 열전달계수 h [W/m²K]",
    "Effective emissivity": "유효 방사율",
    "Initial bed temp [°C]": "분말층 초기 온도 [°C]",
    "Tip: h and k_eff are the two parameters most worth calibrating\nwith a thermocouple experiment.":
        "대류 열전달계수 h와 유효 열전도율은\n열전대 실측으로 보정할 때 효과가 큰 입력값입니다.",
    "5) O₂ diffusion only": "5) 산소 확산",
    "Enable O₂ diffusion-only simulation": "산소 확산 계산 사용",
    "Ambient O₂ [vol%]": "외부 산소 농도 [vol%]",
    "Initial pore O₂ [vol%]": "기공 내 초기 산소 농도 [vol%]",
    "O₂ D_air @25°C [m²/s]": "25°C 공기 중 산소 확산계수 [m²/s]",
    "D temperature exponent": "확산계수의 온도 지수",
    "Tortuosity model": "굴곡도 계산 방식",
    "Manual tortuosity τ": "직접 입력할 굴곡도 τ",
    "Include PSD-based Knudsen correction": "입도 기반 크누센 확산 보정 적용",
    "O₂ access boundaries": "외부 산소가 유입되는 면",
    "Top": "상부", "Sides": "측면",
    "Bottom (sealed metal plate)": "바닥면(밀폐 금속판)",
    "Preview O₂ diffusivity": "산소 확산계수 확인",
    "Diffusion only: no O₂ consumption/reaction. If Initial O₂ = Ambient O₂, the field stays uniform.":
        "산소의 이동만 계산하며 반응이나 산소 소비는 포함하지 않습니다. 초기 농도가 외부와 같으면 농도는 균일하게 유지됩니다.",
    "6) Center thermocouple calibration (optional)": "6) 중심 열전대 실측 보정(선택)",
    "Load TC CSV": "열전대 CSV 불러오기",
    "Fit k scale + h": "열전도율 배율·대류계수 보정",
    "Calibrated k scale": "보정한 열전도율 배율",
    "Calibrated h [W/m²K]": "보정한 대류계수 h [W/m²K]",
    "No thermocouple CSV loaded.": "불러온 열전대 CSV가 없습니다.",
    "CSV: time_min, center_C (header recommended).": "CSV 열: time_min, center_C (첫 행에 열 이름 권장)",
    "Calibration uses the same 3D grid/solver as the final run; it does not reduce numerical precision. Fitting can therefore take time.":
        "보정에도 본 계산과 같은 3차원 격자와 계산법을 사용합니다. 계산 정밀도를 낮추지 않으므로 시간이 걸릴 수 있습니다.",
    "7) Numerical settings": "7) 수치 계산 설정",
    "Grid nx": "X 방향 격자 수 nx", "Grid ny": "Y 방향 격자 수 ny", "Grid nz": "Z 방향 격자 수 nz",
    "Max Δt [s]": "최대 시간 간격 Δt [s]",
    "Output interval [s]": "결과 기록 간격 [s]",
    "3D snapshot interval [min]": "3차원 결과 저장 간격 [min]",
    "3D snapshot interval changes saved visualization frames only; solver precision is unchanged.":
        "3차원 결과 저장 간격은 화면 표시용 데이터의 양만 바꿉니다. 계산 정밀도에는 영향을 주지 않습니다.",
    "8) Oven profile (absolute elapsed time)": "8) 오븐 온도 이력(시작 후 누적 시간)",
    "Time [min]": "누적 시간 [min]", "Oven [°C]": "오븐 온도 [°C]",
    "Add point": "추가", "Delete": "삭제", "Edit": "수정",
    "Hold example: 120 min / 200°C → 840 min / 200°C": "온도 유지 예: 120분 / 200°C → 840분 / 200°C",
    "Run simulation": "시뮬레이션 실행", "Stop": "중단", "Export CSV": "CSV 저장",
    "Save inputs": "입력값 저장", "Load saved": "저장한 입력값 불러오기", "Reset defaults": "기본값으로 되돌리기",
    "Ready": "준비", "Ready — last saved inputs restored": "저장한 입력값을 불러왔습니다.",
    "Temperature history": "온도 변화", "3D temperature field": "3차원 온도 분포",
    "O₂ history": "산소 농도 변화", "3D O₂ field": "3차원 산소 농도 분포",
    "Elapsed time [h]": "경과 시간 [h]", "Temperature [°C]": "온도 [°C]",
    "O₂ [vol%]": "산소 농도 [vol%]", "Play": "재생", "Pause": "일시정지",
    "Stop playback": "정지",
    "Run a simulation to view the 3D field.": "시뮬레이션을 실행하면 3차원 온도 분포가 표시됩니다.",
    "Run an O₂-enabled simulation to view the field.": "산소 확산 계산을 사용해 실행하면 농도 분포가 표시됩니다.",
    "Drag the plot with the mouse to rotate it. Large grids are decimated only for 3D display speed; the thermal calculation keeps the full requested grid.":
        "그래프를 마우스로 드래그하면 회전합니다. 격자가 많으면 화면 표시만 간소화하며, 열전달 계산에는 지정한 전체 격자를 사용합니다.",
    "O₂ is shown as actual gas-phase-equivalent vol%. This mode includes diffusion only; no binder reaction or O₂ consumption.":
        "산소 농도는 기체의 부피 비율(vol%)로 표시합니다. 확산만 계산하며 바인더 반응과 산소 소비는 포함하지 않습니다.",
    "Carbon steel": "탄소강", "Custom": "사용자 지정", "SiC general": "일반 SiC",
    "Manual": "직접 입력", "PSD estimate": "입도 기반 추정", "Calibrated": "실측 보정",
    "Inputs saved": "입력값 저장", "Save error": "저장 오류", "Load error": "불러오기 오류",
    "Inputs loaded": "입력값 불러오기", "Reset all inputs and the oven profile to the built-in defaults?": "모든 입력값과 오븐 온도 이력을 기본값으로 되돌리시겠습니까?",
    "Built-in defaults restored": "기본값으로 되돌렸습니다.",
    "No previously saved input file exists yet.\nRun a simulation once or press Save inputs first.": "저장된 입력값이 없습니다.\n입력값 저장 버튼을 눌러 먼저 저장해 주세요.",
    "Manual mode: the constant Manual k_eff value is used.": "직접 입력한 유효 열전도율을 전체 분말층에 일정하게 적용합니다.",
    "PSD estimate mode: k_eff varies with local temperature.": "입도 기반 모델로 각 위치의 온도에 따른 유효 열전도율을 계산합니다.",
    "Calibrated mode: PSD k_eff(T) × fitted scale, with calibrated h.": "입도 기반 열전도율에 실측 보정 배율을 곱하고, 보정한 대류계수를 적용합니다.",
    "k_eff(T) preview": "온도별 유효 열전도율", "Predicted k_eff:": "예상 유효 열전도율:",
    "Important: PSD mode is an engineering estimate. Particle-contact thermal": "입도 기반 모델은 공학적 추정입니다. 입자 사이의 접촉 열저항은",
    "resistance cannot be uniquely inferred from D10/D50/D90 alone.": "D10·D50·D90만으로 정확하게 정할 수 없습니다.",
    "Use center-thermocouple calibration when available.": "가능하면 중심 열전대 실측으로 보정해 주세요.",
    "About the k_eff models": "유효 열전도율 계산 모델",
    "Input error": "입력 오류", "CSV error": "CSV 오류", "Calibration": "실측 보정",
    "Load a center thermocouple CSV first.": "중심 열전대 CSV를 먼저 불러와 주세요.",
    "Load center thermocouple CSV": "중심 열전대 CSV 불러오기",
    "CSV files": "CSV 파일", "Text files": "텍스트 파일", "All files": "모든 파일",
    "Fewer than 5 numeric time/temperature rows were found.": "시간과 온도가 숫자로 입력된 행이 5개 이상 필요합니다.",
    "Thermocouple data loaded. Fit uses the currently entered oven profile.": "열전대 데이터를 불러왔습니다. 현재 입력한 오븐 온도 이력으로 보정합니다.",
    "Calibrating k scale + h...": "열전도율 배율과 대류계수 보정 중…",
    "=== O₂ diffusion-only model preview ===": "=== 산소 확산 모델 확인 ===",
    "Effective O₂ diffusivity:": "유효 산소 확산계수:",
    "Initial pore O₂ equals ambient O₂.": "기공 내 초기 산소 농도가 외부 농도와 같습니다.",
    "With diffusion only and no reaction, no O₂ gradient will form.": "산소 소비 반응 없이 확산만 계산하므로 농도 차이가 생기지 않습니다.",
    "O₂ diffusivity preview": "산소 확산계수 확인",
    "Select one profile point.": "수정할 온도 이력의 행을 선택해 주세요.",
    "Oven profile point": "오븐 온도 이력 수정", "Elapsed time [min]": "경과 시간 [min]",
    "Oven temperature [°C]": "오븐 온도 [°C]", "Time must be >= 0.": "시간은 0 이상이어야 합니다.",
    "OK": "확인", "Running...": "계산 중…", "Stopping...": "중단 중…",
    "Completed": "계산 완료", "Stopped / error": "중단 또는 오류", "Simulation error": "계산 오류",
    "Calibration complete": "실측 보정 완료",
    "Oven air": "오븐 공기", "Powder surface avg.": "분말 표면 평균", "Powder volume avg.": "분말층 전체 평균",
    "Powder center": "분말층 중심", "Side-wall avg.": "측벽 평균", "Bottom-plate avg.": "바닥판 평균",
    "Bed min-max": "분말층 최저–최고", "Measured center TC": "중심 열전대 실측",
    "O₂ diffusion was disabled for this run.": "이번 계산에서는 산소 확산을 사용하지 않았습니다.",
    "O₂ center": "중심 산소 농도", "O₂ top-layer avg.": "상부층 평균 산소 농도", "O₂ volume avg.": "분말층 평균 산소 농도",
    "Ambient O₂": "외부 산소 농도", "Bed O₂ min-max": "분말층 산소 농도 최저–최고",
    "=== O₂ diffusion-only summary ===": "=== 산소 확산 결과 ===", "Center ingress times:": "중심부 산소 유입 시간:",
    "Initial O₂ = ambient O₂.": "초기 산소 농도와 외부 산소 농도가 같습니다.",
    "Because this mode has no O₂-consuming reaction, a concentration gradient cannot develop.": "이 모델에는 산소 소비 반응이 없으므로 농도 차이가 생기지 않습니다.",
    "Set Initial pore O₂ below ambient (e.g. 0%) only when you want to study pure ingress from an O₂-depleted initial state.": "산소가 부족한 초기 상태에서의 유입만 살펴볼 때 초기 산소 농도를 외부보다 낮게 설정하세요(예: 0%).",
    "Model limits:": "모델의 적용 범위:",
    "- diffusion only; O₂ consumption = 0": "- 확산만 계산하며 산소 소비는 0입니다.",
    "- no PVP oxidation/crosslinking kinetics": "- PVP 산화·가교 반응 속도는 계산하지 않습니다.",
    "- no pressure-driven gas flow or natural convection through the bed": "- 분말층 내부의 압력 유동과 자연대류는 계산하지 않습니다.",
    "- atmospheric-pressure gas-diffusion assumption": "- 대기압에서의 기체 확산을 가정합니다.",
    "- PSD pore size is an estimate used mainly for Knudsen correction": "- 입도로 추정한 기공 크기는 주로 크누센 확산 보정에 사용합니다.",
    "No O₂ snapshots available for this run.": "이번 계산에는 저장된 3차원 산소 농도 결과가 없습니다.",
    "No 3D snapshots available.": "저장된 3차원 결과가 없습니다.",
    "=== Simulation summary ===": "=== 시뮬레이션 결과 ===",
    "Center-temperature cumulative time above thresholds:": "중심 온도가 기준값 이상인 누적 시간:",
    "Interpretation caution:": "결과 해석 시 유의사항:",
    "- Bottom input includes floor radiation and horizontal air-layer natural convection/conduction.": "- 바닥판에는 오븐 바닥 복사와 수평 공기층의 자연대류·열전도를 적용합니다.",
    "- Oven-floor temperature is assumed to follow the oven profile.": "- 오븐 바닥 온도는 입력한 오븐 온도 이력을 따른다고 가정합니다.",
    "- Side-wall metal conduction into the connected bottom plate remains active.": "- 연결된 측벽에서 바닥판으로 전달되는 금속 열전도를 포함합니다.",
    "- k_eff/contact physics and h_conv still dominate uncertainty without calibration.": "- 실측 보정이 없으면 유효 열전도율·입자 접촉·대류계수가 큰 불확실성으로 남습니다.",
    "- PSD k_eff(T) includes temperature dependence, but it remains an engineering model.": "- 입도 기반 열전도율에는 온도 의존성을 반영하지만 공학적 추정 모델입니다.",
    "- Side/bottom metal thermal mass is resolved; a closed lid remains resistance-only.": "- 측벽과 바닥판의 열용량을 계산하며, 닫힌 덮개에는 열저항만 적용합니다.",
    "- Solvent latent heat, binder reaction heat and detailed oven flow are not resolved.": "- 용매 잠열·바인더 반응열·오븐 내부의 상세 유동은 계산하지 않습니다.",
    "- For production decisions, calibrate against at least one thermocouple run.": "- 공정에 적용하기 전에 열전대 실측을 한 번 이상 수행해 보정하세요.",
    "Export": "CSV 저장", "Run a simulation first.": "시뮬레이션을 먼저 실행해 주세요.",
    "Saved settings are not a valid object.": "저장된 설정 형식이 올바르지 않습니다.",
    "Saved variables section is invalid.": "저장된 입력값 형식이 올바르지 않습니다.",
    "Emissivity must be between 0 and 1.": "방사율은 0~1 사이여야 합니다.",
    "Oven-floor emissivity must be between 0 and 1.": "오븐 바닥 방사율은 0~1 사이여야 합니다.",
    "Bottom radiation view factor must be between 0 and 1.": "바닥 복사의 형상계수는 0~1 사이여야 합니다.",
    "Unknown k_eff mode.": "유효 열전도율 계산 방식을 확인해 주세요.",
    "nx, ny, nz must each be at least 3.": "각 방향의 격자 수 nx·ny·nz는 3 이상이어야 합니다.",
    "For desktop use, keep each grid dimension <= 100.": "각 방향의 격자 수는 100 이하로 입력해 주세요.",
    "Bed density must be lower than SiC true density.": "충전 밀도는 SiC 진밀도보다 작아야 합니다.",
    "Oven profile needs at least two time-temperature points.": "오븐 온도 이력에 시간·온도 입력점이 2개 이상 필요합니다.",
    "The first oven profile time must be 0 min.": "오븐 온도 이력의 첫 시간은 0분이어야 합니다.",
    "Oven profile times must be strictly increasing.": "오븐 온도 이력의 시간은 중복 없이 증가해야 합니다.",
    "At least 5 valid thermocouple points are required.": "유효한 열전대 측정값이 5개 이상 필요합니다.",
    "At least 5 thermocouple points must lie inside the oven profile time range.": "오븐 온도 이력의 시간 범위에 열전대 측정값이 5개 이상 있어야 합니다.",
    "PSD must satisfy 0 < D10 <= D50 <= D90.": "입도는 0 < D10 ≤ D50 ≤ D90 조건을 만족해야 합니다.",
    "Contact blend must be between 0 and 1.": "입자 접촉 혼합계수는 0~1 사이여야 합니다.",
    "Solid-k temperature exponent must be >= 0.": "고체 열전도율의 온도 지수는 0 이상이어야 합니다.",
    "Calibrated k scale and h must both be > 0.": "보정한 열전도율 배율과 대류계수 h는 모두 0보다 커야 합니다.",
    "Ambient O2 must be > 0 and <= 100 vol%.": "외부 산소 농도는 0보다 크고 100 vol% 이하여야 합니다.",
    "Initial pore O2 must be between 0 and 100 vol%.": "기공 내 초기 산소 농도는 0~100 vol% 사이여야 합니다.",
    "O2 diffusion coefficient at 25°C must be > 0.": "25°C 산소 확산계수는 0보다 커야 합니다.",
    "O2 diffusion temperature exponent must be >= 0.": "산소 확산계수의 온도 지수는 0 이상이어야 합니다.",
    "O2 tortuosity mode must be Bruggeman or Manual.": "굴곡도 계산 방식은 Bruggeman 또는 직접 입력 중에서 선택해 주세요.",
    "Manual O2 tortuosity must be >= 1.": "직접 입력하는 산소 확산 굴곡도는 1 이상이어야 합니다.",
    "O2 PSD model requires 0 < D10 <= D50 <= D90.": "산소 확산의 입도 모델은 0 < D10 ≤ D50 ≤ D90 조건을 만족해야 합니다.",
    "Bottom O2 access must be off because the connected metal bottom plate is treated as sealed.": "연결된 금속 바닥판을 밀폐면으로 계산하므로 바닥면의 산소 유입을 끄세요.",
    "Simulation stopped by user.": "사용자가 시뮬레이션을 중단했습니다.",
    "Calibration stopped by user.": "사용자가 실측 보정을 중단했습니다.",
    "Initial temperature must be above absolute zero (-273.15°C).": "초기 온도는 절대영도(-273.15°C)보다 높아야 합니다.",
    "Oven profile temperatures must be above absolute zero (-273.15°C).": "오븐 온도 이력의 온도는 절대영도(-273.15°C)보다 높아야 합니다.",
    "Top/lid thickness must be >= 0.": "상부 덮개 두께는 0 이상이어야 합니다.",
    "Grid dimensions must be integers.": "각 방향의 격자 수는 정수로 입력해 주세요.",
    "Oven profile points must contain finite time and temperature values.": "오븐 온도 이력의 시간과 온도는 무한대나 NaN이 아닌 유효한 숫자여야 합니다.",
    "Calculated stable time step must be finite and > 0.": "안정 조건에 따른 시간 간격은 무한대나 NaN이 아니며 0보다 커야 합니다.",
    "True": "사용", "False": "사용 안 함",
    "1. Powder region": "1. 분말 충전 영역", "2. Build box": "2. 빌드박스",
    "3. SiC / PSD / k_eff": "3. SiC·입도·유효 열전도율", "4. Oven heat": "4. 오븐 열전달",
    "5. O₂ diffusion": "5. 산소 확산", "6. Calibration": "6. 실측 보정",
    "7. Numerical": "7. 수치 계산", "8. Oven profile": "8. 오븐 온도 이력", "9. Results": "9. 결과",
}


TEMPLATES_KO = {
    "{0} must be a finite number.": "{0} 값은 무한대나 NaN이 아닌 유효한 숫자여야 합니다.",
    "{0} must be > 0.": "{0} 값은 0보다 커야 합니다.",
    "could not convert string to float: {0}": "숫자로 읽을 수 없는 입력값입니다: {0}",
    "invalid literal for int() with base 10: {0}": "정수로 읽을 수 없는 입력값입니다: {0}",
    "Solid fraction: {0}   |   Porosity: {1}": "고체 부피 비율: {0}   |   기공률: {1}",
    "PSD span={0}   |   lognormal σ={1}   |   estimated D32={2} µm": "입도 분포 폭={0}   |   로그정규분포 σ={1}   |   추정 D32={2} µm",
    "Mode: {0}": "계산 방식: {0}", "Bed density: {0} g/cc": "충전 밀도: {0} g/cc",
    "Porosity: {0}": "기공률: {0}", "PSD span: {0}": "입도 분포 폭: {0}",
    "Estimated D32: {0} µm": "추정 D32: {0} µm",
    "Calibration k scale: {0}": "보정한 열전도율 배율: {0}", "Calibrated h: {0} W/m²K": "보정한 대류계수 h: {0} W/m²K",
    "Current inputs and oven profile were saved.\n\n{0}": "현재 입력값과 오븐 온도 이력을 저장했습니다.\n\n{0}",
    "Restored the last saved inputs and oven profile.\n\n{0}": "저장한 입력값과 오븐 온도 이력을 불러왔습니다.\n\n{0}",
    "Could not read CSV.\\n{0}": "CSV 파일을 읽지 못했습니다.\n{0}",
    "Loaded {0} points | {1}–{2} min | {3}": "데이터 {0}개 | {1}–{2}분 | {3}",
    "Tortuosity τ: {0} ({1})": "굴곡도 τ: {0} ({1})",
    "PSD-based characteristic pore length: {0} µm": "입도로 추정한 대표 기공 길이: {0} µm",
    "Knudsen correction: {0}": "크누센 확산 보정: {0}",
    "Calibration eval {0}: RMSE={1}°C, k-scale={2}, h={3}": "보정 계산 {0}회: RMSE={1}°C, 열전도율 배율={2}, h={3}",
    "Calibration completed: RMSE={0} °C": "실측 보정 완료: RMSE={0} °C",
    "Fit: k scale={0}, h={1} W/m²K, RMSE={2}°C, nfev={3}": "보정 결과: 열전도율 배율={0}, h={1} W/m²K, RMSE={2}°C, 계산 횟수={3}",
    "k_eff scale = {0}\nh = {1} W/m²K\nCenter-temperature RMSE = {2} °C\n\nMode has been switched to Calibrated. Press Run simulation to generate the final 3D result.": "열전도율 배율 = {0}\nh = {1} W/m²K\n중심 온도 RMSE = {2} °C\n\n계산 방식을 실측 보정으로 변경했습니다. 시뮬레이션 실행 버튼을 눌러 최종 3차원 결과를 계산하세요.",
    "3D O₂ diffusion field — {0} h": "3차원 산소 농도 분포 — {0} h",
    "3D powder temperature field — {0} h": "3차원 분말 온도 분포 — {0} h",
    "display {0}/{1} cells": "표시 격자 {0}/{1}개", "{0} cells": "격자 {0}개",
    "{0}/{1}  t={2} h  Center={3} vol%  Range={4}–{5} vol%  |  {6}": "{0}/{1}  경과={2} h  중심={3} vol%  범위={4}–{5} vol%  |  {6}",
    "{0}/{1}  t={2} h  Oven={3}°C  Center={4}°C  Range={5}–{6}°C  |  {7}": "{0}/{1}  경과={2} h  오븐={3}°C  중심={4}°C  범위={5}–{6}°C  |  {7}",
    "Grid: nx×ny×nz = {0}×{1}×{2}": "격자 수 nx×ny×nz = {0}×{1}×{2}",
    "Stable Δt actually used: {0} s": "안정 조건에 따라 사용한 시간 간격 Δt: {0} s",
    "Powder solid fraction: {0}": "분말의 고체 부피 비율: {0}", "Powder porosity: {0}": "분말층 기공률: {0}",
    "k_eff mode: {0}": "유효 열전도율 계산 방식: {0}",
    "Center k_eff at start: {0} W/mK": "초기 중심 유효 열전도율: {0} W/mK",
    "Center k_eff at end: {0} W/mK": "최종 중심 유효 열전도율: {0} W/mK",
    "3D snapshots stored: {0} frames": "저장한 3차원 결과: {0}개",
    "3D snapshot interval used: {0} min": "실제 3차원 결과 저장 간격: {0} min",
    "O₂ diffusion-only enabled: {0}": "산소 확산 계산: {0}",
    "Peak center temperature: {0} °C": "최고 중심 온도: {0} °C",
    "Final center temperature: {0} °C": "최종 중심 온도: {0} °C",
    "Final side-wall average: {0} °C": "최종 측벽 평균 온도: {0} °C",
    "Final bottom-plate average: {0} °C": "최종 바닥판 평균 온도: {0} °C",
    "Final under-plate air-layer h: {0} W/m²K": "최종 바닥판 아래 공기층의 열전달계수 h: {0} W/m²K",
    "Final under-plate air-layer heat: {0} W": "최종 바닥판 아래 공기층 열전달률: {0} W",
    "Final under-plate radiation heat: {0} W": "최종 바닥판 아래 복사 열전달률: {0} W",
    "Maximum oven-center ΔT: {0} °C at {1} h": "오븐과 중심의 최대 온도 차: {0} °C ({1} h)",
    "Center TC comparison RMSE: {0} °C": "중심 열전대 실측과의 RMSE: {0} °C",
    "- No forced convection is applied in the {0}-mm under-plate gap.": "- 바닥판 아래 {0} mm 공기층에는 강제대류를 적용하지 않습니다.",
    "Ambient O₂: {0} vol%": "외부 산소 농도: {0} vol%",
    "Initial pore O₂: {0} vol%": "기공 내 초기 산소 농도: {0} vol%",
    "Final center O₂: {0} vol%": "최종 중심 산소 농도: {0} vol%",
    "Final volume-average O₂: {0} vol%": "최종 분말층 평균 산소 농도: {0} vol%",
    "Center D_eff at start: {0} m²/s": "초기 중심 유효 확산계수: {0} m²/s",
    "Center D_eff at end: {0} m²/s": "최종 중심 유효 확산계수: {0} m²/s",
    "Center cumulative O₂ dose: {0} air-equivalent h": "중심부 누적 산소 노출량: 공기 중 {0}시간에 해당",
    "  {0}% of ambient: not reached": "  외부 농도의 {0}%: 도달하지 않음",
    "  {0}% of ambient: {1} min ({2} h)": "  외부 농도의 {0}%: {1}분 ({2}시간)",
    "Saved:\n{0}": "저장했습니다.\n{0}",
    "{0}\nX: {1} h\nY: {2} °C": "{0}\n경과 시간: {1} h\n온도: {2} °C",
    "{0}\nX: {1} h\nY: {2} vol%": "{0}\n경과 시간: {1} h\n산소 농도: {2} vol%",
}


def _compile_template(template):
    if template == "{0} cells":
        return re.compile(r"([\d,]+) cells")
    parts = re.split(r"(\{\d+\})", template)
    return re.compile("".join("(.+?)" if re.fullmatch(r"\{\d+\}", p) else re.escape(p)
                              for p in parts), re.DOTALL)


_TEMPLATES = [(_compile_template(en), ko) for en, ko in TEMPLATES_KO.items()]


class OvenTranslator:
    def __init__(self, language_getter):
        self.language_getter = language_getter
        self.widgets = []
        self._rendered = {}

    def canonical(self, text):
        return self._rendered.get(str(text), str(text))

    def __call__(self, text):
        text = str(text)
        if self.language_getter() != "ko":
            return text
        translated = self._korean(text)
        if translated != text:
            self._rendered[translated] = text
        return translated

    def _korean(self, text):
        if text in TEXT_KO:
            return TEXT_KO[text]
        for pattern, ko in _TEMPLATES:
            match = pattern.fullmatch(text)
            if match:
                values = [self._value(value) for value in match.groups()]
                return ko.format(*values)
        # Summaries are explicit lists of independent presentation lines. No
        # substring replacements are applied to paths or machine identifiers.
        if "\n" in text:
            return "\n".join(self._korean(line) for line in text.split("\n"))
        return text

    def _value(self, value):
        if value in TEXT_KO:
            return TEXT_KO[value]
        if re.fullmatch(r"(?:display [\d,]+/[\d,]+ cells|[\d,]+ cells)", value):
            return self._korean(value)
        return value

    def refresh(self):
        live = []
        for widget in self.widgets:
            try:
                if widget.winfo_exists():
                    widget._oven_refresh()
                    live.append(widget)
            except tk.TclError:
                continue
        self.widgets[:] = live


class CanonicalCombobox(ttk.Combobox):
    """Translated choices with stable get/set values for physics and persistence."""
    def __init__(self, master, *, translator, model_variable=None, **kwargs):
        self._translator = translator
        self._model_variable = model_variable
        self._canonical_values = tuple(kwargs.pop("values", ()))
        super().__init__(master, **kwargs)
        self._oven_refresh()
        translator.widgets.append(self)
        if model_variable is not None:
            self.set(model_variable.get())
            model_variable.trace_add("write", lambda *_: self.set(model_variable.get()))
            self.bind("<<ComboboxSelected>>", self._sync_variable, add="+")

    def get(self):
        displayed = super().get()
        labels = tuple(self._display(value) for value in self._canonical_values)
        try:
            return self._canonical_values[labels.index(displayed)]
        except ValueError:
            return displayed

    def _display(self, value):
        if self._model_variable is not None and value in ("ko", "en"):
            return {"ko": "한국어", "en": "English"}[value]
        return self._translator(value)

    def set(self, value):
        super().set(self._display(value))

    def _sync_variable(self, _event=None):
        if self._model_variable is not None:
            self._model_variable.set(self.get())

    def _oven_refresh(self):
        current = super().get()
        # Before changing language the reverse cache resolves old translated
        # labels. Canonical identities remain untouched.
        current = self._translator.canonical(current)
        ttk.Combobox.configure(self, values=tuple(self._display(v) for v in self._canonical_values))
        if self._model_variable is not None:
            current = self._model_variable.get()
        self.set(current)


class LocalizedWidgets:
    def __init__(self, translator):
        self.translator = translator

    def __getattr__(self, kind):
        if kind == "Combobox":
            return lambda master, **kwargs: CanonicalCombobox(master, translator=self.translator, **kwargs)
        widget_cls = tk.Text if kind == "Text" else getattr(ttk, kind)

        def create(*args, **kwargs):
            widget = widget_cls(*args, **kwargs)
            self._attach(widget)
            return widget
        return create

    def _attach(self, widget):
        translator = self.translator
        original_configure = widget.configure
        text = {"value": str(widget.cget("text"))} if "text" in widget.keys() else None

        def configure(cnf=None, **kwargs):
            if isinstance(cnf, dict):
                kwargs = {**cnf, **kwargs}
                cnf = None
            if text is not None and "text" in kwargs:
                text["value"] = str(kwargs["text"])
                kwargs["text"] = translator(text["value"])
            return original_configure(cnf, **kwargs)

        widget.configure = configure
        widget.config = configure
        notebook_labels = {}
        headings = {}
        if isinstance(widget, ttk.Notebook):
            add = widget.add
            tab = widget.tab
            def translated_add(child, **kwargs):
                if "text" in kwargs:
                    notebook_labels[str(child)] = kwargs["text"]
                    kwargs["text"] = translator(kwargs["text"])
                return add(child, **kwargs)
            widget.add = translated_add
        elif isinstance(widget, ttk.Treeview):
            heading = widget.heading
            def translated_heading(column, option=None, **kwargs):
                if "text" in kwargs:
                    headings[column] = kwargs["text"]
                    kwargs["text"] = translator(kwargs["text"])
                return heading(column, option=option, **kwargs)
            widget.heading = translated_heading
        elif isinstance(widget, tk.Text):
            insert = widget.insert
            def translated_insert(index, chars, *args):
                return insert(index, translator(chars), *args)
            widget.insert = translated_insert

        def refresh():
            if text is not None:
                original_configure(text=translator(text["value"]))
            for child, label in notebook_labels.items():
                tab(child, text=translator(label))
            for column, label in headings.items():
                heading(column, text=translator(label))
        widget._oven_refresh = refresh
        translator.widgets.append(widget)
        refresh()


class LocalizedDialogs:
    def __init__(self, translator):
        self.translator = translator

    def __getattr__(self, method):
        if hasattr(messagebox, method):
            function = getattr(messagebox, method)
            def dialog(*args, **kwargs):
                args = list(args)
                for index in range(min(2, len(args))):
                    args[index] = self.translator(args[index])
                for key in ("title", "message", "detail"):
                    if key in kwargs:
                        kwargs[key] = self.translator(kwargs[key])
                return function(*args, **kwargs)
            return dialog
        function = getattr(filedialog, method)
        def file_dialog(*args, **kwargs):
            if "title" in kwargs:
                kwargs["title"] = self.translator(kwargs["title"])
            if "filetypes" in kwargs:
                kwargs["filetypes"] = [(self.translator(label), pattern)
                                       for label, pattern in kwargs["filetypes"]]
            return function(*args, **kwargs)
        return file_dialog
