-- 001_initial_schema.sql
-- Full initial schema for the Reddit Troubleshooting Monitor & Reply Agent.
-- Tables are created in dependency order (subreddits before posts, posts
-- before scores/replies/events).

CREATE TABLE IF NOT EXISTS subreddits (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    cursor              TEXT,
    rules_notes         TEXT,
    automation_allowed  BOOLEAN NOT NULL DEFAULT FALSE,
    rules_reviewed_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,
    subreddit_id    INTEGER NOT NULL REFERENCES subreddits(id),
    content_hash    TEXT NOT NULL,
    raw_payload     JSONB NOT NULL,
    status          TEXT NOT NULL,
    created_utc     TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    id              SERIAL PRIMARY KEY,
    post_id         TEXT NOT NULL REFERENCES posts(id),
    score           INTEGER NOT NULL CHECK (score >= 0 AND score <= 100),
    reason          TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    factors         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS replies (
    id                  SERIAL PRIMARY KEY,
    post_id             TEXT NOT NULL UNIQUE REFERENCES posts(id),
    reply_text          TEXT NOT NULL,
    mode                TEXT NOT NULL,
    reddit_comment_id   TEXT,
    status              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kill_switch (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    post_id     TEXT,
    stage       TEXT NOT NULL,
    decision    TEXT,
    reason      TEXT,
    latency_ms  INTEGER,
    cost_usd    NUMERIC(10, 6),
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_events_post_id ON events(post_id);