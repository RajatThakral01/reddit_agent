import datetime as dt
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg2
import pytest

from reddit_agent.cost_tracker import CostTracker
from reddit_agent.db.connection import run_migrations
from reddit_agent.exceptions import CostCapExceeded, GenerationFailed
from reddit_agent.models import NormalizedPost
from reddit_agent.scoring import POLICY_VERSION, pre_filter, score_post

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)


def _make_post(**overrides) -> NormalizedPost:
    base = dict(
        id="t3_score001",
        title="GPU won't boot after driver update",
        body="Fresh install of the driver and now the screen stays black, fans spin at max speed and it keeps crashing.",
        author="score_user",
        subreddit="techsupport",
        created_utc=NOW,
        url="https://reddit.com/r/techsupport/comments/score001",
        flair=None,
        score=5,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    id_value = overrides.get("id", "t3_score001")
    base["id"] = id_value
    return NormalizedPost(**base)


def _config(**overrides):
    cfg = dict(
        keywords=["not working", "error", "crash", "blue screen"],
        worthiness_threshold=65,
        llm_api_key="test-key",
        llm_model="deepseek-chat",
        llm_cost_cap_usd=0.50,
        max_age_hours=48,
    )
    cfg.update(overrides)
    return SimpleNamespace(**cfg)


def _valid_llm_result():
    return {
        "score": 88,
        "reason": "Clear GPU boot failure with specific symptoms",
        "confidence": "high",
        "policy_version": POLICY_VERSION,
        "factors": {
            "relevance": 0.95,
            "problem_clarity": 0.9,
            "user_intent": "seeking_help",
            "recency_minutes": 25,
            "risk": "low",
            "answerable": True,
        },
    }, {"prompt_tokens": 300, "completion_tokens": 90, "total_tokens": 390}


# --- Pre-filter --------------------------------------------------------------


def test_pre_filter_fails_no_keyword():
    post = _make_post(
        id="t3_pf01",
        title="totally unrelated rambling about breakfast foods",
        body="A long discussion about cereal, pancakes and toast that has nothing to do with computers.",
    )
    passes, reason = pre_filter(post, _config())
    assert passes is False
    assert reason == "no_keyword_match"


def test_pre_filter_fails_solved_flair():
    post = _make_post(
        id="t3_pf02",
        flair="[Solved]",
        title="Not working GPU",
        body="The GPU is not working and I need help fixing it.",
    )
    passes, reason = pre_filter(post, _config())
    assert passes is False
    assert reason == "disallowed_flair"


def test_pre_filter_fails_body_too_short():
    # "got an error now" = 16 chars (< MIN_BODY_LENGTH) but still matches a keyword
    post = _make_post(id="t3_pf03", body="got an error now")
    passes, reason = pre_filter(post, _config())
    assert passes is False
    assert reason == "body_too_short"


def test_pre_filter_fails_post_too_old():
    old = NOW - dt.timedelta(hours=100)
    post = _make_post(id="t3_pf04", created_utc=old)
    passes, reason = pre_filter(post, _config())
    assert passes is False
    assert reason == "post_too_old"


def test_pre_filter_passes_valid_post():
    passes, reason = pre_filter(_make_post(), _config())
    assert passes is True
    assert reason == ""


# --- LLM scorer ---------------------------------------------------------------


def _conn():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
        cur.execute("INSERT INTO subreddits (name) VALUES ('techsupport')")
    return conn


def _insert_post(conn, post_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO posts (id, subreddit_id, content_hash, raw_payload, status, created_utc, updated_at)"
            " VALUES (%s, 1, 'h', '{}', 'seen', NOW(), NOW())",
            (post_id,),
        )


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_score_post_returns_valid_structure(mocker):
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=_valid_llm_result())
    conn = _conn()
    try:
        _insert_post(conn, "t3_score001")
        result = await score_post(_make_post(), _config(), conn)
        assert isinstance(result["score"], int)
        assert result["policy_version"] == POLICY_VERSION
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM posts WHERE id='t3_score001'")
            assert cur.fetchone()[0] == "scored"
            cur.execute("SELECT COUNT(*) FROM scores WHERE post_id='t3_score001'")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_score_post_all_factors_present(mocker):
    result, usage = _valid_llm_result()
    mocker.patch("reddit_agent.scoring._chat_completion", return_value=(result, usage))
    conn = _conn()
    try:
        _insert_post(conn, "t3_score001")
        result = await score_post(_make_post(), _config(), conn)
        assert set(result["factors"].keys()) == {
            "relevance",
            "problem_clarity",
            "user_intent",
            "recency_minutes",
            "risk",
            "answerable",
        }
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_score_is_deterministic(mocker):
    conn = _conn()
    try:
        mocker.patch("reddit_agent.scoring._chat_completion", return_value=_valid_llm_result())
        for i in range(2):
            _insert_post(conn, f"t3_score0{i}")
            post = _make_post(id=f"t3_score0{i}")
            await score_post(post, _config(), conn)
        with conn.cursor() as cur:
            cur.execute("SELECT score, reason, confidence FROM scores ORDER BY id")
            rows = cur.fetchall()
        assert rows[0][0] == rows[1][0]
        assert rows[0] == rows[1]
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")
async def test_malformed_llm_response_retries_once_then_raises(mocker):
    async def broken(payload, api_key):
        return {"not": "valid"}, {}

    mocker.patch("reddit_agent.scoring._chat_completion", side_effect=broken)
    conn = _conn()
    try:
        with pytest.raises(GenerationFailed):
            await score_post(_make_post(), _config(), conn)
    finally:
        conn.close()


def test_cost_cap_exceeded_raises():
    tracker = CostTracker(cap_usd=1e-6)
    tracker.add(0.0000005)
    with pytest.raises(CostCapExceeded):
        tracker.add(0.0000009)


def test_cost_tracker_summary():
    tracker = CostTracker(cap_usd=0.50)
    tracker.add(0.01, tokens_used=100)
    summary = tracker.summary()
    assert summary["total_usd"] == pytest.approx(0.01)
    assert summary["call_count"] == 1
    assert summary["cap_usd"] == 0.50


def test_pre_filter_skip_does_not_call_llm(mocker):
    called = {"count": 0}

    async def fake_chat(payload, api_key):
        called["count"] += 1
        return _valid_llm_result()

    mocker.patch("reddit_agent.scoring._chat_completion", fake_chat)
    bad_post = _make_post(
        id="t3_pfskip",
        title="totally unrelated ramen and noodles discussion",
        body="A long post about soup recipes, noodle brands and restaurant recommendations.",
    )
    passes, reason = pre_filter(bad_post, _config())
    assert passes is False
    assert reason == "no_keyword_match"
    assert called["count"] == 0