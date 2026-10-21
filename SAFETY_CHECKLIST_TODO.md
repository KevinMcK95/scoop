# Agentic System Safety Checklist (Low-Risk Scientific Data)

## Essential (Minimal Overhead)
- [x] Per-request token/USD hard cap: enforce simple per-request limits. *(Per-conversation USD budgets, checked before each command runs: `scoop/cost.py`.)*
- [x] Domain allowlist + robots.txt respect: allow known scholarly domains (arXiv, PubMed). *(The unattended dataset scout, every redirect included: `scoop/scout/fetch.py`. The chat's `web_fetch` reads any public host on a user's request.)*
- [x] Fetch proxy with size + MIME limits and script removal: sanitize fetched content. *(`scoop/web.py`, `scoop/scout/fetch.py`.)*
- [ ] Rate limits: per-user and per-domain request throttles. *(Per-host throttle in the scout only.)*
- [x] Immutable audit log (append-only file/DB): record requests, plans, approvals, and tool calls. *(One JSON line per turn, no message text: `--audit-log` in `scoop/server`.)*

## Recommended (Lightweight, Opt-in)
- [x] Pre-execution cost estimate: show token/USD estimate and warn when near cap. *(`scoop/cost.py`, shown in the chat panel.)*
- [x] Dry-run for any operation labeled “destructive” (e.g., deletes, writes to repos). *(`python -m scoop.scout --dry-run`.)*
- [ ] Simple PII/secret regex scanner on inputs/outputs; redact matches by default.
- [ ] Approval hook for ops above configurable cost/risk thresholds.

## Optional (Enable When Risk Grows)
- [ ] Containerized sandboxing and network egress controls. *(`run_python` runs in Pyodide under macOS sandbox-exec with no network; `run_kernel` is deliberately not sandboxed and off by default.)*
- [ ] Policy-as-code engine (OPA/Rego) and complex rule sets.
- [ ] Vector DB + fact-checking pipeline to reduce hallucinations.
- [ ] Multi-party approvals and automated rollback.

---

### Rationale
- Scholarly sources like arXiv are low-risk for malware/credential exfiltration; still enforce domain allowlist, rate-limits, and robots.txt to be good citizens.
- Token/cost controls prevent runaway expenses even for benign tasks.
- Lightweight fetching + logging gives traceability with minimal friction.

### Minimal Enforcement Components to Implement First
- [ ] Enforcement middleware: simple interceptor that checks per-request token cap, domain allowlist, and rate-limits.
- [x] Lightweight fetch proxy: single-process HTTP fetcher that strips scripts, enforces size limits, and respects robots.txt.
- [x] Cost estimator: rough token-count estimator (prompt + response size heuristic).
- [x] Append-only audit logger: write structured JSON events to disk/DB.
