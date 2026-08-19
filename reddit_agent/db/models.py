"""SQLAlchemy Core table definitions for the Reddit Agent persistence layer.

This module defines the full Postgres schema as SQLAlchemy Core ``Table``
objects (not ORM) for clarity and explainability. Every table mirrors the
migration scripts in ``db/migrations/``.

The schema guarantees the agent's two hardest correctness properties:
- The ``replies.post_id`` UNIQUE constraint is the idempotency guarantee that
  prevents a second public reply for the same post.
- ``subreddits.automation_allowed`` defaults to FALSE, so no posting can
  happen until a human explicitly confirms a subreddit's rules allow bots.

Note: JSONB columns are represented with SQLAlchemy's cross-dialect ``JSON``
type here. The authoritative column types (including ``jsonb``) live in the
raw-SQL migration files, which are what actually create the tables.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)

metadata = MetaData()


def define_tables(meta: MetaData) -> list[Table]:
    """Instantiate and return all table objects bound to ``meta``.

    Returns the tables in dependency order so callers can create/insert them
    in the correct sequence.
    """
    subreddits = Table(
        "subreddits",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", Text, nullable=False, unique=True),
        Column("cursor", Text, nullable=True),
        Column("rules_notes", Text, nullable=True),
        Column("automation_allowed", Boolean, nullable=False, default=False),
        Column("rules_reviewed_at", TIMESTAMP(timezone=True), nullable=True),
    )

    posts = Table(
        "posts",
        meta,
        Column("id", Text, primary_key=True),
        Column("subreddit_id", Integer, nullable=False),
        Column("content_hash", Text, nullable=False),
        Column("raw_payload", JSON, nullable=False),
        Column("status", Text, nullable=False),
        Column("created_utc", TIMESTAMP(timezone=True), nullable=False),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
    )

    scores = Table(
        "scores",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("post_id", Text, nullable=False),
        Column("score", Integer, nullable=False),
        Column("reason", Text, nullable=False),
        Column("confidence", Text, nullable=False),
        Column("policy_version", Text, nullable=False),
        Column("factors", JSON, nullable=False),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
    )

    replies = Table(
        "replies",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("post_id", Text, nullable=False),
        Column("reply_text", Text, nullable=False),
        Column("mode", Text, nullable=False),
        Column("reddit_comment_id", Text, nullable=True),
        Column("status", Text, nullable=False),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        UniqueConstraint("post_id", name="uq_replies_post_id"),
    )

    kill_switch = Table(
        "kill_switch",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("enabled", Boolean, nullable=False, default=False),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
        CheckConstraint("id = 1", name="single_row"),
    )

    events = Table(
        "events",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("post_id", Text, nullable=True),
        Column("stage", Text, nullable=False),
        Column("decision", Text, nullable=True),
        Column("reason", Text, nullable=True),
        Column("latency_ms", Integer, nullable=True),
        Column("cost_usd", Numeric(10, 6), nullable=True),
        Column("error", Text, nullable=True),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    )

    return [subreddits, posts, scores, replies, kill_switch, events]


TABLES = define_tables(metadata)