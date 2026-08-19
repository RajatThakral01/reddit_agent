"""Pipeline orchestrator: wires every stage into one end-to-end flow (FR-13).

Stage sequence for each post:
    INGEST -> PRE-FILTER -> SCORE -> GENERATE -> GUARDRAIL -> POST

Every stage is latency-tracked via ``timed_stage`` and logs its outcome via
``observability.log_event``. Each stage handles its own failure modes gracefully —
a single bad item never crashes the process. The one exception is
``CostCapExceeded``, which aborts the entire run as required by FR-14.
"""

import time

from reddit_agent.cost_tracker import CostTracker
from reddit_agent.exceptions import (
    BlockedBySubredditPolicy,
    CostCapExceeded,
    DuplicateReplyPrevented,
    GenerationFailed,
    GuardrailBlocked,
    KillSwitchActive,
    RedditUnavailable,
    UnactionableContent,
)
from reddit_agent.ingest import _get_or_create_subreddit, ingest_post
from reddit_agent.models import NormalizedPost
from reddit_agent.observability import LogEvent, Stage, log_event, timed_stage
from reddit_agent.posting import attempt_post
from reddit_agent.reply_gen import generate_reply
from reddit_agent.scoring import pre_filter, score_post


def _log(conn, stage: Stage, decision: str, post_id: str, reason=None, error=None):
    try:
        log_event(
            LogEvent(stage=stage, decision=decision, reason=reason, post_id=post_id, error=error),
            conn=conn,
        )
    finally:
        conn.commit()


def _set_status(conn, post_id: str, status: str):
    with conn.cursor() as cur:
        cur.execute("UPDATE posts SET status=%s, updated_at=NOW() WHERE id=%s", (status, post_id))
    conn.commit()


def _summary(post_id: str, stages: list[str], start: float, status: str) -> dict:
    return {
        "post_id": post_id,
        "final_status": status,
        "stages_completed": stages,
        "total_latency_ms": int((time.perf_counter() - start) * 1000),
    }


async def run_pipeline_for_post(
    post: NormalizedPost,
    subreddit_name: str,
    config,
    reddit_source,
    cost_tracker: CostTracker,
    conn,
) -> dict:
    """Run the full pipeline for a single post.

    Returns {(post_id, final_status, stages_completed, total_latency_ms)}.
    """
    start = time.perf_counter()
    stages: list[str] = []
    subreddit_id = _get_or_create_subreddit(conn, subreddit_name)

    # ---- Stage 1: INGEST ------------------------------------------------------
    try:
        with timed_stage(Stage.INGEST, post_id=post.id, conn=conn):
            ingest_status = await ingest_post(post, subreddit_id, conn)
    except UnactionableContent:
        return _summary(post.id, stages, start, "unactionable")
    stages.append("ingest")

    if ingest_status in ("duplicate_ignored", "edit_after_reply_ignored"):
        _log(conn, Stage.INGEST, ingest_status, post.id)
        return _summary(post.id, stages, start, "duplicate")

    # ---- Stage 2: PRE-FILTER (zero-cost, before any LLM call) ------------------
    with timed_stage(Stage.SCORE, post_id=post.id, conn=conn):
        passes, reason = pre_filter(post, config)
    if not passes:
        _log(conn, Stage.SCORE, "pre_filter_skipped", post.id, reason=reason)
        _set_status(conn, post.id, "skipped")
        return _summary(post.id, stages + ["pre_filter"], start, "skipped")
    stages.append("pre_filter")

    # ---- Stage 3: SCORE (LLM) ----------------------------------------------------
    try:
        with timed_stage(Stage.SCORE, post_id=post.id, conn=conn):
            score_result = await score_post(post, config, conn, cost_tracker)
    except CostCapExceeded:
        raise
    except (GenerationFailed, RedditUnavailable) as exc:
        _log(conn, Stage.SCORE, "score_error", post.id, reason=type(exc).__name__, error=str(exc))
        _set_status(conn, post.id, "error")
        return _summary(post.id, stages + ["score"], start, "error")
    stages.append("score")

    if int(score_result.get("score", 0)) < int(getattr(config, "worthiness_threshold", 50)):
        _log(conn, Stage.SCORE, "below_threshold", post.id, reason=f"score={score_result.get('score')}")
        _set_status(conn, post.id, "skipped")
        return _summary(post.id, stages, start, "skipped")

    # ---- Stage 4/5: GENERATE + GUARDRAILS ---------------------------------------- 
    try:
        with timed_stage(Stage.GENERATE, post_id=post.id, conn=conn):
            reply_text = await generate_reply(post, score_result, config, cost_tracker, conn)
    except GuardrailBlocked:
        return _summary(post.id, stages + ["generate", "guardrail"], start, "blocked_by_guardrail")
    except GenerationFailed as exc:
        _log(conn, Stage.GENERATE, "generate_error", post.id, reason=type(exc).__name__, error=str(exc))
        _set_status(conn, post.id, "error")
        return _summary(post.id, stages + ["generate"], start, "error")
    stages.append("generate")

    # ---- Stage 6: POST GATE ---------------------------------------------------------
    try:
        with timed_stage(Stage.POST, post_id=post.id, conn=conn):
            result = await attempt_post(post, reply_text, config, reddit_source, conn)
    except (KillSwitchActive, BlockedBySubredditPolicy, DuplicateReplyPrevented) as exc:
        _log(conn, Stage.POST, "post_blocked", post.id, reason=type(exc).__name__, error=str(exc))
        return _summary(post.id, stages + ["post"], start, "post_blocked")
    except RedditUnavailable as exc:
        _log(conn, Stage.POST, "post_failed", post.id, reason=type(exc).__name__, error=str(exc))
        _set_status(conn, post.id, "error")
        return _summary(post.id, stages + ["post"], start, "error")
    stages.append("post")

    if result.get("status") == "simulated":
        _set_status(conn, post.id, "replied")
        return _summary(post.id, stages, start, "simulated")
    if result.get("status") == "posted":
        _set_status(conn, post.id, "replied")
        return _summary(post.id, stages, start, "posted")
    return _summary(post.id, stages, start, result.get("status", "error"))


async def run_poll_cycle(
    config,
    reddit_source,
    cost_tracker: CostTracker,
    conn,
) -> dict:
    """
    Run one full poll cycle across all configured subreddits.

    For each subreddit:
    1. Read cursor from subreddits table
    2. Fetch new posts via reddit_source.fetch_new_posts()
    3. Run run_pipeline_for_post() for each post
    4. Update cursor in subreddits table

    Returns counts: {total, new, skipped, simulated, posted, errors, unactivated, ...}
    """
    counts = {
        "total": 0,
        "new": 0,
        "skipped": 0,
        "unactionable": 0,
        "duplicate": 0,
        "blocked": 0,
        "simulated": 0,
        "posted": 0,
        "errors": 0,
    }

    for subreddit in getattr(config, "subreddits", []):
        with conn.cursor() as cur:
            cur.execute("SELECT cursor FROM subreddits WHERE name=%s", (subreddit,))
            row = cur.fetchone()
            cursor = row[0] if row else None

        posts, new_cursor = await reddit_source.fetch_new_posts(subreddit, cursor)

        for post in posts:
            counts["total"] += 1
            result = await run_pipeline_for_post(
                post, subreddit, config, reddit_source, cost_tracker, conn
            )
            status = result["final_status"]
            if status == "simulated":
                counts["simulated"] += 1
                counts["new"] += 1
            elif status == "posted":
                counts["posted"] += 1
                counts["new"] += 1
            elif status == "skipped":
                counts["skipped"] += 1
            elif status == "unactionable":
                counts["unactionable"] += 1
            elif status == "duplicate":
                counts["duplicate"] += 1
            elif status in ("blocked_by_guardrail", "post_blocked"):
                counts["blocked"] += 1
            else:
                counts["errors"] += 1

        if new_cursor:
            with conn.cursor() as cur:
                cur.execute("UPDATE subreddits SET cursor=%s WHERE name=%s", (new_cursor, subreddit))
            conn.commit()

    return counts