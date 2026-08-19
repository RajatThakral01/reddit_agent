# Architecture

## Overview

The Reddit Troubleshooting Monitor & Reply Agent is a single-process vertical
slice: a polling loop that pulls posts, decides worthiness with an explainable
score, generates a guarded reply, and posts only through a multi-gate safety
checklist. Every stage writes a structured audit event to PostgreSQL and a
single-line JSON record to stdout.

All mutable state lives in PostgreSQL. The unique constraint on
`replies.post_id` is the ultimate idempotency guarantee — even if application
logic is bypassed, the database refuses a second reply for the same post.

## Mermaid Flowchart

```mermaid
flowchart TD
    A[Reddit API / Fixtures] --> B[RedditSource\ntest or live]
    B --> C[Ingest Pipeline\nreddit_agent/ingest.py]
    C --> D{Already seen?\nEdit detected?}
    D -- duplicate --> E[Log: duplicate_ignored\nno re-processing]
    D -- unactionable --> F[Log: unactionable\nlocked / deleted / removed / archived / empty]
    D -- new/edited --> G[Pre-filter\nreddit_agent/scoring.py]
    G -- fails --> H[Log: skipped\nno LLM call]
    G -- passes --> I[LLM Scorer\nDeepSeek API + cost tracker]
    I --> J{Score >= threshold?}
    J -- no --> K[Log: skipped_low_score]
    J -- yes --> L[Reply Generator\nreddit_agent/reply_gen.py]
    L --> M[Guardrails\nreddit_agent/guardrails.py]
    M -- hazard --> N[Safety-first reply\nno repair steps]
    M -- blocked --> O[Log: blocked_by_guardrail\nnever posted]
    M -- clean --> P[Post Gate\nreddit_agent/posting.py]
    P --> Q{Kill switch on?}
    Q -- yes --> R[Log: kill_switch_active\npost suppressed]
    Q -- no --> S{automation_allowed?}
    S -- false --> T[Log: blocked_by_subreddit_policy]
    S -- true --> U{Mode?}
    U -- DRY_RUN --> V[Write replies: simulated\nnever touches Reddit]
    U -- LIVE --> W[Reddit API\npost comment\n(bounded retry + backoff)]
    W --> X[Log: posted\nstore reddit_comment_id]

    OBS[Observability\nreddit_agent/observability.py\nevents table + stdout JSON] -.-> C
    OBS -.-> G
    OBS -.-> I
    OBS -.-> L
    OBS -.-> M
    OBS -.-> P
```

## Stage descriptions

| Stage | Module | Decisions logged |
|---|---|---|
| Source | `sources/` | `test` (fixtures, zero network) or `live` (asyncpraw OAuth2) |
| Ingest | `ingest.py` | `new`, `duplicate_ignored`, `unactionable`, `requeued_for_scoring`, `edit_after_reply_ignored` |
| Pre-filter | `scoring.py` | `no_keyword_match`, `disallowed_flair`, `body_too_short`, `post_too_old` (no LLM) |
| Scorer | `scoring.py` | score 0–100 + `factors` + `policy_version`, `CostCapExceeded` |
| Generate | `reply_gen.py` | troubleshooting reply vs clarifying question |
| Guardrails | `guardrails.py` | `passed`, `safety_reply`, `blocked_by_guardrail` |
| Post gate | `posting.py` | `simulated`, `posted`, `duplicate`, `post_blocked`, `post_failed` |

## Failure paths

- **LLM failure/timeout** → bounded retry, then `status=error` (never a crash).
- **Reddit 429** → sleep `X-Ratelimit-Reset` + jitter, retry up to max.
- **Reddit 5xx/outage** → exponential backoff (1s, 2s, 4s, 8s) then pause/log.
- **Auth refresh failure** → `AuthRefreshFailed`; that subreddit pauses, process continues.
- **Cost cap** → `CostCapExceeded` aborts remaining LLM calls for the run.
- **Kill switch / policy / duplicate** → post suppressed without a Reddit call.