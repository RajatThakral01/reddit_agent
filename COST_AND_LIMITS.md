# Cost & Rate Limits — Project 01

DAXVORA-RAJAT-2026-08-A01 · Reddit Troubleshooting Monitor & Reply Agent

## LLM (DeepSeek v4 Flash / `deepseek-chat`)

Pricing model used in code: **$0.14 / 1M input tokens, $0.28 / 1M output tokens**
(scoring.py and reply_gen.py constants; candidate-set estimates, adjust if the
final model's published rate differs).

| Item | Input tokens (approx.) | Output tokens (approx.) | Estimated cost |
|---|---|---|---|
| Worthiness scoring call | 300 | 90 | **$0.00007** |
| Reply generation call | 400 | 60 | **$0.00003** |
| Full pipeline per replied post (score + generate) | 620 | 150 | **~$0.00010** |
| Full fixture test run (≈12 LLM-worthy posts) | ~3,840 | ~1,800 | **~$0.00122** |

- Hard cap configured: **$0.50 per run** via `LLM_COST_CAP_USD`.
- Enforcement: in-memory `CostTracker` aborts the run with `CostCapExceeded` the
  moment cumulative spend exceeds the cap.
- Paid service: **YES** — written approval from Krishnam is required before any
  real (unmocked) LLM call is made.
- During development/testing, all LLM calls were **mocked** — actual spend was
  **$0.00**.
- Retries are bounded (`MAX_RETRY_ATTEMPTS`, default 4); a failed paid call is
  never retried unboundedly.

## Reddit API

- API tier: **Free, non-commercial** (script app type).
- Rate limit: **100 requests per 10 minutes** (per script app).
- Live source throttles below 5 remaining requests and honors
  `X-Ratelimit-Reset` on 429s.
- Max subreddits: **5** (enforced at config load — `ConfigError` otherwise).
- No paid Reddit tier used.

## Database

- PostgreSQL (local) — **no cloud cost**.
- State, dedup, and audit events all live locally.

## Total project spend to date
- **$0.00** — every LLM path exercised via fixtures or mocked HTTP; live Reddit
  integration was not run without credentials/approval.