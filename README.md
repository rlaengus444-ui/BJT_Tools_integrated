# BJT Tools Integrated

BJT 3D model → slicing → shell/lattice generation → STL integrated Python application.

## Current source
- `BJT_model_to_lattice_STL_all_in_one.py` — v5.6 (source snapshot dated 2026-09-11).
- Single-file application with dependency bootstrap.
- Run: `python BJT_model_to_lattice_STL_all_in_one.py`
- Recommended environment: 64-bit Windows with suitable Python; see source's pinned dependencies.

## Important limitations
The 400-DPI, 0.1-mm layer-print proxy STL mode preserves layer masks, not necessarily continuous CAD geometry or topology. Independently validate with real slicing output before production.

## Previous version inventory
- **v5.5**: an original distribution archive `BJT_SG1024_400dpi_v5_5.zip` was found in the user's file library (2026-09-10). Its contents have not yet been imported into this GitHub repository.
- Earlier algorithm sections mentioned inside the v5.6 file (e.g., v5.2/v5.3) are **not** separate, verified version snapshots.
- Do not reconstruct or mislabel old releases from current source.

Changes from this imported snapshot onward are traceable through Git commits.
