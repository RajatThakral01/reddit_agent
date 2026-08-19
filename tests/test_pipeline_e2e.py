import os
from datetime import datetime, timezone

import psycopg2
import pytest

from reddit_agent.config import AgentConfig
from reddit_agent.cost_tracker import CostTracker
from reddit_agent.db.connection import run_migrations
from reddit_agent.exceptions import ConfigError, CostCapExceeded
from reddit_agent.guardrails import SAFETY_FIRST_REPLY
from reddit_agent.models import NormalizedPost
from reddit_agent.pipeline import run_pipeline_for_post, run_poll_cycle
from reddit_agent.sources.test_source import TestRedditSource

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

KEYWORDS = [
    "help",
    "error",
    "crash",
    "boot",
    "bios",
    "driver",
    "wifi",
    "ssd",
    "ram",
    "battery",
    "smell",
    "shock",
    "login",
    "won't",
    "overheating",
    "temperature",
]

# NOTE: keyword set is intentionally narrow so the negative fixtures (news,
# promos, memes, polls) never match the pre-filter.


def _make_post(**overrides) -> NormalizedPost:
    base = dict(
        id="t3_e2e001",
        title="PC suddenly crashes while gaming",
        body="My PC keeps crashing during demanding games and I already updated the driver.",
        author="user_e2e",
        subreddit="techsupport",
        created_utc=datetime(2026, 8, 20, 1, 0, 0, tzinfo=timezone.utc),
        url="https://reddit.com/r/techsupport/comments/e2e001",
        flair=None,
        score=3,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    return NormalizedPost(**base)


class CountingSource(TestRedditSource):
    """TestRedditSource that also counts post_comment calls."""

    def __init__(self, categories):
        super().__init__(fixture_categories=categories)
        self.post_calls = 0

    async def post_comment(self, post_id, body):
        self.post_calls += 1
        raise AssertionError("post_comment must never be called in DRY_RUN e2e tests")


class StubPostingSource(TestRedditSource):
    """TestRedditSource that CAN post (returns a fake comment id)."""

    def __init__(self):
        super().__init__(fixture_categories=[])
        self.post_calls = 0

    async def post_comment(self, post_id, body):
        self.post_calls += 1
        return f"t1_{post_id}"


def _config(**overrides):
    base = dict(
        subreddits=["techsupport"],
        keywords=KEYWORDS,
        worthiness_threshold=50,
        poll_interval_seconds=1,
        mode="DRY_RUN",
        reddit_source="test",
        llm_provider="deepseek",
        llm_api_key="sk-test",
        llm_model="deepseek-chat",
        llm_cost_cap_usd=0.05,
        max_retry_attempts=2,
    )
    base.update(overrides)
    return AgentConfig(**base)


def _score(**factor_overrides):
    factors = dict(
        relevance=0.7,
        problem_clarity=0.8,
        user_intent="seeking_help",
        recency_minutes=30,
        risk="low",
        answerable=True,
    )
    factors.update(factor_overrides)
    return (
        {
            "score": 87,
            "reason": "clear tech problem seeking help",
            "confidence": "high",
            "policy_version": "v1.0",
            "factors": factors,
        },
        {"prompt_tokens": 300, "completion_tokens": 90, "total_tokens": 390},
    )


def _reply(text="Try reseating the cable and rebooting in safe mode."):
    return (text, {"prompt_tokens": 150, "completion_tokens": 40, "total_tokens": 190})


def _conn(with_subreddit=True):
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
        if with_subreddit:
            cur.execute("INSERT INTO subreddits (name, automation_allowed) VALUES ('test', TRUE)")
    return conn


# --- Positive path ----------------------------------------------------------------


async def test_e2e_positive_post_dry_run_simulated(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply())
    conn = _conn()
    source = CountingSource(["positive"])
    config = _config(subreddits=["techsupport"])
    try:
        summary = await run_poll_cycle(config, source, CostTracker(0.05), conn)
        assert summary["simulated"] == 5
        assert summary["errors"] == 0
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM posts WHERE id='t3_pos001'")
            assert cur.fetchone()[0] == "replied"
            cur.execute("SELECT mode, status FROM replies WHERE post_id='t3_pos001'")
            assert cur.fetchone() == ("dry_run", "simulated")
            cur.execute("SELECT DISTINCT stage FROM events WHERE post_id='t3_pos001'")
            stages = {r[0] for r in cur.fetchall()}
        assert {"INGEST", "SCORE", "GENERATE", "GUARDRAIL", "POST"} <= stages
        assert source.post_calls == 0
    finally:
        conn.close()


async def test_e2e_negative_post_skipped(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply())
    conn = _conn()
    source = CountingSource(["negative"])
    try:
        summary = await run_poll_cycle(_config(), source, CostTracker(0.05), conn)
        assert summary["simulated"] == 0
        assert summary["posted"] == 0
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT status FROM posts WHERE id LIKE 't3_neg%'")
            statuses = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM replies")
            assert cur.fetchone()[0] == 0
        assert statuses <= {"skipped", "unactionable"}
    finally:
        conn.close()


async def test_e2e_ambiguous_gets_clarifying_question(mocker):
    mocker.patch(
        "reddit_agent.scoring._chat_completion",
        return_value=_score(problem_clarity=0.1, answerable=False),
    )
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=_reply("What exact device model and OS version are you using?"),
    )
    conn = _conn()
    source = CountingSource(["ambiguous"])
    try:
        await run_poll_cycle(_config(), source, CostTracker(0.05), conn)
        with conn.cursor() as cur:
            cur.execute("SELECT reply_text FROM replies WHERE post_id='t3_amb003'")
            row = cur.fetchone()
        assert row is not None
        assert "?" in row[0]
    finally:
        conn.close()


async def test_e2e_hazard_post_gets_safety_reply(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply("Open the case and check the fan."))
    conn = _conn()
    source = CountingSource(["risk"])
    try:
        await run_poll_cycle(_config(), source, CostTracker(0.05), conn)
        with conn.cursor() as cur:
            cur.execute("SELECT reply_text FROM replies WHERE post_id='t3_risk001'")
            row = cur.fetchone()
        assert row is not None
        assert row[0] == SAFETY_FIRST_REPLY
    finally:
        conn.close()


async def test_e2e_locked_post_unactionable(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply())
    conn = _conn()
    source = CountingSource(["edge_cases"])
    try:
        await run_poll_cycle(_config(), source, CostTracker(0.05), conn)
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM posts WHERE id='t3_edge001'")
            assert cur.fetchone()[0] == "unactionable"
            cur.execute("SELECT COUNT(*) FROM replies WHERE post_id='t3_edge001'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM scores WHERE post_id='t3_edge001'")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


async def test_e2e_duplicate_only_one_reply(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply())
    conn = _conn()
    source = CountingSource(["duplicate"])
    post = _make_post(id="t3_dup001", subreddit="test")
    try:
        first = await run_pipeline_for_post(post, "test", _config(), source, CostTracker(0.05), conn)
        assert first["final_status"] in ("simulated", "posted")
        second = await run_pipeline_for_post(post, "test", _config(), source, CostTracker(0.05), conn)
        assert second["final_status"] == "duplicate"
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM replies WHERE post_id='t3_dup001'")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


# --- Config / gates ---------------------------------------------------------------


async def test_e2e_6_subreddits_config_error():
    import os as os_mod

    old = os_mod.environ.get("SUBREDDITS")
    os_mod.environ["SUBREDDITS"] = "a,b,c,d,e,f"
    try:
        with pytest.raises(ConfigError):
            AgentConfig()
    finally:
        if old is None:
            os_mod.environ.pop("SUBREDDITS", None)
        else:
            os_mod.environ["SUBREDDITS"] = old


async def test_e2e_kill_switch_stops_new_posts(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply())
    conn = _conn()
    config = _config(mode="LIVE")
    try:
        post_a = _make_post(id="post_kill_a", subreddit="test")
        post_b = _make_post(id="post_kill_b", subreddit="test")

        with conn.cursor() as cur:
            cur.execute("INSERT INTO posts (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at) VALUES (%s, %s, 'h', '{}', 'seen', NOW(), NOW())", (post_a.id, 1))
        first = await run_pipeline_for_post(post_a, "test", _config(mode="LIVE"), StubPostingSource(), CostTracker(0.05), conn)
        assert first["final_status"] == "posted"

        with conn.cursor() as cur:
            cur.execute("UPDATE kill_switch SET enabled=TRUE WHERE id=1")
            cur.execute("INSERT INTO posts (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at) VALUES (%s, 1, 'h', '{}', 'seen', NOW(), NOW())", (post_b.id,))

        second = await run_pipeline_for_post(post_b, "test", _config(mode="LIVE"), StubPostingSource(), CostTracker(0.05), conn)
        assert second["final_status"] == "post_blocked"

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM replies WHERE post_id='post_kill_b'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM events WHERE decision='post_blocked' OR reason='KillSwitchActive'")
            assert cur.fetchone()[0] >= 1
    finally:
        conn.close()


async def test_e2e_cost_cap_aborts_run(mocker):
    called = {"n": 0}

    async def limited_score(payload, api_key):
        called["n"] += 1
        return _score()

    mocker.patch("reddit_agent.scoring._chat_completion", side_effect=limited_score)
    conn = _conn()
    source = CountingSource(["positive"])
    try:
        config = _config(subreddits=["techsupport"], llm_cost_cap_usd=0.000001)
        with pytest.raises(CostCapExceeded):
            await run_poll_cycle(config, source, CostTracker(0.000001), conn)
        assert called["n"] == 1
    finally:
        conn.close()


async def test_e2e_no_network_in_test_mode(mocker):
    connects = {"n": 0}

    def blocking_connect(self_addr, address):
        connects["n"] += 1
        raise AssertionError("network call attempted")

    mocker.patch("socket.socket.connect", blocking_connect)
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_score())
    mocker.patch("reddit_agent.reply_gen._chat_completion", return_value=_reply())
    conn = _conn()
    source = CountingSource(["positive"])
    try:
        await run_poll_cycle(_config(reddit_source="test", subreddits=["techsupport"]), source, CostTracker(0.05), conn)
        assert connects["n"] == 0
    finally:
        conn.close()