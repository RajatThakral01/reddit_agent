class RedditAgentError(Exception):
    """Base exception for all Reddit Agent errors."""


class ConfigError(RedditAgentError):
    """
    Raised when the agent configuration is invalid.
    Examples: subreddit count out of range, missing required field,
    invalid mode value.
    """


class RateLimitExceeded(RedditAgentError):
    """
    Raised when Reddit returns a 429 response.
    Attributes:
        retry_after_seconds (int): seconds to wait before retrying,
        read from X-Ratelimit-Reset header.
        attempt (int): which retry attempt this is.
        max_attempts (int): the configured maximum.
    """

    def __init__(self, message, retry_after_seconds=0, attempt=0, max_attempts=0):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.attempt = attempt
        self.max_attempts = max_attempts


class RedditUnavailable(RedditAgentError):
    """
    Raised when Reddit API returns 5xx or is unreachable after max retries.
    Attributes:
        subreddit (str): the subreddit that was being polled.
    """

    def __init__(self, message, subreddit=""):
        super().__init__(message)
        self.message = message
        self.subreddit = subreddit


class AuthRefreshFailed(RedditAgentError):
    """
    Raised when the OAuth2 token refresh fails.
    This should pause the affected subreddit's polling, NOT crash the process.
    Attributes:
        subreddit (str): the subreddit whose polling should be paused.
    """

    def __init__(self, message, subreddit=""):
        super().__init__(message)
        self.message = message
        self.subreddit = subreddit


class GenerationFailed(RedditAgentError):
    """
    Raised when the LLM fails to generate a valid response after max retries.
    Attributes:
        post_id (str): the Reddit post ID that failed generation.
        attempt (int): how many attempts were made.
    """

    def __init__(self, message, post_id="", attempt=0):
        super().__init__(message)
        self.message = message
        self.post_id = post_id
        self.attempt = attempt


class GuardrailBlocked(RedditAgentError):
    """
    Raised when a generated reply is blocked by the guardrail system.
    This is NOT retried — the item is permanently marked blocked_by_guardrail.
    Attributes:
        rule_name (str): the specific guardrail rule that was violated.
        post_id (str): the Reddit post ID.
    """

    def __init__(self, message, rule_name="", post_id=""):
        super().__init__(message)
        self.message = message
        self.rule_name = rule_name
        self.post_id = post_id


class KillSwitchActive(RedditAgentError):
    """
    Raised when a post action is attempted but the kill switch is enabled.
    Attributes:
        post_id (str): the post that was being processed.
    """

    def __init__(self, message, post_id=""):
        super().__init__(message)
        self.message = message
        self.post_id = post_id


class BlockedBySubredditPolicy(RedditAgentError):
    """
    Raised when a post is blocked because automation_allowed=false for that subreddit.
    Attributes:
        subreddit (str): the subreddit name.
        post_id (str): the post that was being processed.
    """

    def __init__(self, message, subreddit="", post_id=""):
        super().__init__(message)
        self.message = message
        self.subreddit = subreddit
        self.post_id = post_id


class DuplicateReplyPrevented(RedditAgentError):
    """
    Raised when the DB unique constraint prevents a duplicate reply insertion.
    This is a normal, expected event — not a system error. It should be logged
    as an informational event, not treated as a crash.
    Attributes:
        post_id (str): the post that was already replied to.
    """

    def __init__(self, message, post_id=""):
        super().__init__(message)
        self.message = message
        self.post_id = post_id


class CostCapExceeded(RedditAgentError):
    """
    Raised when the cumulative LLM spend for this run exceeds LLM_COST_CAP_USD.
    When raised, all further LLM calls for the run must be aborted.
    Attributes:
        current_cost_usd (float): how much has been spent.
        cap_usd (float): the configured cap.
    """

    def __init__(self, message, current_cost_usd=0.0, cap_usd=0.0):
        super().__init__(message)
        self.message = message
        self.current_cost_usd = current_cost_usd
        self.cap_usd = cap_usd


class UnactionableContent(RedditAgentError):
    """
    Raised when a post cannot be acted on due to its state.
    Examples: locked, archived, deleted, removed, empty body, malformed payload.
    Attributes:
        post_id (str): the Reddit fullname ID.
        reason (str): one of: locked, archived, deleted, removed, empty_body, malformed
    """

    def __init__(self, message, post_id="", reason=""):
        super().__init__(message)
        self.message = message
        self.post_id = post_id
        self.reason = reason