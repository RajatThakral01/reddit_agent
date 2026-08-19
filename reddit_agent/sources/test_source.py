"""Fixture-driven Reddit source for testing.

Intentional design constraint: this module imports nothing network-related
(there is no asyncpraw import anywhere in this file, and nothing in the
standard library import list opens sockets). This makes it structurally
impossible for the test source to perform a network call.
"""

import json
from datetime import datetime
from pathlib import Path

from reddit_agent.models import NormalizedPost
from reddit_agent.sources.base import RedditSource

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


def _normalize_post(data: dict) -> NormalizedPost | None:
    """Convert a fixture dict into a NormalizedPost, skipping malformed entries."""
    if not REQUIRED_FIELDS.issubset(data.keys()):
        return None
    created_utc = datetime.fromisoformat(data["created_utc"].replace("Z", "+00:00"))
    return NormalizedPost(
        id=data["id"],
        title=data["title"],
        body=data["body"],
        author=data["author"],
        subreddit=data["subreddit"],
        created_utc=created_utc,
        url=data["url"],
        flair=data["flair"],
        score=data["score"],
        is_locked=data["is_locked"],
        is_deleted=data["is_deleted"],
        is_removed=data["is_removed"],
        is_archived=data["is_archived"],
        _scenario=data.get("_scenario", ""),
    )


class TestRedditSource(RedditSource):
    """Fixture-driven Reddit source for testing.

    Makes zero network calls. Deterministic and replayable.
    All fixture data comes from JSON files in tests/fixtures/.
    """

    def __init__(
        self,
        fixtures_dir: str = "tests/fixtures",
        fixture_categories: list[str] | None = None,
    ):
        directory = Path(fixtures_dir)
        if fixture_categories is None:
            files = sorted(directory.glob("*.json"))
        else:
            files = sorted(directory / f"{cat}.json" for cat in fixture_categories)

        self._posts: list[NormalizedPost] = []
        for path in files:
            with open(path, encoding="utf-8") as fh:
                raw_posts = json.load(fh)
            for entry in raw_posts:
                post = _normalize_post(entry)
                if post is not None:
                    self._posts.append(post)

    async def fetch_new_posts(
        self,
        subreddit: str,
        cursor: str | None,
    ) -> tuple[list[NormalizedPost], str | None]:
        # Return posts in order, using cursor as an offset.
        if cursor is None:
            start = 0
        else:
            ids = [post.id for post in self._posts]
            if cursor not in ids:
                return [], cursor
            start = ids.index(cursor) + 1

        batch = self._posts[start:]
        if not batch:
            return [], cursor
        return batch, batch[-1].id

    async def close(self) -> None:
        pass  # Nothing to close for test source