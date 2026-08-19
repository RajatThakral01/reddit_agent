"""Scoring engine: rule-based pre-filter + LLM worthiness scorer (FR-6, FR-14).

The pre-filter runs FIRST and is cheap/deterministic/zero-cost. Only posts that
pass it reach the LLM, because LLM calls are the only paid resource in the
project. Every score is persisted with its ``policy_version`` and structured
``factors`` so decisions are explainable and reproducible re-runs are possible.
"""

import json
import time
from datetime import datetime, timezone

import httpx

from reddit_agent.cost_tracker import CostTracker
from reddit_agent.exceptions import GenerationFailed
from reddit_agent.models import NormalizedPost
from reddit_agent.observability import LogEvent, Stage, log_event

# --------------------------------------------------------------------------- #
# Part 1 — Policy version
# --------------------------------------------------------------------------- #
POLICY_VERSION = "v1.0"
# When scoring logic changes, bump this version.
# Old scores with different policy_version are distinguishable in the DB.


# ---------------------------------------------------------------------------
# Part 2 — Rule-based pre-filter
# --------------------------------------------------------------------------- #
SKIP_FLAIRS = {"solved", "[solved]", "meme", "off-topic", "meta", "weekly thread"}
MIN_BODY_LENGTH = 20
MAX_AGE_HOURS = 48

_CONFIDENCE_VALUES = ("low", "medium", "high")
_USER_INTENT_VALUES = ("seeking_help", "venting", "sharing", "other")
_RISK_VALUES = ("none", "low", "medium", "hazard")
_REQUIRED_FACTORS = (
    "relevance",
    "problem_clarity",
    "user_intent",
    "recency_minutes",
    "risk",
    "answerable",
)


def pre_filter(post: NormalizedPost, config) -> tuple[bool, str]:
    """
    Fast, deterministic, zero-cost pre-filter.
    Returns (passes: bool, reason: str).

    A post FAILS the pre-filter if ANY of these are true:
    1. None of the configured keywords appear in title.lower() + body.lower()
    2. The post flair (if present) is in the SKIP_FLAIRS set
    3. The post body is shorter than MIN_BODY_LENGTH characters
    4. The post is older than max_age_hours (default 48 hours)
    """
    haystack = f"{post.title} {post.body}".lower()

    if not any(keyword.lower() in haystack for keyword in config.keywords):
        return False, "no_keyword_match"

    if post.flair and post.flair.lower().strip() in SKIP_FLAIRS:
        return False, "disallowed_flair"

    if len(post.body) < MIN_BODY_LENGTH:
        return False, "body_too_short"

    max_age_hours = getattr(config, "max_age_hours", MAX_AGE_HOURS)
    age_hours = (datetime.now(timezone.utc) - post.created_utc).total_seconds() / 3600
    if age_hours > max_age_hours:
        return False, "post_too_old"

    return True, ""


# ---------------------------------------------------------------------------
# LLM scorer
# --------------------------------------------------------------------------- #
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
MAX_SCORE_ATTEMPTS = 2

# Candidate set pricing for the model used. Replace with the model's actual rates.
PRICE_USD_PER_1M_INPUT_TOKENS = 0.14
PRICE_USD_PER_1M_OUTPUT_TOKENS = 0.28

_SCORING_PROMPT_TEMPLATE = """
You are the worthiness scorer for a Reddit tech-troubleshooting reply agent.
Score how worthwhile the following post is for a grounded, helpful reply.
Return ONLY valid JSON, no commentary, in exactly this shape:
{{
  "score": <integer 0-100>,
  "reason": "<one sentence explaining the score>",
  "confidence": "<low|medium|high>",
  "policy_version": "{policy_version}",
  "factors": {{
    "relevance": <float 0.0-1.0>,
    "problem_clarity": <float 0.0-1.0>,
    "user_intent": "<seeking_help|venting|sharing|other>",
    "recency_minutes": <integer>,
    "risk": "<none|low|medium|hazard>",
    "answerable": <boolean>
  }}
}}

Scoring guidelines:
- Score 80-100: clear tech problem, specific symptoms, seeking help, safely answerable
- Score 60-79: tech problem but some ambiguity, still worth replying with clarification
- Score 40-59: possibly relevant but very vague or low intent
- Score 0-39: off-topic, promotional, solved, rhetorical, or unanswerable

Subreddit: r/{subreddit}
Post title: {title}
Post body: {body}
""".strip()


def _build_scoring_prompt(post: NormalizedPost) -> str:
    body = post.body[:2000] if post.body else "(no body)"
    return _SCORING_PROMPT_TEMPLATE.format(
        policy_version=POLICY_VERSION,
        subreddit=post.subreddit,
        title=post.title[:500],
        body=body,
    )


def _estimate_cost_usd(usage: dict) -> float:
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cost = (
        prompt_tokens / 1_000_000 * PRICE_USD_PER_1M_INPUT_TOKENS
        + completion_tokens / 1_000_000 * PRICE_USD_PER_1M_OUTPUT_TOKENS
    )
    return round(cost, 10)


def _extract_json_object(content: str) -> dict:
    """Parse a JSON object out of a model response, tolerating code fences."""
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM response")
    return json.loads(text[start : end + 1])


def _validate_scoring(parsed) -> dict:
    """Validate a parsed scoring payload; raise ValueError if malformed."""
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    score = parsed.get("score")
    if not isinstance(score, int) and not isinstance(score, float):
        raise ValueError("missing or invalid 'score'")
    if not 0 <= int(score) <= 100:
        raise ValueError("'score' out of range 0-100")
    if not isinstance(parsed.get("reason"), str) or not parsed["reason"]:
        raise ValueError("missing or invalid 'reason'")
    if parsed.get("confidence") not in _CONFIDENCE_VALUES:
        raise ValueError("invalid 'confidence'")
    if parsed.get("policy_version") is None:
        raise ValueError("missing 'policy_version'")
    factors = parsed.get("factors")
    if not isinstance(factors, dict):
        raise ValueError("missing 'factors' object")
    missing = [key for key in _REQUIRED_FACTORS if key not in factors]
    if missing:
        raise ValueError(f"factors missing keys: {missing}")
    return parsed


async def _chat_completion(payload: dict, api_key: str) -> tuple[dict, dict]:
    """POST the scoring payload to DeepSeek and return (parsed JSON, usage).

    This is the module's single HTTP boundary; tests replace it.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(DEEPSEEK_BASE_URL, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return _extract_json_object(content), data.get("usage", {})


def _build_score_payload(post: NormalizedPost, config) -> dict:
    return {
        "model": config.llm_model,
        "messages": [{"role": "user", "content": _build_scoring_prompt(post)}],
        "temperature": 0,  # deterministic scoring
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }


async def score_post(
    post: NormalizedPost,
    config,
    conn,
    cost_tracker: CostTracker | None = None,
) -> dict:
    """
    Score a post using the LLM worthiness scorer.

    ONLY called if pre_filter passes — never called on pre-filter failures.

    Steps:
    1. Build the scoring prompt
    2. Call DeepSeek API with the prompt
    3. Parse the JSON response
    4. Validate all required fields
    5. Check cumulative cost vs LLM_COST_CAP_USD — raise CostCapExceeded if over
    6. Persist to ``scores`` table
    7. Update ``posts.status = 'scored'``
    8. Log the event with cost_usd and latency_ms

    Returns the score dict as stored in the DB.
    On malformed LLM response: retry once, then raise GenerationFailed.
    """
    tracker = cost_tracker or CostTracker(config.llm_cost_cap_usd)
    payload = _build_score_payload(post, config)
    start = time.perf_counter()

    scoring = None
    usage = {}

    for attempt in range(1, MAX_SCORE_ATTEMPTS + 1):
        try:
            parsed, usage = await _chat_completion(payload, config.llm_api_key)
            scoring = _validate_scoring(parsed)
            break
        except (ValueError, json.JSONDecodeError, httpx.HTTPError, KeyError) as exc:
            continue

    latency_ms = int((time.perf_counter() - start) * 1000)

    if scoring is None:
        raise GenerationFailed(
            f"GenerationFailed: item {post.id} after 2 attempts",
            post_id=post.id,
            attempt=MAX_SCORE_ATTEMPTS,
        )

    cost_usd = _estimate_cost_usd(usage)
    tracker.add(cost_usd, tokens_used=int(usage.get("total_tokens", 0) or 0))

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scores (post_id, score, reason, confidence, policy_version, factors)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                post.id,
                int(scoring["score"]),
                scoring["reason"],
                scoring["confidence"],
                scoring.get("policy_version", POLICY_VERSION),
                json.dumps(scoring["factors"]),
            ),
        )
        cur.execute("UPDATE posts SET status='scored', updated_at=NOW() WHERE id=%s", (post.id,))
    conn.commit()

    try:
        log_event(
            LogEvent(
                stage=Stage.SCORE,
                decision="scored",
                reason=scoring["reason"],
                post_id=post.id,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            ),
            conn=conn,
        )
    finally:
        conn.commit()

    return scoring