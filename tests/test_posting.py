import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg2
import pytest

from reddit_agent.db.connection import run_migrations
from reddit_agent.exceptions import (
    BlockedBySubredditPolicy,
    DuplicateReplyPrevented,
    KillSwitchActive,
    RateLimitExceeded,
    RedditUnavailable,
)
from reddit_agent.models import NormalizedPost
from reddit_agent.posting import attempt_post, post_with_retry

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.asyncio


def _post(**overrides) -> NormalizedPost:
    base = dict(
        id="t3_post001",
        title="WiFi keeps dropping",
        body="My wifi disconnects every ten minutes and I have tried restarting the router.",
        author="post_user",
        subreddit="techsupport",
        created_utc=datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc),
        url="https://reddit.com/r/techsupport/comments/post001",
        flair=None,
        score=3,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    return NormalizedPost(**base)


def _config(**overrides):
    cfg = dict(mode="DRY_RUN", max_retry_attempts=2)
    cfg.update(overrides)
    return SimpleNamespace(**cfg)


class _Source:
    """Fake RedditSource whose post_comment can be customized per test."""

    def __init__(self, comment_id="t1_xyz", error_factory=None):
        self.comment_id = comment_id
        self.error_factory = error_factory  # callable(n) -> Exception to raise
        self.calls = 0
        self.last_post_id = None
        self.last_text = None

    async def post_comment(self, post_id, body):
        self.calls += 1
        self.last_post_id = post_id
        self.last_text = body
        if self.error_factory is not None:
            error = self.error_factory(self.calls)
            if error is not None:
                raise error
        return self.comment_id


def _seed_post(conn, post: NormalizedPost):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO posts (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at)"
            " SELECT %s, id, 'h', '{}', 'scored', NOW(), NOW() FROM subreddits WHERE name=%s",
            (post.id, post.subreddit),
        )


def _conn(auto_allow=True):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
        cur.execute(
            "INSERT INTO subreddits (name, automation_allowed) VALUES ('techsupport', %s)",
            (auto_allow,),
        )
    return conn


# --- DRY_RUN -----------------------------------------------------------------


async def test_dry_run_writes_simulated_status():
    conn = _conn()
    post = _post()
    _seed_post(conn, post)
    try:
        result = await attempt_post(post, "hello world", _config(), _Source(), conn)
        assert result == {"status": "simulated", "mode": "dry_run"}
        with conn.cursor() as cur:
            cur.execute("SELECT mode, status FROM replies WHERE post_id='t3_post001'")
            row = cur.fetchone()
        assert row == ("dry_run", "simulated")
    finally:
        conn.close()


async def test_dry_run_never_calls_reddit_api():
    conn = _conn()
    post = _post()
    _seed_post(conn, post)
    hitting_source = _Source(error_factory=lambda n: AssertionError("reddit called in dry run"))
    try:
        result = await attempt_post(_post(), "test", _config(), hitting_source, conn)
        assert result["status"] == "simulated"
        assert hitting_source.calls == 0
    finally:
        conn.close()


# --- Kill switch ---------------------------------------------------------------


async def test_kill_switch_blocks_live_post():
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE kill_switch SET enabled=TRUE WHERE id=1")
    try:
        with pytest.raises(KillSwitchActive):
            await attempt_post(_post(), "test", _config(mode="LIVE"), _Source(), conn)
    finally:
        conn.close()


async def test_kill_switch_checked_fresh():
    conn = _conn()
    _seed_post(conn, _post())
    _seed_post(conn, _post(id="t3_new01"))
    source = _Source()
    try:
        # Disabled at first -> posts through.
        result = await attempt_post(_post(), "first", _config(mode="LIVE"), source, conn)
        assert result["status"] == "posted"
        # Enabled AFTER config was loaded -> must still be caught on next attempt.
        with conn.cursor() as cur:
            cur.execute("UPDATE kill_switch SET enabled=TRUE WHERE id=1")
        with pytest.raises(KillSwitchActive):
            await attempt_post(_post(id="t3_new01"), "second", _config(mode="LIVE"), source, conn)
    finally:
        conn.close()


# --- Subreddit policy -----------------------------------------------------------


async def test_automation_not_allowed_blocks_post():
    conn = _conn(auto_allow=False)
    try:
        with pytest.raises(BlockedBySubredditPolicy):
            await attempt_post(_post(), "reply", _config(mode="LIVE"), _Source(), conn)
    finally:
        conn.close()


# --- Idempotency ----------------------------------------------------------------


async def test_idempotency_duplicate_reply_prevented():
    conn = _conn()
    _seed_post(conn, _post())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO replies (post_id, reply_text, mode, status) VALUES ('t3_post001', 'x', 'live', 'posted')"
            )
        with pytest.raises(DuplicateReplyPrevented):
            await attempt_post(_post(), "dup reply", _config(mode="LIVE"), _Source(), conn)
    finally:
        conn.close()


async def test_idempotency_survives_process_restart():
    conn = _conn()
    _seed_post(conn, _post())
    source = _Source()
    try:
        # First "process" posts successfully.
        first = await attempt_post(_post(), "reply", _config(mode="LIVE"), source, conn)
        assert first["status"] == "posted"
        assert source.calls == 1
        # Second "process" re-runs the same ingestion -> duplicate, no new post.
        with pytest.raises(DuplicateReplyPrevented):
            await attempt_post(_post(), "reply again", _config(mode="LIVE"), source, conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM replies WHERE post_id='t3_post001'")
            count = cur.fetchone()[0]
        assert count == 1
        assert source.calls == 1
    finally:
        conn.close()


# --- LIVE posting ----------------------------------------------------------------


async def test_live_post_stores_comment_id():
    conn = _conn()
    _seed_post(conn, _post())
    source = _Source(comment_id="t1_commentxy")
    try:
        result = await attempt_post(_post(), "reply", _config(mode="LIVE"), source, conn)
        assert result == {"status": "posted", "mode": "live", "reddit_comment_id": "t1_commentxy"}
        with conn.cursor() as cur:
            cur.execute("SELECT reddit_comment_id, status FROM replies WHERE post_id='t3_post001'")
            row = cur.fetchone()
        assert row == ("t1_commentxy", "posted")
    finally:
        conn.close()


async def test_429_triggers_backoff(mocker):
    conn = _conn()
    _seed_post(conn, _post())

    def flaky(n):
        if n == 1:
            return RateLimitExceeded("429", retry_after_seconds=0, attempt=1, max_attempts=2)
        return None

    source = _Source(error_factory=flaky)
    sleep = mocker.patch("reddit_agent.posting.asyncio.sleep", new_callable=AsyncMock)
    try:
        result = await attempt_post(_post(), "reply", _config(mode="LIVE"), source, conn)
        assert result["status"] == "posted"
        assert source.calls == 2
        assert sleep.await_count >= 1
    finally:
        conn.close()


async def test_max_retries_raises_reddit_unavailable(mocker):
    async def noop_sleep(*args, **kwargs):
        return None

    source = _Source(error_factory=lambda n: RedditUnavailable("5xx", subreddit="x"))
    mocker.patch("reddit_agent.posting.asyncio.sleep", noop_sleep)
    with pytest.raises(RedditUnavailable):
        await post_with_retry(source, "t3_x", "reply", _config(max_retry_attempts=2))