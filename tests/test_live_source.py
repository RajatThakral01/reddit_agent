import os
from types import SimpleNamespace

import pytest

from reddit_agent.models import NormalizedPost
from reddit_agent.sources.live import LiveRedditSource

SKIP_REASON = "Live Reddit credentials not available"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("REDDIT_CLIENT_ID"), reason=SKIP_REASON),
]


def _config():
    return SimpleNamespace(
        reddit_source="live",
        reddit_client_id=os.getenv("REDDIT_CLIENT_ID"),
        reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        reddit_username=os.getenv("REDDIT_USERNAME", ""),
        reddit_user_agent=os.getenv("REDDIT_USER_AGENT", "reddit-agent-test"),
    )


async def test_live_fetch_returns_normalized_posts():
    source = LiveRedditSource(_config())
    try:
        posts, cursor = await source.fetch_new_posts("learnpython", None)
        assert isinstance(posts, list)
        assert len(posts) > 0
        assert all(isinstance(post, NormalizedPost) for post in posts)
        assert all(post.id.startswith("t3_") for post in posts)
        assert cursor is not None
    finally:
        await source.close()


async def test_live_cursor_advances():
    source = LiveRedditSource(_config())
    try:
        first_batch, cursor = await source.fetch_new_posts("learnpython", None)
        assert cursor is not None
        second_batch, cursor2 = await source.fetch_new_posts("learnpython", cursor)
        # Newest-first pagination: the second batch overlaps or is a strict subset.
        first_ids = {post.id for post in first_batch}
        second_ids = {post.id for post in second_batch}
        assert len(second_ids) <= len(first_ids)
        assert second_ids <= first_ids
    finally:
        await source.close()


async def test_live_auth_works():
    source = LiveRedditSource(_config())
    try:
        posts, _ = await source.fetch_new_posts("programming", None)
        assert len(posts) >= 1
    finally:
        await source.close()