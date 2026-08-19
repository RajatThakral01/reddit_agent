import pytest

from reddit_agent.exceptions import GuardrailBlocked
from reddit_agent.guardrails import (
    SAFETY_FIRST_REPLY,
    check_hazard,
    check_reply_guardrails,
    run_guardrails,
)
from reddit_agent.models import NormalizedPost


def _post(**overrides) -> NormalizedPost:
    from datetime import datetime, timezone

    base = dict(
        id="t3_gr001",
        title="PC fans at max speed",
        body="My PC fans suddenly spin at max speed and it is getting hot.",
        author="u/guard_user",
        subreddit="techsupport",
        created_utc=datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc),
        url="https://reddit.com/r/techsupport/comments/gr001",
        flair=None,
        score=3,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    return NormalizedPost(**base)


def test_hazard_detected_in_post():
    assert check_hazard("There is a burning smell from my laptop", "Laptop issue") is True


def test_hazard_forces_safety_reply():
    hazard_post = _post(
        id="t3_gr002",
        body="There is a burning smell and smoke coming from the laptop vents.",
    )
    reply, reason = run_guardrails(hazard_post, "Step 1: remove the battery and open the case")
    assert reply == SAFETY_FIRST_REPLY
    assert reason == ""


def test_hazard_runs_before_denylist():
    # Even if the reply is destructive, hazard on the post wins.
    hazard_post = _post(body="My phone sparked and white smoke came out")
    dirty_reply = "format your drive and flash bios without backup, then try again"
    reply, _ = run_guardrails(hazard_post, dirty_reply)
    assert reply == SAFETY_FIRST_REPLY


def test_destructive_content_blocked():
    passes, rule = check_reply_guardrails("Just format your drive and start over.")
    assert passes is False
    assert rule == "destructive_content"
    with pytest.raises(GuardrailBlocked):
        run_guardrails(_post(), "format your drive")


def test_credential_request_blocked():
    passes, rule = check_reply_guardrails("send me your password and I will take a look.")
    assert passes is False
    assert rule == "credential_request"


def test_promotional_content_blocked():
    passes, rule = check_reply_guardrails("Our DAXVORA service can fix that for you.")
    assert passes is False
    assert rule == "promotional_content"


def test_url_in_reply_blocked():
    passes, rule = check_reply_guardrails("See https://example.com/fix for details.")
    assert passes is False
    assert rule == "promotional_content"


def test_clean_reply_passes_all_guardrails():
    clean = "Try reseating the RAM and booting with a single stick to isolate the issue."
    passes, rule = check_reply_guardrails(clean)
    assert passes is True
    assert rule == ""
    final, reason = run_guardrails(_post(), clean)
    assert final == clean
    assert reason == ""


def test_certainty_language_blocked():
    passes, rule = check_reply_guardrails("This will definitely fix it, always works.")
    assert passes is False
    assert rule == "unsupported_certainty"