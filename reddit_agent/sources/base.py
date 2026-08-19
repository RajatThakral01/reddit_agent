from abc import ABC, abstractmethod

from reddit_agent.models import NormalizedPost


class RedditSource(ABC):
    """
    Abstract interface for Reddit data sources.
    Two implementations exist:
    - TestRedditSource: fixture-driven, deterministic, zero network calls
    - LiveRedditSource: asyncpraw-based, real Reddit API

    All pipeline code must depend on this interface only, never on a concrete impl.
    """

    @abstractmethod
    async def fetch_new_posts(
        self,
        subreddit: str,
        cursor: str | None,
    ) -> tuple[list[NormalizedPost], str | None]:
        """
        Fetch new posts for a subreddit since the cursor position.

        Args:
            subreddit: subreddit name (without r/)
            cursor: last-seen Reddit fullname, or None for first fetch

        Returns:
            Tuple of (list of NormalizedPost, new cursor value)
            If no new posts, returns ([], cursor unchanged)
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any held connections or resources."""
        ...

    @abstractmethod
    async def post_comment(self, post_id: str, body: str) -> str:
        """Post a comment reply to Reddit. Returns the new comment ID.

        Only ever called from the LIVE posting path (reddit_agent.posting).
        """
        ...