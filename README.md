# BJT Tools Integrated

BJT 3D model → slicing → shell/lattice generation → STL integrated Python application.

## Current source
- `BJT_model_to_lattice_STL_all_in_one.py` — v5.6 (source snapshot dated 2026-09-11).
- Single-file application with dependency bootstrap.
- Run: `python BJT_model_to_lattice_STL_all_in_one.py`
- Recommended environment: 64-bit Windows with suitable Python; see source's pinned dependencies.

## Important limitations
The 400-DPI, 0.1-mm layer-print proxy STL mode preserves layer masks, not necessarily continuous CAD geometry or topology. Independently validate with real slicing output before production.

## Verified historical sources

Authentic earlier code snapshots are stored under `versions/`:

- v5.5 — `versions/v5.5-print400-verified-candidate/BJT_model_to_lattice_STL_all_in_one.py`
- v4.6 — `versions/v4.6-cap-profiled-fast/BJT_model_to_lattice_STL_all_in_one.py`
- v4.6 validation-only baseline — `versions/v4.6-cap-profiled-fast/comparison_baseline.py` (not a separate release)

See [HISTORY.md](HISTORY.md) for origin archives, hashes, exclusions and validation limits. Do not reconstruct unknown releases from intermediate function names.

Changes after these imported snapshots remain traceable through Git commits.
