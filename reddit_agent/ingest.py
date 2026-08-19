"""Ingestion pipeline: raw source data -> database.

This module holds the ONLY place that writes to the ``posts`` table's initial
state. It implements dedup, edit detection, and unactionable-content handling.

IMPORTANT DESIGN RULE: all dedup logic is enforced via DB state (SELECT from the
``posts`` table), never via in-memory sets. That is what makes process restarts
safe — a re-run of ingestion is just a batch of SELECTs against durable state.
"""

import json
from dataclasses import asdict

from reddit_agent.exceptions import UnactionableContent
from reddit_agent.models import NormalizedPost
from reddit_agent.observability import LogEvent, Stage, log_event

TERMINAL_STATUSES = ("replied", "skipped")


def _unactionable_reason(post: NormalizedPost) -> str:
    """Pick the exact reason string for a non-actionable post."""
    if post.is_locked:
        return "locked"
    if post.is_deleted:
        return "deleted"
    if post.is_removed:
        return "removed"
    if post.is_archived:
        return "archived"
    if not post.body.strip():
        return "empty_body"
    if not post.title.strip():
        return "malformed"
    return ""


def _post_to_dict(post: NormalizedPost) -> dict:
    """Serialize a NormalizedPost to a JSON-serializable dict for raw_payload."""
    data = {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "author": post.author,
        "subreddit": post.subreddit,
        "created_utc": post.created_utc.isoformat(),
        "url": post.url,
        "flair": post.flair,
        "score": post.score,
        "is_locked": post.is_locked,
        "is_deleted": post.is_deleted,
        "is_removed": post.is_removed,
        "is_archived": post.is_archived,
        "_scenario": post._scenario,
    }
    return data


async def ingest_post(
    post: NormalizedPost,
    subreddit_id: int,
    conn,  # psycopg2 connection
) -> str:
    """
    Ingest a single NormalizedPost into the pipeline.

    Returns the new status of the post:
    - "duplicate_ignored": already seen with same hash and terminal status
    - "unactionable": locked/deleted/removed/archived/empty/malformed
    - "edit_after_reply_ignored": post was edited but already replied to
    - "requeued_for_scoring": post was edited, no reply yet
    - "new": first time seeing this post

    This function writes to ``posts`` and ``events`` tables.
    It NEVER writes to ``scores``, ``replies``, or calls any LLM.
    """
    content_hash = post.content_hash()

    with conn.cursor() as cur:
        cur.execute("SELECT id, status, content_hash FROM posts WHERE id = %s", (post.id,))
        row = cur.fetchone()

    if row is None:
        # ---- New post --------------------------------------------------------
        if not post.is_actionable():
            reason = _unactionable_reason(post)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO posts"
                    " (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at)"
                    " VALUES (%s, %s, %s, %s, 'unactionable', %s, NOW())"
                    " ON CONFLICT (id) DO NOTHING",
                    (
                        post.id,
                        subreddit_id,
                        content_hash,
                        json.dumps(_post_to_dict(post)),
                        post.created_utc,
                    ),
                )
            conn.commit()
            try:
                log_event(
                    LogEvent(
                        stage=Stage.INGEST,
                        decision="unactionable",
                        reason=reason,
                        post_id=post.id,
                    ),
                    conn=conn,
                )
            finally:
                conn.commit()
            raise UnactionableContent(
                f"UnactionableContent: {reason}", post_id=post.id, reason=reason
            )

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO posts"
                " (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at)"
                " VALUES (%s, %s, %s, %s, 'seen', %s, NOW())"
                " ON CONFLICT (id) DO NOTHING",
                (
                    post.id,
                    subreddit_id,
                    content_hash,
                    json.dumps(_post_to_dict(post)),
                    post.created_utc,
                ),
            )
        conn.commit()
        try:
            log_event(
                LogEvent(stage=Stage.INGEST, decision="new", reason="first seen", post_id=post.id),
                conn=conn,
            )
        finally:
            conn.commit()
        return "new"

    # ---- Existing post -----------------------------------------------------
    existing_hash = row[2]
    existing_status = row[1]

    if existing_hash == content_hash:
        # Already processed with exactly this content — never re-queue.
        if existing_status in TERMINAL_STATUSES:
            reason = "same hash, terminal status"
        else:
            reason = "same hash, already in pipeline"
        try:
            log_event(
                LogEvent(
                    stage=Stage.INGEST,
                    decision="duplicate_ignored",
                    reason=reason,
                    post_id=post.id,
                ),
                conn=conn,
            )
        finally:
            conn.commit()
        return "duplicate_ignored"

    # ---- Hash changed: the post was edited ---------------------------------
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM replies WHERE post_id = %s", (post.id,))
        has_reply = cur.fetchone() is not None

    if has_reply:
        try:
            log_event(
                LogEvent(
                    stage=Stage.INGEST,
                    decision="edit_after_reply_ignored",
                    reason="post edited after reply already posted",
                    post_id=post.id,
                ),
                conn=conn,
            )
        finally:
            conn.commit()
        return "edit_after_reply_ignored"

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE posts"
            " SET content_hash = %s, raw_payload = %s, status = 'seen', updated_at = NOW()"
            " WHERE id = %s",
            (content_hash, json.dumps(_post_to_dict(post)), post.id),
        )
    conn.commit()
    try:
        log_event(
            LogEvent(
                stage=Stage.INGEST,
                decision="requeued_for_scoring",
                reason="post edited",
                post_id=post.id,
            ),
            conn=conn,
        )
    finally:
        conn.commit()
    return "requeued_for_scoring"


def _get_or_create_subreddit(conn, subreddit_name: str) -> int:
    """Return the id of ``subreddit_name``, creating it if missing.

    Created subreddits default to ``automation_allowed=false`` (schema default),
    per FR-9a — posting is impossible until a human confirms the rules.
    """
    with conn.cursor() as cur:
        cur.execute("INSERT INTO subreddits (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (subreddit_name,))
        cur.execute("SELECT id FROM subreddits WHERE name = %s", (subreddit_name,))
        row = cur.fetchone()
    conn.commit()
    return row[0]


async def ingest_batch(
    posts: list[NormalizedPost],
    subreddit_name: str,
    conn,
) -> dict[str, int]:
    """
    Ingest a batch of posts for a subreddit.
    Returns counts: {"new": n, "duplicate_ingnored": n, "unactionable": n, ...}
    Upserts the subreddit row if it doesn't exist (creates it with automation_allowed=false).
    """
    subreddit_id = _get_or_create_subreddit(conn, subreddit_name)
    counts: dict[str, int] = {}

    for post in posts:
        try:
            status = await ingest_post(post, subreddit_id, conn)
        except UnactionableContent:
            status = "unactionable"
        counts[status] = counts.get(status, 0) + 1

    return counts