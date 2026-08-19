from reddit_agent.sources.base import RedditSource


class LiveRedditSource(RedditSource):
    """
    asyncpraw-based Reddit source for live operation.
    Requires REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME,
    REDDIT_USER_AGENT environment variables.
    """

    async def fetch_new_posts(self, subreddit, cursor):
        raise NotImplementedError("LiveRedditSource will be implemented in a later phase")

    async def close(self):
        pass