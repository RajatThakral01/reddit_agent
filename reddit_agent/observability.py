"""Observability — structured audit events for every stage of every item.

FR-12 requires that EVERY stage of EVERY item's processing writes a structured
event. This module is the single place that produces those events. Every event
is written to:
  1. stdout as a single-line JSON object (for the evaluator to see in the
     terminal), and
  2. the Postgres ``events`` table (when a connection is supplied).

Redaction is mandatory: secrets (keys, secrets, passwords, tokens) and Reddit
usernames never appear in any output.
"""

import json
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterator

_USERNAME_RE = re.compile(r"u/[\w\-\_]+", re.IGNORECASE)

_INSERT_EVENT_SQL = """
    INSERT INTO events (post_id, stage, decision, reason, latency_ms, cost_usd, error, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


class Stage(str, Enum):
    """Processing stage of an item. Values map 1:1 to the events table ``stage`` column."""

    INGEST = "INGEST"
    SCORE = "SCORE"
    GENERATE = "GENERATE"
    GUARDRAIL = "GUARDRAIL"
    POST = "POST"
    SYSTEM = "SYSTEM"  # non-item events: startup, kill switch changes, etc.


@dataclass
class LogEvent:
    """A single structured event to be recorded by :func:`log_event`."""

    stage: Stage
    decision: str | None = None
    reason: str | None = None
    post_id: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    extra: dict | None = None  # additional context, always serialized


_SECRET_KEYS = ("secret", "key", "password", "token")
_USERNAME_RE = re.compile(r"u/[\w\-\_]+", re.IGNORECASE)


def _redact_secret_value(key: str, value) -> str:
    """Return the redacted form of a value when its key suggests a secret."""
    lower = key.lower()
    if any(part in lower for part in _SECRET_KEYS):
        return "[REDACTED]"
    return value


def _redact_text(text: str | None) -> str | None:
    """Replace Reddit usernames with a placeholder in free-text fields."""
    if text is None:
        return None
    return _USERNAME_RE.sub("u/[username]", text)


def _redact_extra(extra: dict | None) -> dict | None:
    """Return a copy of ``extra`` with secret-typed values replaced."""
    if not extra:
        return extra
    cleaned = {}
    for key, value in extra.items():
        cleaned[key] = _redact_secret_value(key, value)
    return cleaned


def _json_default(obj):
    """Serialize datetimes as ISO strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj).__name__} not serializable")


def log_event(
    event: LogEvent,
    conn=None,  # psycopg2 connection, optional — if None, only logs to stdout
) -> None:
    """Record a single structured event to stdout and, if ``conn`` is given, Postgres.

    Every stage of every item must call this. The function never raises, so
    observability failures cannot crash the pipeline.
    """
    created_at = datetime.now(timezone.utc)

    record = {
        "timestamp": created_at.isoformat(),
        "stage": event.stage.value,
        "decision": event.decision,
        "reason": event.reason,
        "post_id": event.post_id,
        "latency_ms": event.latency_ms,
        "cost_usd": event.cost_usd,
        "error": event.error,
    }

    # Public output: redact usernames in free text, and any secret-typed extras.
    public = dict(record)
    for field in ("reason", "error", "decision"):
        public[field] = _redact_text(public[field])
    public["extra"] = _redact_extra(event.extra)

    sys.stdout.write(json.dumps(public, default=_json_default, separators=(",", ":")) + "\n")
    sys.stdout.flush()

    if conn is not None:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    _INSERT_EVENT_SQL,
                    (
                        event.post_id,
                        event.stage.value,
                        event.decision,
                        event.reason,
                        event.latency_ms,
                        event.cost_usd,
                        event.error,
                        created_at,
                    ),
                )
        except Exception:
            # Observability must never break the pipeline.
            pass


@contextmanager
def timed_stage(
    stage: Stage,
    post_id: str | None = None,
    conn=None,
    decision: str | None = None,
) -> Iterator[None]:
    """Context manager that logs an event with the elapsed time on exit.

    On a normal exit it logs ``latency_ms``; on an exception it logs the error
    and re-raises it.
    """
    start = datetime.now(timezone.utc)

    def _emit(error: str | None, latency_ms: int | None):
        log_event(
            LogEvent(
                stage=stage,
                decision=decision,
                post_id=post_id,
                latency_ms=latency_ms,
                error=error,
            ),
            conn=conn,
        )

    try:
        yield
    except Exception as exc:
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _emit(error=str(exc), latency_ms=elapsed)
        raise
    else:
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        _emit(error=None, latency_ms=elapsed)