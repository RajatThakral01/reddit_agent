import os

import psycopg2
import pytest
from psycopg2 import errors

from reddit_agent.db.connection import run_migrations

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set — requires a real Postgres test DB",
)
class TestDatabase:
    def _conn(self):
        """Fresh connection to the test DB with a clean schema and migrations applied."""
        conn = psycopg2.connect(TEST_DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        run_migrations(conn)
        return conn

    def test_migrations_run_clean(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
                tables = {row[0] for row in cur.fetchall()}
            expected = {"subreddits", "posts", "scores", "replies", "kill_switch", "events"}
            assert expected.issubset(tables), f"missing tables: {expected - tables}"
        finally:
            conn.close()

    def test_migrations_idempotent(self):
        conn = self._conn()
        try:
            run_migrations(conn)  # second run must not error
        finally:
            conn.close()

    def test_unique_constraint_on_replies(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO subreddits (name) VALUES ('unique_test_sub') RETURNING id")
                subreddit_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO posts (id, subreddit_id, content_hash, raw_payload, status,"
                    " created_utc, updated_at)"
                    " VALUES ('t3_test', %s, 'hash', '{}', 'replied', NOW(), NOW())",
                    (subreddit_id,),
                )
                cur.execute(
                    "INSERT INTO replies (post_id, reply_text, mode, status) "
                    "VALUES ('t3_test', 'hello', 'dry_run', 'simulated')"
                )
                conn.commit()
                with pytest.raises(errors.UniqueViolation):
                    cur.execute(
                        "INSERT INTO replies (post_id, reply_text, mode, status) "
                        "VALUES ('t3_test', 'hello again', 'dry_run', 'simulated')"
                    )
                    conn.commit()
        finally:
            conn.rollback()
            conn.close()

    def test_kill_switch_seed_is_false(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT enabled FROM kill_switch WHERE id = 1")
                row = cur.fetchone()
            assert row is not None
            assert row[0] is False
        finally:
            conn.close()

    def test_automation_allowed_defaults_false(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO subreddits (name) VALUES (%s) RETURNING automation_allowed",
                    ("automation_test_sub",),
                )
                allowed = cur.fetchone()[0]
            assert allowed is False
        finally:
            conn.close()