# Repository instructions — BJT_Tools_integrated

## Model routing
- Default work is done by GPT-6.1 Terra XHigh. Handle routine GUI adjustments,
  isolated functions, documentation, packaging and unit tests in the parent agent.
- **Escalate to the custom `sol_reviewer` subagent (GPT-6.1 Sol Max)** when:
  1. numerical methods, 3D heat/oxygen diffusion, material properties, unit
     conversions, physical boundary/initial conditions or discretization change;
  2. a change alters interfaces or shared state across multiple tools/modules,
     alters production output formats, or risks breaking existing saved inputs;
  3. an unexplained correctness/performance regression remains after two
     focused debugging attempts;
  4. release-critical changes or broad refactors require independent review.
- Before implementing a **new physics algorithm or cross-module architecture**,
  ask `sol_reviewer` for a design critique. For any escalated code change,
  invoke `sol_reviewer` again **after implementation** to review the diff,
  test coverage and regression risks. Wait for its answer, then resolve findings.
- For all other work, do not spawn Sol; ordinary helpers inherit Terra.
- Record in the final response whether Sol was actually spawned. If subagents
  are unavailable or the model is inaccessible, say so and do not imply review
  happened. The instructions request routing; they are not a hard automatic gate.

## Repository rules
- Current integrated app source is in `suite/`; entry point is
  `python suite/run_suite.py`.
- Preserve historical original snapshots under `versions/` byte-for-byte.
  Do not silently change the legacy root single-tool original.
- Keep packaged Windows executable assumptions and user settings/backward
  compatibility in mind. Do not launch release/build commands unless asked.
- For changes to thermal/oxygen simulation, test basic dimensions, units,
  stable time steps, boundary values, invalid-input handling and conservation
  or convergence where physically applicable.
- Prefer focused checks first; run available relevant tests and report what
  was actually tested. Do not claim an unrun GUI or Windows EXE was verified.
- No surprise dependency installs, external uploads, GitHub pushes or data
  deletion without the user's instruction.
