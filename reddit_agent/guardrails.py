"""Guardrails: hazard detection (FR-8a) + reply denylist checks (FR-8).

CRITICAL RULE: these checks run on EVERY generated reply with no exceptions,
including DRY_RUN mode. Hazard detection (against the source post) ALWAYS runs
before the general denylist (against the generated reply).
"""

import re

import re

from reddit_agent.exceptions import GuardrailBlocked
from reddit_agent.models import NormalizedPost

HAZARD_PATTERNS = [
    "burning smell",
    "smoke",
    "on fire",
    "electric shock",
    "shocked me",
    "sparks",
    "sparking",
    "swollen battery",
    "puffy battery",
    "battery bulging",
    "data loss",
    "deleted everything",
    "wiped drive",
    "rm -rf",
    "house fire",
    "caught fire",
]

SAFETY_FIRST_REPLY = (
    "This sounds like it could involve a serious safety risk. "
    "Please stop using the device immediately and consult a qualified professional "
    "before attempting any repairs or recovery steps. Your safety is the priority."
)

DESTRUCTIVE_PATTERNS = [
    "format your drive",
    "format the drive",
    "delete system32",
    "flash bios without",
    "remove all files",
    "wipe your hard drive",
    "factory reset without backup",
    "delete windows",
]

CREDENTIAL_PATTERNS = [
    "send me your password",
    "share your password",
    "give me your login",
    "share your credentials",
    "provide your username and password",
    "dm me your",
    "message me your password",
]

CERTAINTY_PATTERNS = [
    "100% guaranteed",
    "definitely will fix",
    "always works",
    "this will definitely",
    "guaranteed to work",
    "without a doubt this fixes",
]

PROMOTIONAL_PATTERNS = [
    "daxvora",
    "our service",
    "our product",
    "check out our",
    "visit our website",
    "sign up for",
    "use code ",
    r"https?://",  # any URL
]

_BLOCK_RULES = (
    ("destructive_content", DESTRUCTIVE_PATTERNS),
    ("credential_request", CREDENTIAL_PATTERNS),
    ("unsupported_certainty", CERTAINTY_PATTERNS),
    ("promotional_content", PROMOTIONAL_PATTERNS),
)


def check_hazard(post_body: str, post_title: str) -> bool:
    """Return True if the source post contains hazard-risk content.

    Checked against post content (not the reply). If True, the reply MUST be
    SAFETY_FIRST_REPLY, never standard repair steps.
    """
    haystack = f"{post_title} {post_body}".lower()
    return any(pattern in haystack for pattern in HAZARD_PATTERNS)


def check_reply_guardrails(reply_text: str) -> tuple[bool, str]:
    """Check a generated reply against all denylist patterns.

    Returns (passes: bool, rule_name: str).
    rule_name is empty string if passes.
    rule_name is one of: "destructive_content", "credential_request",
    "unsupported_certainty", "promotional_content".
    """
    lowered = reply_text.lower()
    for rule_name, patterns in _BLOCK_RULES:
        for pattern in patterns:
            if re.search(pattern, lowered):
                return False, rule_name
    return True, ""


def run_guardrails(
    post: NormalizedPost,
    reply_text: str,
) -> tuple[str, str]:
    """Run the complete guardrail pipeline.

    ORDER: hazard check on post first, then denylist on reply.

    Returns (final_reply_text, block_reason).
    - If hazard detected: returns (SAFETY_FIRST_REPLY, "") — not blocked, just replaced
    - If denylist fails: raises GuardrailBlocked — do not post
    - If all pass: returns (reply_text, "") — proceed normally
    """
    if check_hazard(post.body, post.title):
        return SAFETY_FIRST_REPLY, ""

    passes, rule_name = check_reply_guardrails(reply_text)
    if not passes:
        raise GuardrailBlocked(
            f"GuardrailBlocked: {rule_name}",
            rule_name=rule_name,
            post_id=post.id,
        )

    return reply_text, ""