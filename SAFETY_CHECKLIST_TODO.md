# Agentic System Safety Checklist (Low-Risk Scientific Data)

## Essential (Minimal Overhead)
- [ ] Per-request token/USD hard cap: enforce simple per-request limits.
- [ ] Domain allowlist + robots.txt respect: allow known scholarly domains (arXiv, PubMed).
- [ ] Fetch proxy with size + MIME limits and script removal: sanitize fetched content.
- [ ] Rate limits: per-user and per-domain request throttles.
- [ ] Immutable audit log (append-only file/DB): record requests, plans, approvals, and tool calls.

## Recommended (Lightweight, Opt-in)
- [ ] Pre-execution cost estimate: show token/USD estimate and warn when near cap.
- [ ] Dry-run for any operation labeled “destructive” (e.g., deletes, writes to repos).
- [ ] Simple PII/secret regex scanner on inputs/outputs; redact matches by default.
- [ ] Approval hook for ops above configurable cost/risk thresholds.

## Optional (Enable When Risk Grows)
- [ ] Containerized sandboxing and network egress controls.
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
- [ ] Lightweight fetch proxy: single-process HTTP fetcher that strips scripts, enforces size limits, and respects robots.txt.
- [ ] Cost estimator: rough token-count estimator (prompt + response size heuristic).
- [ ] Append-only audit logger: write structured JSON events to disk/DB.
