import json
import os

import psycopg2
import pytest

from reddit_agent.db.connection import run_migrations
from reddit_agent.observability import LogEvent, Stage, log_event, timed_stage

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _capture_log(event: LogEvent, conn=None) -> dict:
    import io
    import sys
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        log_event(event, conn=conn)
    return json.loads(buf.getvalue())


def test_log_event_writes_to_stdout():
    out = _capture_log(LogEvent(stage=Stage.INGEST, decision="seen", post_id="t3_abc"))
    assert out["stage"] == "INGEST"
    assert out["decision"] == "seen"
    assert out["post_id"] == "t3_abc"
    assert "timestamp" in out


def test_log_event_writes_to_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
    try:
        log_event(LogEvent(stage=Stage.SCORE, decision="scored", post_id="t3_db"), conn=conn)
        with conn.cursor() as cur:
            cur.execute("SELECT post_id, stage, decision FROM events WHERE post_id='t3_db'")
            row = cur.fetchone()
        assert row is not None
        assert row[1] == "SCORE"
        assert row[2] == "scored"
    finally:
        conn.close()


def test_log_event_redacts_api_key_in_extra():
    out = _capture_log(
        LogEvent(stage=Stage.SYSTEM, extra={"api_key": "sk-real", "iterations": 3})
    )
    assert out["extra"]["api_key"] == "[REDACTED]"
    assert "sk-real" not in json.dumps(out)


def test_log_event_redacts_username_in_reason():
    out = _capture_log(
        LogEvent(stage=Stage.INGEST, reason="posted by u/actualuser about a GPU")
    )
    assert "u/[username]" in out["reason"]
    assert "u/actualuser" not in out["reason"]


def test_timed_stage_logs_latency():
    import io
    import sys
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        with timed_stage(Stage.GENERATE, post_id="t3_abc"):
            pass
    out = json.loads(buf.getvalue())
    assert out["latency_ms"] is not None
    assert out["latency_ms"] >= 0


def test_timed_stage_logs_error_on_exception():
    import io
    import sys
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(RuntimeError):
            with timed_stage(Stage.POST, post_id="t3_abc"):
                raise RuntimeError("boom")
    out = json.loads(buf.getvalue())
    assert out["stage"] == "POST"
    assert out["error"] == "boom"