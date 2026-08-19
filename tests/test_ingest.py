import os
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from reddit_agent.db.connection import run_migrations
from reddit_agent.exceptions import UnactionableContent
from reddit_agent.ingest import ingest_batch, ingest_post
from reddit_agent.models import NormalizedPost

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.asyncio


def _make_post(**overrides) -> NormalizedPost:
    base = dict(
        id="t3_ing001",
        title="PC won't boot after update",
        body="It stops at the logo screen with a spinning circle and never gets to the desktop.",
        author="ingest_user",
        subreddit="techsupport",
        created_utc=NOW,
        url="https://reddit.com/r/techsupport/comments/ing001",
        flair=None,
        score=4,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    return NormalizedPost(**base)


def _conn():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
        cur.execute("INSERT INTO subreddits (name) VALUES ('techsupport')")
    return conn


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set — requires a real Postgres test DB",
)
async def test_new_post_stored_with_status_seen():
    conn = _conn()
    try:
        status = await ingest_post(_make_post(), subreddit_id=1, conn=conn)
        assert status == "new"
        with conn.cursor() as cur:
            cur.execute("SELECT status, content_hash FROM posts WHERE id='t3_ing001'")
            row = cur.fetchone()
        assert row[0] == "seen"
        assert row[1] == _make_post().content_hash()
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_duplicate_same_hash_returns_duplicate_ignored():
    conn = _conn()
    try:
        posts = [_make_post(), _make_post()]
        counts = await ingest_batch(posts, "techsupport", conn)
        assert counts["new"] == 1
        assert counts["duplicate_ignored"] == 1
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM posts WHERE id='t3_ing001'")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_locked_post_returns_unactionable():
    conn = _conn()
    try:
        post = _make_post(id="t3_lock01", is_locked=True)
        with pytest.raises(UnactionableContent) as exc_info:
            await ingest_post(post, subreddit_id=1, conn=conn)
        assert exc_info.value.reason == "locked"
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM posts WHERE id='t3_lock01'")
            assert cur.fetchone()[0] == "unactionable"
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_deleted_post_returns_unactionable():
    conn = _conn()
    try:
        post = _make_post(id="t3_del01", is_deleted=True)
        with pytest.raises(UnactionableContent) as exc_info:
            await ingest_post(post, subreddit_id=1, conn=conn)
        assert exc_info.value.reason == "deleted"
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_empty_body_returns_unactionable():
    conn = _conn()
    try:
        with pytest.raises(UnactionableContent) as exc_info:
            await ingest_post(_make_post(id="t3_empty01", body=""), subreddit_id=1, conn=conn)
        assert exc_info.value.reason == "empty_body"
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_edit_no_reply_requeues_for_scoring():
    conn = _conn()
    try:
        original = _make_post()
        edited = _make_post(
            body="Update: I removed the GPU and it boots fine, so I think the graphics card is failing."
        )
        assert (await ingest_post(original, subreddit_id=1, conn=conn)) == "new"
        status = await ingest_post(edited, subreddit_id=1, conn=conn)
        assert status == "requeued_for_scoring"
        with conn.cursor() as cur:
            cur.execute("SELECT status, content_hash FROM posts WHERE id='t3_ing001'")
            row = cur.fetchone()
        assert row[0] == "seen"
        assert row[1] == edited.content_hash()
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_edit_after_reply_ignored():
    conn = _conn()
    try:
        original = _make_post()
        assert (await ingest_post(original, subreddit_id=1, conn=conn)) == "new"
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM subreddits LIMIT 1")
            sub_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO replies (post_id, reply_text, mode, status) VALUES (%s, 'x', 'dry_run', 'simulated')",
                (original.id,),
            )
        edited = _make_post(body="changed content after editing")
        status = await ingest_post(edited, subreddit_id=1, conn=conn)
        assert status == "edit_after_reply_ignored"
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_ingest_batch_creates_subreddit_if_missing():
    conn = _conn()
    try:
        await ingest_batch([_make_post()], "brandnewsub", conn)
        with conn.cursor() as cur:
            cur.execute("SELECT automation_allowed FROM subreddits WHERE name='brandnewsub'")
            row = cur.fetchone()
        assert row is not None
        assert row[0] is False
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_ingest_batch_returns_correct_counts():
    conn = _conn()
    try:
        posts = [
            _make_post(id="t3_mix01"),
            _make_post(id="t3_mix01"),
            _make_post(id="t3_mix02", is_locked=True),
            _make_post(id="t3_mix03", body=""),
        ]
        counts = await ingest_batch(posts, "mixedsub", conn)
        assert counts["new"] == 1
        assert counts["unactionable"] == 2
        assert counts["duplicate_ignored"] == 1
    finally:
        conn.close()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_all_events_logged():
    conn = _conn()
    try:
        posts = [
            _make_post(id="t3_ev01"),
            _make_post(id="t3_ev02", body="unique second post"),
            _make_post(id="t3_ev03", is_removed=True),
        ]
        await ingest_batch(posts, "eventsub", conn)
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT post_id FROM events WHERE stage = 'INGEST'")
            logged = {row[0] for row in cur.fetchall()}
        assert logged == {"t3_ev01", "t3_ev02", "t3_ev03"}
    finally:
        conn.close()