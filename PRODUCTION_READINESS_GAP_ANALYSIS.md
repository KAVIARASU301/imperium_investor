# Production Readiness Gap Analysis

## Scope reviewed
- App entrypoint, config, scanner integrations, caching, and repo hygiene.
- Representative files from Kite and IBKR flows.

## Top gaps and recommended actions

1. **No meaningful operational documentation**
   - Evidence: README currently has only a single title line.
   - Why this blocks production: no setup guide, runtime prerequisites, deployment model, rollback/runbook, or incident/debug instructions.
   - Action:
     - Expand README with install/start instructions for both broker modes.
     - Add architecture diagram and service boundaries.
     - Add on-call runbook (auth failures, websocket reconnect issues, scanner outages).

2. **Hardcoded user-specific scanner configuration**
   - Evidence: `PREMIUM_USER_ID = "570267"` is committed directly in scanner code.
   - Why this blocks production: environment-specific config in source creates operational risk and accidental leakage.
   - Action:
     - Move scanner identity/config to environment variables or secure config file.
     - Validate at startup and fail fast with clear error.
     - Add per-environment config profiles (dev/staging/prod).

3. **Potential sensitive token exposure in logs**
   - Evidence: scanner logs a prefix of CSRF token (`csrf_token[:10]`).
   - Why this blocks production: even partial tokens should generally be treated as secrets in logs.
   - Action:
     - Remove token logging entirely or redact to fixed placeholder.
     - Introduce a centralized log-redaction filter for API keys/tokens.

4. **Unsafe cache serialization format (`pickle`)**
   - Evidence: instrument cache uses `pickle.load` / `pickle.dump`.
   - Why this blocks production: pickle is unsafe for untrusted/compromised files and brittle for long-term compatibility.
   - Action:
     - Switch to JSON/Parquet/SQLite with schema versioning.
     - Add atomic writes and checksum/version metadata.
     - Enforce file permissions for local cache directory.

5. **No real automated test suite / CI quality gates**
   - Evidence: repo has only a connection script-style test file and no structured tests.
   - Why this blocks production: regression risk for order routing, position/PnL calculations, and market-data workflows.
   - Action:
     - Add pytest suite for critical business flows (order validation, PnL, cache logic, alert triggers).
     - Add CI pipeline: lint, type-check, unit tests, smoke tests.
     - Require status checks before merging.

6. **Debug scripts mixed into production tree**
   - Evidence: standalone print-based diagnostics script under `ibkr/core/linux_ibkr_deep_fix.py`.
   - Why this blocks production: unclear ownership and risk of ad-hoc scripts being run in production environments.
   - Action:
     - Move diagnostics to `scripts/diagnostics/` with clear execution contract.
     - Gate with explicit CLI flags and logging conventions.

## Recommended execution order
1. Establish config/secret management + log redaction.
2. Add tests and CI gates.
3. Replace pickle cache format.
4. Separate diagnostics/tools from runtime modules.
5. Complete docs/runbooks for release and operations.
