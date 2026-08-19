# Prompts Given During Build — Project 01

These are the high-level prompts supplied during the build of
DAXVORA-RAJAT-2026-08-A01. They are referenced by name from `AI_USAGE.md`.

1. **P00 — Read the two source files** (PRD + Implementation Plan)
2. **P1 — Project scaffold** — exact folder/file tree, `.gitignore`, `.env.example`,
   `pyproject.toml`, `AI_USAGE.md` template, `ai-usage.json` template, README
   section placeholders; git init + first commit.
3. **P2 — Typed exceptions module** — full exception hierarchy in `exceptions.py`.
4. **P3 — Configuration system** — `config.py` (pydantic-settings `AgentConfig`),
   `cli.py`/`__main__.py` stubs, `tests/test_config.py` (FR-1).
5. **P4 — Database schema & migrations** — `db/models.py`, `001_initial_schema.sql`,
   `002_seed.sql`, `db/connection.py`, `cli db migrate`, `tests/test_db.py`.
6. **P5 — Observability** — `observability.py` (`Stage`, `LogEvent`, `log_event`,
   `timed_stage`, redaction), `tests/test_observability.py` (FR-12).
7. **P6 — Domain models + fixtures** — `models.py` (`NormalizedPost`),
   7 fixture JSON categories, `tests/test_models.py`.
8. **P7 — RedditSource interface + TestRedditSource** — `sources/base.py`,
   `sources/test_source.py`, live stub, `get_source` factory, `tests/test_sources.py`.
9. **P8 — Ingest pipeline** — `ingest.py` (dedup/edit/unactionable),
   `tests/test_ingest.py` (FR-2,3,4,5).
10. **P9 — Scoring engine** — `scoring.py` (pre-filter, LLM scorer), `cost_tracker.py`,
    `tests/test_scoring.py` (FR-6, FR-14).
11. **P10 — Reply generation + guardrails** — `reply_gen.py`, `guardrails.py`
    (hazard FR-8a, denylist FR-8), `tests/test_guardrails.py`, `tests/test_reply_gen.py`.
12. **P11 — Posting gate + kill switch** — `posting.py` (4 gates, idempotent LIVE
    post, retry/backoff), `kill-switch` CLI, `tests/test_posting.py` (FR-9,9a,10,11).
13. **P12 — Pipeline orchestrator + CLI** — `pipeline.py`, full `run`/`check-env`
    CLI, `tests/test_pipeline_e2e.py` (FR-13).
14. **P13 — LiveRedditSource** — asyncpraw implementation, `tests/test_live_source.py`.
15. **P14 — Docs + check-env** — full README, `docs/architecture.md`, check-env.
16. **P15 — Final verification & submission** — this set of prompts: full suite
    report, security scan, cost/limits doc, verification log, AI disclosure,
    tag/submission.

Note: no prompt in this list contains real credentials or personal data.