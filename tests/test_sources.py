from types import SimpleNamespace

import pytest

from reddit_agent.exceptions import ConfigError
from reddit_agent.models import NormalizedPost
from reddit_agent.sources import get_source
from reddit_agent.sources.test_source import TestRedditSource


@pytest.mark.asyncio
class TestTestSource:
    async def test_test_source_loads_fixtures(self):
        assert TestRedditSource(fixture_categories=["positive"]) is not None

    async def test_test_source_returns_normalized_posts(self):
        src = TestRedditSource(fixture_categories=["positive"])
        posts, cursor = await src.fetch_new_posts("techsupport", None)
        assert all(isinstance(p, NormalizedPost) for p in posts)
        assert len(posts) == 5

    async def test_test_source_cursor_advances(self):
        src = TestRedditSource(fixture_categories=["positive"])
        posts, _ = await src.fetch_new_posts("techsupport", None)
        first_cursor = posts[0].id
        after, new_cursor = await src.fetch_new_posts("techsupport", first_cursor)
        assert len(after) == len(posts) - 1
        assert all(p.id != first_cursor for p in after)
        assert new_cursor == posts[-1].id

    async def test_test_source_makes_no_network_calls(self, mocker):
        called = {"blocked": False}

        def blocking_connect(self_addr, address):
            called["blocked"] = True
            raise AssertionError("network call attempted")

        mocker.patch("socket.socket.connect", blocking_connect)
        src = TestRedditSource(fixture_categories=["risk"])
        await src.fetch_new_posts("techsupport", None)
        assert called["blocked"] is False

    async def test_test_source_deterministic(self):
        src = TestRedditSource(fixture_categories=["ambiguous"])
        first, _ = await src.fetch_new_posts("techsupport", None)
        second, _ = await src.fetch_new_posts("techsupport", None)
        assert [p.id for p in first] == [p.id for p in second]

    async def test_test_source_respects_unknown_cursor(self):
        src = TestRedditSource(fixture_categories=["positive"])
        posts, cursor = await src.fetch_new_posts("techsupport", "t3_does_not_exist")
        assert posts == []
        assert cursor == "t3_does_not_exist"


@pytest.mark.asyncio
class TestGetSource:
    async def test_get_source_returns_test_source(self):
        config = SimpleNamespace(reddit_source="test")
        source = get_source(config)
        assert isinstance(source, TestRedditSource)
        await source.close()

    async def test_get_source_invalid_raises(self):
        config = SimpleNamespace(reddit_source="invalid")
        with pytest.raises(ConfigError):
            get_source(config)