import json
from datetime import datetime
from pathlib import Path

import pytest

from reddit_agent.models import NormalizedPost

FIXTURES_DIR = Path(__file__).parent / "fixtures"

FIXTURE_FILES = [
    "positive.json",
    "negative.json",
    "ambiguous.json",
    "edge_cases.json",
    "duplicate.json",
    "edited.json",
    "risk.json",
]

REQUIRED_FIELDS = {
    "id",
    "title",
    "body",
    "author",
    "subreddit",
    "created_utc",
    "url",
    "flair",
    "score",
    "is_locked",
    "is_deleted",
    "is_removed",
    "is_archived",
}

ALLOWED_FIELDS = REQUIRED_FIELDS | {"_scenario"}


def _make_post(**overrides) -> NormalizedPost:
    base = dict(
        id="t3_test",
        title="Test title",
        body="Test body with details.",
        author="user",
        subreddit="techsupport",
        created_utc=datetime(2026, 8, 19, 10, 0, 0),
        url="https://reddit.com/r/techsupport/comments/test",
        flair=None,
        score=1,
        is_locked=False,
        is_deleted=False,
        is_removed=False,
        is_archived=False,
    )
    base.update(overrides)
    return NormalizedPost(**base)


def test_content_hash_deterministic():
    a = _make_post().content_hash()
    b = _make_post().content_hash()
    assert a == b


def test_content_hash_changes_on_edit():
    original = _make_post()
    edited = _make_post(body="Completely different body text.")
    assert original.content_hash() != edited.content_hash()


def test_is_actionable_locked_false():
    assert _make_post(is_locked=True).is_actionable() is False


def test_is_actionable_deleted_false():
    assert _make_post(is_deleted=True).is_actionable() is False


def test_is_actionable_empty_body_false():
    assert _make_post(body="").is_actionable() is False


def test_is_actionable_normal_post_true():
    assert _make_post().is_actionable() is True


def test_fixtures_load_correctly():
    for filename in FIXTURE_FILES:
        with open(FIXTURES_DIR / filename, encoding="utf-8") as fh:
            posts = json.load(fh)
        assert isinstance(posts, list)
        assert len(posts) > 0, f"{filename} is empty"
        for post in posts:
            assert "_scenario" in post, f"{filename}: missing _scenario"
            if "title" not in post:
                # The only item may be the intentional malformed fixture.
                assert post.get("_scenario") == "edge_malformed_missing_title"
                continue
            missing = REQUIRED_FIELDS - set(post.keys())
            assert not missing, f"{filename}: missing fields {missing}"
            extra = set(post.keys()) - ALLOWED_FIELDS
            assert not extra, f"{filename}: unexpected fields {extra}"
            datetime.fromisoformat(post["created_utc"].replace("Z", "+00:00"))