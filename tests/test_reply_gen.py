import os
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg2
import pytest

from reddit_agent.cost_tracker import CostTracker
from reddit_agent.db.connection import run_migrations
from reddit_agent.exceptions import GuardrailBlocked
from reddit_agent.guardrails import SAFETY_FIRST_REPLY
from reddit_agent.models import NormalizedPost
from reddit_agent.reply_gen import generate_reply

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.asyncio


def _post(**overrides) -> NormalizedPost:
    base = dict(
        id="t3_rg001",
        title="GPU fan noise after driver update",
        body="After updating the driver my graphics card fan runs at full speed even at idle, temperatures are normal.",
        author="reply_user",
        subreddit="techsupport",
        created_utc=NOW,
        url="https://reddit.com/r/techsupport/comments/rg001",
        flair=None,
        score=4,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    return NormalizedPost(**base)


def _score_result(**factors_overrides):
    factors = {
        "relevance": 0.9,
        "problem_clarity": 0.85,
        "user_intent": "seeking_help",
        "recency_minutes": 30,
        "risk": "low",
        "answerable": True,
    }
    factors.update(factors_overrides)
    return {
        "score": 85,
        "reason": "clear problem",
        "confidence": "high",
        "policy_version": "v1.0",
        "factors": factors,
    }


def _config(**overrides):
    cfg = dict(
        llm_api_key="test-key",
        llm_model="deepseek-chat",
        llm_cost_cap_usd=0.50,
    )
    cfg.update(overrides)
    return SimpleNamespace(**cfg)


def _conn():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
        cur.execute("INSERT INTO subreddits (name) VALUES ('techsupport')")
    return conn


def _insert_post(conn, post_id="t3_rg001"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO posts (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at)"
            " VALUES (%s, 1, 'h', '{}', 'scored', NOW(), NOW())",
            (post_id,),
        )


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_generates_troubleshooting_for_clear_post(mocker):
    reply = "Try reseating the GPU and reinstalling the driver in safe mode to isolate the fan issue."
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=(reply, {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}),
    )
    conn = _conn()
    try:
        _insert_post(conn, "t3_rg001")
        result = await generate_reply(_post(), _score_result(), _config(), CostTracker(0.5), conn)
        assert result == reply
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_generates_clarifying_question_for_ambiguous_post(mocker):
    question = "What laptop model and operating system are you using?"
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=(question, {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}),
    )
    conn = _conn()
    try:
        _insert_post(conn, "t3_rg001")
        result = await generate_reply(
            _post(),
            _score_result(problem_clarity=0.2, answerable=False),
            _config(),
            CostTracker(0.5),
            conn,
        )
        assert result == question
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_guardrail_block_raises_exception(mocker):
    bad_reply = "This will definitely fix it: format your drive and reinstall everything."
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=(bad_reply, {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}),
    )
    conn = _conn()
    try:
        _insert_post(conn, "t3_rg001")
        with pytest.raises(GuardrailBlocked):
            await generate_reply(_post(), _score_result(), _config(), CostTracker(0.5), conn)
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM posts WHERE id='t3_rg001'")
            assert cur.fetchone()[0] == "blocked_by_guardrail"
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_hazard_post_gets_safety_reply_not_diagnosis(mocker):
    hazard_post = _post(
        id="t3_riskr",
        body="My laptop has a burning smell and white smoke is coming from the vents.",
    )
    diagnosis = "Open the case and check the fan for debris."
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=(diagnosis, {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}),
    )
    conn = _conn()
    try:
        _insert_post(conn, "t3_riskr")
        result = await generate_reply(hazard_post, _score_result(), _config(), CostTracker(0.5), conn)
        assert result == SAFETY_FIRST_REPLY
        assert "vent" not in result.lower()
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_reply_under_200_words(mocker):
    long_reply = " ".join(["Check"] * 150)  # exactly 150 words
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=(long_reply, {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500}),
    )
    conn = _conn()
    try:
        _insert_post(conn, "t3_rg001")
        result = await generate_reply(_post(), _score_result(), _config(), CostTracker(0.5), conn)
        assert len(result.split()) <= 200
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_cost_tracked_for_generation_call(mocker):
    mocker.patch(
        "reddit_agent.reply_gen._chat_completion",
        return_value=(
            "A short safe troubleshooting reply.",
            {"prompt_tokens": 400, "completion_tokens": 50, "total_tokens": 450},
        ),
    )
    tracker = CostTracker(cap_usd=0.50)
    conn = _conn()
    try:
        _insert_post(conn, "t3_rg001")
        await generate_reply(_post(), _score_result(), _config(), tracker, conn)
        assert tracker.call_count == 1
        assert tracker.total_usd > 0
    finally:
        conn.close()