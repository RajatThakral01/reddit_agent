# AI Usage Disclosure
**Project:** Project 01 — Reddit Troubleshooting Monitor & Reply Agent
**Evaluation ID:** DAXVORA-RAJAT-2026-08-A01

## Models Used
| Model | Provider | Version | Dates Used | Purpose |
|---|---|---|---|---|
| DeepSeek v4 Flash (`deepseek-v4-flash`) | DeepSeek (OpenRouter) | v4-flash-latest | 2026-08-19 → 2026-08-20 | Code generation, refactoring, documentation, verification |
| DeepSeek Chat (`deepseek-chat`) | DeepSeek | — (referenced in code/config for LLM calls) | not exercised | Runtime scoring/reply provider (mocked in all tests) |

> Note: during the entire build no LLM API spend was incurred — the runtime
> provider was always either mocked (tests) or replaced by the rule-based
> pre-filter (CLI verification runs had no LLM calls). Actual spend: **$0.00**.

## Tools / Skills / Plugins Used
- opencode (CLI coding agent) with tool access: file read/write/edit, glob/grep,
  bash (git, pytest, psql), web fetch, task/subagent execution.
- Python 3.12 dev environment, pytest (+asyncio, mock), psycopg2/Postgres local,
  click CLI, pytest-mock for all LLM/HTTP boundaries.

## Major Prompts Given
See `PROMPTS.md` (P1–P15). In brief:
1. Project scaffold + compliance files
2. Typed exceptions module
3. Config system with validation (FR-1)
4. DB schema + migrations (FR-3 idempotency constraint)
5. Observability/audit logging (FR-12)
6. Domain model + fixture data (7 categories)
7. RedditSource interface + TestRedditSource
8. Ingest pipeline (dedup/edit/unactionable)
9. Scoring engine + cost tracker (FR-6/14)
10. Reply generation + guardrails (FR-7/8/8a)
11. Posting gate + kill switch + LIVE (FR-9/9a/10/11)
12. Pipeline orchestrator + CLI (FR-13)
13. LiveRedditSource (asyncpraw)
14. README + architecture diagram + check-env
15. Final verification, security scan, verification log, disclosure, submission

## Generated Files
- `reddit_agent/__init__.py`, `__main__.py`, `cli.py`, `config.py`,
  `exceptions.py`, `models.py`, `observability.py`, `ingest.py`, `scoring.py`,
  `cost_tracker.py`, `guardrails.py`, `reply_gen.py`, `posting.py`,
  `pipeline.py`
- `reddit_agent/sources/__init__.py`, `base.py`, `test_source.py`, `live.py`
- `reddit_agent/db/__init__.py`, `models.py`, `connection.py`,
  `migrations/001_initial_schema.sql`, `migrations/002_seed.sql`
- `admin_api/__init__.py`
- `tests/` — `test_config.py`, `test_db.py`, `test_observability.py`,
  `test_models.py`, `test_sources.py`, `test_ingest.py`, `test_scoring.py`,
  `test_guardrails.py`, `test_reply_gen.py`, `test_posting.py`,
  `test_pipeline_e2e.py`, `test_live_source.py`, `__init__.py`,
  `fixtures/{positive,negative,ambiguous,edge_cases,duplicate,edited,risk}.json`
- `conftest.py`, `.gitignore`, `.env.example`, `pyproject.toml`, `README.md`
- `docs/architecture.md`, `COST_AND_LIMITS.md`, `VERIFICATION_LOG.md`,
  `test_report.txt`, `PROMPTS.md`, `AI_USAGE.md`, `ai-usage.json`

## Human-Modified Files
- `.env.example` and `pyproject.toml` seeds were supplied by the human in the
  initial prompt (base templates used verbatim).
- The human reviewed output throughout and requested cleanup fixes (e.g.,
  duplicated `_seed_post` calls in `tests/test_posting.py`).
- No human-authored feature code was added; all application code was generated
  and then reviewed.

## Verification Performed
- `pytest tests/ -v --tb=short` → **91 passed, 3 skipped** (live-skip only);
  report saved to `test_report.txt`.
- Security scan: `git grep -rIn "API_KEY|SECRET|PASSWORD|Bearer sk-|Bearer ds-"`
  → no real secrets found.
- `.gitignore` verified: no `.env` tracked.
- Manual CLI: ConfigError for 6 subreddits; `check-env` missing-var handling;
  `kill-switch enable/disable/status`; `run --once` against fixtures with zero
  network (see `VERIFICATION_LOG.md`).
- Idempotency/kill-switch/cost-cap verified by automated tests.

## Known Limitations of AI-Assisted Parts
- LLM-provider behavior verified only through mocked/unit paths; live scoring
  and posting were not exercised because no credentials/approval were available
  (documented openly, no fabricated "live" claims).
- `TestRedditSource` returns fixtures in a fixed order; it does not model live
  pagination edge cases.
- Cost estimates use candidate-set DeepSeek pricing constants; adjust for the
  final model rates before real spend.
- All code is AI-generated and has not been hardened by an independent human
  security review beyond the included secrets scan.