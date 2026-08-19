"""Reply generation: grounded troubleshooting or clarifying question (FR-7).

Guardrails run on every generated reply without exception (including DRY_RUN).
Hazard detection runs BEFORE the denylist: a hazardous post always yields the
safety-first message, never standard repair steps.
"""

import json
import time

import httpx

from reddit_agent.cost_tracker import CostTracker
from reddit_agent.exceptions import GenerationFailed, GuardrailBlocked
from reddit_agent.guardrails import SAFETY_FIRST_REPLY, run_guardrails
from reddit_agent.models import NormalizedPost
from reddit_agent.observability import LogEvent, Stage, log_event

from reddit_agent.config import AgentConfig

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
MAX_REPLY_ATTEMPTS = 3

_TROUBLESHOOTING_PROMPT_TEMPLATE = """
You are replying on Reddit to a tech-troubleshooting post with helpful, grounded advice.

Rules:
- Keep the reply under 200 words.
- Reference specific details from the post.
- Never assert a definitive diagnosis ("this is definitely X").
- Never recommend destructive steps.
- Never mention any product, service, or brand.
- Never include URLs.
- Do not ask for additional info unless truly needed.

Post subreddit: r/{subreddit}
Post title: {title}
Post body: {body}
Post score: {score}

Return only the reply text, with no surrounding quotes or commentary.
""".strip()

_CLARIFYING_PROMPT_TEMPLATE = """
You are a helper on Reddit answering a tech-troubleshooting post that is missing
important information.

Rules:
- Keep the reply under 200 words.
- Ask exactly ONE short clarifying question for the single most important
   missing detail (device model, OS version, exact error message, etc.).
- Do not guess a diagnosis.
- Never recommend destructive steps.
- Never mention any product, service, or brand.
- Never include URLs.

Post: {title} — {body}

Return only the question text, with no surrounding quotes or commentary.
""".strip()

# Candidate-set pricing for the model used.
PRICE_USD_PER_1M_INPUT_TOKENS = 0.14
PRICE_USD_PER_1M_OUTPUT_TOKENS = 0.28


def _estimate_cost_usd(usage: dict) -> float:
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    return round(
        prompt_tokens / 1_000_000 * PRICE_USD_PER_1M_INPUT_TOKENS
        + completion_tokens / 1_000_000 * PRICE_USD_PER_1M_OUTPUT_TOKENS,
        10,
    )


def _build_reply_prompt(post: NormalizedPost, score_result: dict, clarifying: bool) -> str:
    body = post.body[:2000] if post.body else "(no body)"
    if clarifying:
        return _CLARIFYING_PROMPT_TEMPLATE.format(
            title=post.title[:500],
            body=body,
        )
    return _TROUBLESHOOTING_PROMPT_TEMPLATE.format(
        subreddit=post.subreddit,
        title=post.title[:500],
        body=body,
        score=int(score_result.get("score", 0)),
    )


async def _chat_completion(payload: dict, api_key: str) -> tuple[str, dict]:
    """POST a chat request to DeepSeek. Returns (reply text, usage dict).

    This is reply_gen's single HTTP boundary; tests replace it.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(DEEPSEEK_BASE_URL, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip(), data.get("usage", {})


def _build_reply_payload(prompt: str, config) -> dict:
    return {
        "model": config.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
        "max_tokens": 400,
    }


async def generate_reply(
    post: NormalizedPost,
    score_result: dict,
    config: AgentConfig,
    cost_tracker: CostTracker,
    conn,
) -> str:
    """
    Generate a reply for a post that has passed scoring.

    Two modes based on score_result:
    1. If factors["answerable"] is True and factors["problem_clarity"] >= 0.5:
       → Generate specific troubleshooting advice grounded in the post
    2. Otherwise (insufficient info):
       → Generate a single clarifying question for the ONE most important
         missing piece of info

    After generating the raw reply:
    1. Run run_guardrails(post, raw_reply)
    2. If guardrails block: mark status=blocked_by_guardrail, raise GuardrailBlocked
    3. If hazard detected: use SAFETY_FIRST_REPLY instead of generated reply
    4. Log the result to events table (stage=GENERATE and stage=GUARDRAIL)
    5. Return the final safe reply text
    """
    factors = score_result.get("factors", {})
    clarifying = not (
        factors.get("answerable") is True and (factors.get("problem_clarity") or 0) >= 0.5
    )

    prompt = _build_reply_prompt(post, score_result, clarifying)
    payload = _build_reply_payload(prompt, config)
    start = time.perf_counter()

    raw_reply = ""
    for _attempt in range(1, MAX_REPLY_ATTEMPTS + 1):
        try:
            raw_reply, usage = await _chat_completion(payload, config.llm_api_key)
            if raw_reply:
                break
        except (httpx.HTTPError, KeyError, IndexError):
            continue

    latency_ms = int((time.perf_counter() - start) * 1000)
    cost_usd = _estimate_cost_usd(usage) if raw_reply else 0.0
    cost_tracker.add(cost_usd, tokens_used=int(usage.get("total_tokens", 0) or 0))

    if not raw_reply:
        raise GenerationFailed(
            f"GenerationFailed: item {post.id} after {MAX_REPLY_ATTEMPTS} attempts",
            post_id=post.id,
            attempt=MAX_REPLY_ATTEMPTS,
        )

    decision = "clarifying_question" if clarifying else "troubleshooting_reply"
    try:
        log_event(
            LogEvent(
                stage=Stage.GENERATE,
                decision=decision,
                post_id=post.id,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
            ),
            conn=conn,
        )
    finally:
        conn.commit()

    try:
        final_reply, _ = run_guardrails(post, raw_reply)
    except GuardrailBlocked as exc:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE posts SET status='blocked_by_guardrail', updated_at=NOW() WHERE id=%s",
                (post.id,),
            )
        conn.commit()
        try:
            log_event(
                LogEvent(
                    stage=Stage.GUARDRAIL,
                    decision="blocked",
                    reason=exc.rule_name,
                    post_id=post.id,
                ),
                conn=conn,
            )
        finally:
            conn.commit()
        raise

    if final_reply == SAFETY_FIRST_REPLY:
        guardrail_decision = "safety_reply"
        guardrail_reason = "hazard"
    else:
        guardrail_decision = "passed"
        guardrail_reason = ""

    try:
        log_event(
            LogEvent(
                stage=Stage.GUARDRAIL,
                decision=guardrail_decision,
                reason=guardrail_reason,
                post_id=post.id,
            ),
            conn=conn,
        )
    finally:
        conn.commit()

    return final_reply