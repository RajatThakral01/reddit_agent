from reddit_agent.exceptions import ConfigError
from reddit_agent.sources.base import RedditSource


def get_source(config) -> RedditSource:
    """
    Factory function — returns the correct RedditSource based on config.reddit_source.
    This is the ONLY place in the codebase that decides which source to use.
    """
    if config.reddit_source == "test":
        from reddit_agent.sources.test_source import TestRedditSource

        return TestRedditSource()
    elif config.reddit_source == "live":
        from reddit_agent.sources.live import LiveRedditSource

        return LiveRedditSource(config)
    else:
        raise ConfigError(f"Unknown REDDIT_SOURCE: {config.reddit_source}")