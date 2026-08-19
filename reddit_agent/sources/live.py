"""LiveRedditSource: asyncpraw-based real Reddit API source.

OAuth2 script-app authentication, asyncpraw-managed token refresh, and
rate-limit-header-aware throttling (X-Ratelimit-Remaining / X-Ratelimit-Reset).

Auth refresh failure raises ``AuthRefreshFailed`` — the caller must pause the
affected subreddit's polling rather than crash the process.

Critical safety rule: ``post_comment`` must ONLY be called from
``reddit_agent.posting.attempt_post`` (the single posting gate).
"""

import asyncio
import time
from datetime import datetime, timezone

import asyncpraw
from asyncprawcore.exceptions import (
    InvalidToken,
    OAuthException,
    RequestException,
    ServerError,
    TooManyRequests,
)

from reddit_agent.exceptions import (
    AuthRefreshFailed,
    RateLimitExceeded,
    RedditUnavailable,
)
from reddit_agent.models import NormalizedPost
from reddit_agent.sources.base import RedditSource

MIN_RATE_LIMIT_REMAINING = 5
DEFAULT_RATE_WAIT_SECONDS = 30
MAX_RATE_WAIT_SECONDS = 60
FETCH_BATCH_LIMIT = 50


def _normalize_submission(submission) -> NormalizedPost:
    """Convert an asyncpraw Submission into a NormalizedPost."""
    author = "[deleted]" if submission.author is None else submission.author.name

    body = submission.selftext or ""
    is_removed = body in ("[removed]", "[deleted]")
    is_deleted = submission.author is None

    return NormalizedPost(
        id=f"t3_{submission.id}",
        title=submission.title or "",
        body=body,
        author=author,
        subreddit=submission.subreddit.display_name,
        created_utc=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
        url=f"https://www.reddit.com{submission.permalink}",
        flair=submission.link_flair_text if submission.link_flair_text else None,
        score=submission.score or 0,
        is_locked=bool(getattr(submission, "locked", False)),
        is_deleted=is_deleted,
        is_removed=is_removed,
        is_archived=bool(getattr(submission, "archived", False)),
    )


class LiveRedditSource(RedditSource):
    """
    asyncpraw-based Reddit source for live operation.

    Authentication: OAuth2 script app type.
    Rate limiting: reads X-Ratelimit-Remaining / X-Ratelimit-Reset headers.
    Token refresh: automatic via asyncpraw; on failure raises AuthRefreshFailed.
    """

    def __init__(self, config):
        self._reddit = asyncpraw.Reddit(
            client_id=config.reddit_client_id,
            client_secret=config.reddit_client_secret,
            username=config.reddit_username,
            user_agent=config.reddit_user_agent,
            password=getattr(config, "reddit_password", None),
            check_for_updates=False,
            timeout=60,
        )

    # ------------------------------------------------------------------
    # Rate limiting helpers
    # ------------------------------------------------------------------
    def _rate_limits(self) -> dict | None:
        """Return {'used', 'remaining', 'reset_timestamp'} or None if unknown."""
        try:
            return self._reddit.auth.limits or None
        except Exception:
            return None

    async def _throttle(self):
        """Sleep until the rate limit resets if the budget is nearly exhausted."""
        limits = self._rate_limits()
        if not limits:
            return
        try:
            remaining = int(limits.get("remaining", 0))
        except (TypeError, ValueError):
            return
        if remaining >= MIN_RATE_LIMIT_REMAINING:
            return
        reset = limits.get("reset_timestamp")
        wait = DEFAULT_RATE_WAIT_SECONDS
        if reset:
            wait = max(float(reset) - time.time(), 1.0)
            wait = min(wait, MAX_RATE_WAIT_SECONDS)
        await asyncio.sleep(wait)

    def _map_network_error(self, exc, subreddit: str = "") -> Exception:
        """Translate asyncprawcore errors into typed project exceptions."""
        if isinstance(exc, TooManyRequests):
            return RateLimitExceeded(
                "RateLimitExceeded: 429 from Reddit",
                retry_after_seconds=int(getattr(exc, "retry_after", 0) or 0),
            )
        if isinstance(exc, (OAuthException, InvalidToken)):
            return AuthRefreshFailed(
                f"AuthRefreshFailed: reddit auth failure ({type(exc).__name__})",
                subreddit=subreddit,
            )
        if isinstance(exc, (ServerError, RequestException)):
            return RedditUnavailable(
                f"RedditUnavailable: {type(exc).__name__}",
                subreddit=subreddit,
            )
        return exc

    # ------------------------------------------------------------------
    # RedditSource interface
    # ------------------------------------------------------------------
    async def fetch_new_posts(
        self,
        subreddit: str,
        cursor: str | None,
    ) -> tuple[list[NormalizedPost], str | None]:
        """
        Fetch new posts from a subreddit since ``cursor``.

        Uses ``subreddit.new()`` with the newest posts first. Stops once it
        reaches a post whose fullname equals the cursor. Returns posts in
        reverse-chronological order and the new cursor (the newest fetched
        fullname, or the same cursor if there is nothing new).
        """
        try:
            sub = self._reddit.subreddit(subreddit)
            posts: list[NormalizedPost] = []
            async for submission in sub.new(limit=FETCH_BATCH_LIMIT):
                fullname = f"t3_{submission.id}"
                if cursor and fullname == cursor:
                    break
                posts.append(_normalize_submission(submission))
        except Exception as exc:
            raise self._map_network_error(exc, subreddit=subreddit) from exc

        await self._throttle()
        if posts:
            return posts, posts[0].id
        return [], cursor

    async def post_comment(self, post_id: str, body: str) -> str:
        """Post a comment reply to a Reddit submission.

        Safety rule — NEVER call this directly. All posting must go through
        ``reddit_agent.posting.attempt_post`` (the posting gate), which enforces:
        DRY_RUN mode, kill switch, subreddit automation policy, and the DB
        idempotency constraint before any Reddit call happens.

        Args:
            post_id: Reddit fullname (t3_xxxxx).
            body: reply text (must have passed guardrails before calling).

        Returns:
            The reddit comment fullname (t1_xxxxx).

        Raises:
            RateLimitExceeded: Reddit returned 429.
            RedditUnavailable: Reddit returned 5xx or was unreachable.
        """
        try:
            submission_id = post_id.split("_")[-1]
            submission = await self._reddit.submission(submission_id)
            comment = await submission.reply(body)
            return f"t1_{comment.id}"
        except Exception as exc:
            raise self._map_network_error(exc) from exc

    async def close(self) -> None:
        """Release the asyncpraw HTTP session."""
        try:
            await self._reddit.close()
        except Exception:
            pass