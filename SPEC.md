# SCOOP (Sky Cone of Observations and Papers), Team 1
Members: Radha Mastandrea, Kevin McKinnon, Duo Xu, Xuan Ji, Yilun Guan
Repo: https://github.com/KevinMcK95/scoop.git

## 1. Task
SCOOP answers: “What do we know about this object/region/topic in the sky?” for astronomers who currently need to query many archives manually. Users provide a natural-language astronomy query (or coordinates + radius), and SCOOP returns a merged result: objects, observations, spectra/images/catalog entries, relevant papers/proposals/telegrams, plus a citation-backed summary and downloadable files. The primary users are researchers, students, and observers planning or interpreting science observations.

Example: `"QSOs within 1 degree radius of the center of M31"` -> a list of matching QSOs with archive coverage (images/spectra/catalog records), relevant papers, and a short cited summary of key findings.

## 2. Why an agent?
The model must make runtime decisions: parse ambiguous natural-language intent into a structured plan, choose which archives/tools to query first, handle partial failures/timeouts, decide when to refine or re-rank literature, and synthesize evidence with citations. This is not a fixed deterministic script because query intent, data availability, and cross-source conflicts vary per request.

## 3. Tools and data (3-6 tools)
- SIMBAD/NED resolver + object profiles: resolve names/groups/classes and fetch object metadata. In: names, coordinates, object constraints. Out: canonical targets, IDs, classifications, positions/redshifts/distances. Data source: SIMBAD, NED. Access checked: yes.
- MAST/IRSA/HEASARC archive search: query mission and survey coverage for cone/region. In: sky region + filters. Out: observations, products, links, metadata. Data source: MAST, IRSA, HEASARC. Access checked: yes.
- ADS + arXiv literature retrieval: fetch and rank relevant papers and abstracts. In: object IDs, topic query, constraints. Out: ranked papers, metadata, abstracts. Data source: NASA ADS, arXiv. Access checked: yes.
- Multi-messenger/transient checks: test containment in GW/neutrino/GRB/FRB localizations and transient reports. In: coordinates/region, time constraints. Out: containment hits and event metadata. Data source: GraceDB/GCN/related feeds. Access checked: no.
- Summarization + compiler layer: merge heterogeneous outputs and generate citation-backed narrative. In: structured data + literature records. Out: unified result schema + summary + download manifest. Data source: internal pipeline over all above sources. Access checked: yes.

## 4. Baseline (Milestone III, Tue 5 PM)
Baseline is a single-step workflow: one LLM call with no external tools that takes the user query and produces a plain-language answer template. For coordinate/object input, baseline optionally echoes a static placeholder structure (objects/data/papers/summary) without live retrieval. Agent performance is compared against this baseline on factual coverage, citation quality, and utility.

## 5. Evaluation (Milestone IV, Wed 11 AM)
- Test cases: 10 cases with pass/fail checks (mix of object-name queries, cone searches, and topic-driven literature queries) and expected verifiable outcomes (e.g., at least one known paper/object match, correct coordinate resolution behavior, schema completeness).
- Metric: primary metric = task success rate; secondary metrics = citation validity, p95 latency, cost per request, and budget-violation rate.
- Runs per case: 3 runs per case (to account for model variance), reported as mean and spread.
- Comparison: baseline vs. agent on the same case set; optional model comparison on the agent path.
- RL component (optional): observation = query state + retrieval status; actions = select next tool/query/refine strategy; reward = successful retrieval + citation validity - latency/cost penalties; baseline policy = fixed tool order.

## 6. Demo (Milestone V, Wed 1 PM)
Live demo query: `"QSOs within 1 degree radius of the center of M31"`. Audience should see: (1) parsed plan, (2) parallel retrieval from object/database/archive/literature sources, (3) merged result with citations, and (4) concise summary + downloadable data manifest.

## 7. Risks, limits
- Biggest risk: external service instability/rate limits and heterogeneous schema mismatches across sources.
- Fallback if you are behind on Tuesday at noon: narrow to one object-resolution path + one archive + one literature source, still end-to-end with summary.
- Limits: max 12 tool steps/request, max 120k tokens/request, expected spend cap $5/day on team keys during development.
