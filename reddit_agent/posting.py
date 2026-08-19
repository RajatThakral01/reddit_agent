"""Posting gate: the most safety-critical part of the system.

Every post attempt passes through multiple independent gates, ALL of which must
pass before any public reply happens:
    1. Mode gate (DRY_RUN, never touches Reddit — structurally)
    2. Kill switch (read FRESH from the DB on every attempt, never cached)
    3. Subreddit automation policy (FR-9a)
    4. Idempotency (app-level check + DB unique constraint as the safety net)

The unique constraint on ``replies.post_id`` is the ULTIMATE idempotency
guarantee: even if the app-level logic is bypassed, the DB refuses a second row.
"""

import asyncio
import random
import time

from reddit_agent.exceptions import (
    BlockedBySubredditPolicy,
    DuplicateReplyPrevented,
    KillSwitchActive,
    RateLimitExceeded,
    RedditUnavailable,
)
from reddit_agent.models import NormalizedPost
from reddit_agent.observability import LogEvent, Stage, log_event

MAX_RETRY_DELAYS = (1, 2, 4, 8)


def _log_entry(conn, decision, post_id, reason=None, latency_ms=None, error=None):
    try:
        log_event(
            LogEvent(
                stage=Stage.POST,
                decision=decision,
                reason=reason,
                post_id=post_id,
                latency_ms=latency_ms,
                error=error,
            ),
            conn=conn,
        )
    finally:
        conn.commit()


async def post_with_retry(
    reddit_source,
    post_id: str,
    reply_text: str,
    config,
) -> str:
    """Post to Reddit with bounded retry/backoff. Returns the Reddit comment ID.

    On 429: sleep X-Ratelimit-Reset + jitter (0-2s), retry.
    On 5xx/timeout/outage: exponential backoff (1s, 2s, 4s, 8s), retry.

    After config.max_retry_attempts total attempts, raises RedditUnavailable.
    Never retries more than config.max_retry_attempts times total.
    """
    max_attempts = int(getattr(config, "max_retry_attempts", 4) or 4)
    attempt = 0

    while True:
        attempt += 1
        try:
            comment_id = await reddit_source.post_comment(post_id, reply_text)
            return comment_id
        except RateLimitExceeded as exc:
            wait = float(exc.retry_after_seconds or 0) + random.uniform(0, 2)
            reason = f"rate_limited: backing off {wait:.1f}s, attempt {attempt}/{max_attempts}"
        except (RedditUnavailable, OSError, TimeoutError) as exc:
            wait = float(MAX_RETRY_DELAYS[min(attempt - 1, len(MAX_RETRY_DELAYS) - 1)])
            reason = f"unavailable: backing off {wait}s, attempt {attempt}/{max_attempts}"

        log_event(
            LogEvent(
                stage=Stage.POST,
                decision="retry",
                reason=reason,
                post_id=post_id,
                error=None,
            ),
            conn=None,
        )

        if attempt >= max_attempts:
            raise RedditUnavailable(
                f"RedditUnavailable: post {post_id} after {attempt} attempts",
                subreddit="",
            )
        await asyncio.sleep(wait)


async def attempt_post(
    post: NormalizedPost,
    reply_text: str,
    config,
    reddit_source,  # RedditSource instance (only used in LIVE mode)
    conn,
) -> dict:
    """
    Attempt to post a reply, passing through all required gates.

    Gate order (all must pass, checked in this exact order):
    1. Mode gate: if config.mode != "LIVE" -> DRY_RUN path, never call Reddit
    2. Kill switch gate: SELECT enabled FROM kill_switch WHERE id=1
    3. Subreddit policy gate: SELECT automation_allowed FROM subreddits WHERE name=?
    4. Idempotency check: SELECT 1 FROM replies WHERE post_id = post.id

    Returns dict with status, mode, and reddit_comment_id (if LIVE+posted).
    """
    start = time.perf_counter()

    # ---- Gate 1: mode gate -----------------------------------------------------
    if config.mode != "LIVE":
        # DRY_RUN path: structurally never touches reddit_source.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO replies (post_id, reply_text, mode, status)"
                " VALUES (%s, %s, 'dry_run', 'simulated')",
                (post.id, reply_text),
            )
        conn.commit()
        _log_entry(conn, "simulated", post.id, reason="dry run, no reddit call")
        return {"status": "simulated", "mode": "dry_run"}

    # ---- Gate 2: KILL SWITCH (read FRESH every attempt, never cached) ----------
    with conn.cursor() as cur:
        cur.execute("SELECT enabled FROM kill_switch WHERE id=1")
        row = cur.fetchone()
    if row is not None and row[0]:
        _log_entry(conn, "kill_switch_blocked", post.id, reason="KillSwitchActive")
        raise KillSwitchActive(
            f"KillSwitchActive: post suppressed for item {post.id}",
            post_id=post.id,
        )

    # ---- Gate 3: SUBREDDIT POLICY (FR-9a) --------------------------------------
    with conn.cursor() as cur:
        cur.execute("SELECT automation_allowed FROM subreddits WHERE name=%s", (post.subreddit,))
        row = cur.fetchone()
    if row is None or not row[0]:
        _log_entry(conn, "subreddit_policy_blocked", post.id, reason="automation not allowed")
        raise BlockedBySubredditPolicy(
            f"BlockedBySubredditPolicy: subreddit {post.subreddit} automation not confirmed allowed",
            subreddit=post.subreddit,
            post_id=post.id,
        )

    # ---- Gate 4: IDEMPOTENCY (app-level check) ---------------------------------
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM replies WHERE post_id = %s", (post.id,))
        exists = cur.fetchone() is not None
    if exists:
        _log_entry(conn, "duplicate", post.id, reason="DuplicateReplyPrevented")
        raise DuplicateReplyPrevented(
            f"DuplicateReplyPrevented: post {post.id} already replied",
            post_id=post.id,
        )

    # ---- LIVE path: transactional insert (DB constraint is the safety net) ----
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO replies (post_id, reply_text, mode, status)"
            " VALUES (%s, %s, 'live', 'failed')"
            " ON CONFLICT (post_id) DO NOTHING RETURNING id",
            (post.id, reply_text),
        )
        inserted = cur.fetchone()
    conn.commit()

    if inserted is None:
        # The DB unique constraint (not just our app check) prevented a duplicate.
        _log_entry(conn, "duplicate", post.id, reason="DatabaseUniqueConstraint")
        return {"status": "duplicate", "mode": "live"}

    reply_id = inserted[0]

    try:
        comment_id = await post_with_retry(reddit_source, post.id, reply_text, config)
    except Exception as exc:
        with conn.cursor() as cur:
            cur.execute("UPDATE replies SET status='failed' WHERE id=%s", (reply_id,))
        conn.commit()
        _log_entry(conn, "failed", post.id, reason="post failed", error=str(exc))
        raise

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE replies SET reddit_comment_id=%s, status='posted' WHERE id=%s",
            (comment_id, reply_id),
        )
    conn.commit()

    latency_ms = int((time.perf_counter() - start) * 1000)
    _log_entry(conn, "posted", post.id, reason="live post confirmed", latency_ms=latency_ms)
    return {"status": "posted", "mode": "live", "reddit_comment_id": comment_id}