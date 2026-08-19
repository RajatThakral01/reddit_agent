# Reddit Troubleshooting Monitor & Reply Agent

## Purpose

People post computer-repair and tech-troubleshooting problems on Reddit and often
get no reply, a wrong reply, or a promotional/spam reply. This agent monitors
1–5 subreddits for genuinely answerable troubleshooting posts, decides — with an
**explainable score and reason** — whether a post is worth a reply, and generates
a **grounded, safe, non-destructive, non-promotional** troubleshooting reply (or a
single clarifying question when information is missing).

It is designed for a technical operator (the DAXVORA evaluator, or a developer
running it locally). It never double-replies, never posts when it shouldn't, and
records a searchable audit event for every decision it makes. It runs identically
in `TEST` mode against fixture data with **zero network calls**, so the whole
system is verifiable even without live Reddit API approval.

**Evaluation ID:** DAXVORA-RAJAT-2026-08-A01

## Architecture

```
Reddit API (live) ─┐
                   ├──> RedditSource ──> Ingest ──> Pre-filter ──> LLM Scorer
Fixtures (test) ───┘   (test|live)     (dedup / edit /   (rules, no LLM)
                                        unactionable)
                                                  │  score >= threshold
                                                  ▼
                          Post Gate <── Guardrails <── Reply Generator
                     (mode + kill switch +      (denylist + hazard +           LLM)
                      subreddit policy)
                        │
                        ├── DRY_RUN → write simulated reply row
                        └── LIVE    → post comment to Reddit (idempotent)

Observability (events table + stdout JSON) cross-cuts every stage.
```

All mutable state lives in PostgreSQL, which is what makes restarts safe and
idempotency provable: the `replies.post_id` unique constraint is the *ultimate*
guarantee that a post is never replied to twice.

See **`docs/architecture.md`** for the full Mermaid flowchart of the pipeline
including every failure path.

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL (local or Docker)
- A DeepSeek API key (only needed when scoring/generation runs against the real
  LLM; fixtures + mocked tests need no key)

### Quick Start

```bash
git clone <repo>
cd reddit_agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your values
python -m reddit_agent db migrate
python -m reddit_agent check-env
python -m reddit_agent run --once
```

## Configuration

All configuration comes from environment variables (`.env` is loaded if present;
names only live in `.env.example`).

| Variable | Description | Default |
|---|---|---|
| `REDDIT_SOURCE` | `test` (fixture-driven, no network) or `live` (real Reddit API) | *(empty)* |
| `MODE` | `DRY_RUN` (never posts) or `LIVE` (posts when all gates pass) | `DRY_RUN` |
| `REDDIT_CLIENT_ID` | Reddit OAuth2 client id — live mode only | *(empty)* |
| `REDDIT_CLIENT_SECRET` | Reddit OAuth2 client secret — live mode only | *(empty)* |
| `REDDIT_USERNAME` | Reddit account username — live mode only | *(empty)* |
| `REDDIT_USER_AGENT` | Reddit user agent string — live mode only | *(empty)* |
| `LLM_PROVIDER` | LLM provider | `deepseek` |
| `LLM_API_KEY` | LLM API key (secret) | *(empty)* |
| `LLM_MODEL` | Exact model name to use | *(empty)* |
| `LLM_COST_CAP_USD` | Hard per-run LLM spend cap | `0.50` |
| `DATABASE_URL` | Postgres connection string | `postgresql://localhost:5432/reddit_agent` |
| `SUBREDDITS` | Comma-separated list (1–5) | `techsupport,pcmasterrace` |
| `KEYWORDS` | Comma-separated keyword list for the pre-filter | *(see .env.example)* |
| `WORTHINESS_THRESHOLD` | Score gate (0–100) | `65` |
| `POLL_INTERVAL_SECONDS` | Seconds between poll cycles | `300` |
| `MAX_RETRY_ATTEMPTS` | Max retries for provider calls | `4` |

**Important:** posting also requires `automation_allowed=true` for the target
subreddit in the `subreddits` table (a human must set it after reviewing the
subreddit's rules) and the kill switch must be off.

## Usage

### Modes

- **DRY_RUN** (default): processes posts, generates replies, writes `simulated`
  reply rows — but never posts to Reddit.
- **TEST** (`REDDIT_SOURCE=test`): uses fixture data from `tests/fixtures/`, makes
  zero network calls, and needs no Reddit credentials.
- **LIVE** (`MODE=LIVE` + `REDDIT_SOURCE=live`): posts to Reddit only when every
  gate passes (mode, kill switch, subreddit automation policy, idempotency).

Run one cycle:

```bash
python -m reddit_agent run --once
```

Or keep polling:

```bash
python -m reddit_agent run
```

### Kill Switch

The kill switch is checked **fresh from the database on every post attempt** —
never cached at startup.

```bash
python -m reddit_agent kill-switch enable   # stop all posting immediately
python -m reddit_agent kill-switch disable  # re-enable posting
python -m reddit_agent kill-switch status   # check current state
```

### Database

```bash
python -m reddit_agent db migrate           # create all tables + seed rows
```

### Check environment

```bash
python -m reddit_agent check-env           # PASS/FAIL per variable, exit 0/1
```

Secret values are never printed — they appear as `[SET]`.

### Running Tests

```bash
pytest tests/ -v                     # all tests (network-free, live tests auto-skip)
pytest tests/ -v -k "not live"      # explicitly skip live tests
```

Database-backed tests require `TEST_DATABASE_URL` (e.g.
`postgresql://localhost:5432/reddit_agent_test`) to be set; live-credential tests
require the `REDDIT_*` variables.

## Known Limitations

- Maximum **5 subreddits** per configuration (enforced with `ConfigError`).
- LIVE mode requires Reddit API approval (script app under the Responsible
  Builder Policy) and automation authorization per subreddit; it cannot be
  verified until credentials and approval are available.
- LLM scoring is only deterministic at `temperature=0` (the default). Changing
  the temperature makes scores variable.
- TEST mode returns fixtures in a fixed order and does not simulate live
  pagination/ordering edge cases beyond the fixture set.
- A single Reddit account is used; no multi-account or multi-tenant support.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ConfigError: at most 5 subreddits allowed, got N` | More than 5 subreddits in `SUBREDDITS` | Use 1–5 |
| `ConfigError: at least 1 subreddit required` | `SUBREDDITS` empty | Fill in at least one |
| `check-env` reports missing vars / exit 1 | Env not loaded or `.env` not copied | `cp .env.example .env`, add your values |
| `psycopg2.OperationalError: could not connect` | Postgres not running or wrong `DATABASE_URL` | Start Postgres, verify URL, run `db migrate` |
| `DATABASE_URL not set` | Env missing | Set `DATABASE_URL` or load `.env` |
| `Failed: UNIQUE constraint` on a reply | Duplicate ingest (by design) | Expected — no action needed |
| `GenerationFailed` repeatedly | Invalid LLM key/model or timeout | Verify `LLM_API_KEY`/`LLM_MODEL`, check logs |
| Live source auth errors | Bad/expired Reddit credentials | Re-check `REDDIT_*` vars and app type |
| Nothing is ever posted | `MODE` not `LIVE`, kill switch on, or `automation_allowed=false` | Check all three gates in `kill-switch status` and the DB |

> Note: redacting test secrets — the reply text, logs and fixtures never contain
> real credentials. `.env` is git-ignored; only `.env.example` (names, no values)
> is committed.