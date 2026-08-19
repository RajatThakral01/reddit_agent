from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from reddit_agent.exceptions import ConfigError


class AgentConfig(BaseSettings):
    """Validated agent configuration loaded from environment variables.

    This is the first thing constructed when the agent starts. If the config
    is invalid, a ``ConfigError`` (or pydantic validation error) is raised and
    the agent refuses to start — no DB connection and no Reddit call is made.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        enable_decoding=False,
    )

    subreddits: list[str] = Field(
        default_factory=list,
        description="Subreddit names from SUBREDDITS env var (comma-separated).",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords from KEYWORDS env var (comma-separated).",
    )
    worthiness_threshold: int = Field(
        default=65, ge=0, le=100, description="0-100 worthiness gate."
    )
    poll_interval_seconds: int = Field(default=300, description="Polling interval.")
    mode: Literal["DRY_RUN", "LIVE"] = Field(
        default="DRY_RUN", description="Run mode: DRY_RUN (never posts) or LIVE."
    )
    reddit_source: Literal["test", "live"] = Field(
        default="test", description="Data source: test (fixture) or live (Reddit API)."
    )
    llm_provider: str = Field(default="deepseek", description="LLM provider name.")
    llm_api_key: str = Field(default="", description="LLM API key. Secret.")
    llm_model: str = Field(default="", description="Exact LLM model name.")
    llm_cost_cap_usd: float = Field(default=0.50, description="Per-run LLM spend cap.")
    database_url: str = Field(
        default="postgresql://localhost:5432/reddit_agent",
        description="Postgres connection string.",
    )
    reddit_client_id: str | None = Field(
        default=None, description="Reddit OAuth client id (live mode only)."
    )
    reddit_client_secret: str | None = Field(
        default=None, description="Reddit OAuth client secret (live mode only)."
    )
    reddit_username: str | None = Field(
        default=None, description="Reddit account username (live mode only)."
    )
    reddit_user_agent: str | None = Field(
        default=None, description="Reddit user agent string (live mode only)."
    )
    max_retry_attempts: int = Field(default=4, description="Max retry attempts.")

    @field_validator("subreddits", "keywords", mode="before")
    @classmethod
    def _parse_comma_separated(cls, v):
        """Parse comma-separated env strings into stripped, non-empty item lists."""
        if v is None:
            return []
        if isinstance(v, list):
            items = [str(i) for i in v]
        else:
            items = str(v).split(",")
        return [item.strip() for item in items if item.strip()]

    def model_post_init(self, __context) -> None:
        """Run cross-field validation after pydantic populates the model."""
        self._validate_subreddit_count()
        self._validate_live_credentials()
        super().model_post_init(__context)

    def _validate_subreddit_count(self) -> None:
        n = len(self.subreddits)
        if n > 5:
            raise ConfigError(f"ConfigError: at most 5 subreddits allowed, got {n}")
        if n < 1:
            raise ConfigError("ConfigError: at least 1 subreddit required")

    def _validate_live_credentials(self) -> None:
        if self.reddit_source != "live":
            return
        required = {
            "reddit_client_id": self.reddit_client_id,
            "reddit_client_secret": self.reddit_client_secret,
            "reddit_username": self.reddit_username,
            "reddit_user_agent": self.reddit_user_agent,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigError(
                f"ConfigError: reddit_source=live requires: {', '.join(missing)}"
            )

    def __repr__(self) -> str:
        """Representation that never leaks secrets: API key and Reddit credentials."""
        secret_redaction = "[REDACTED]"
        return (
            f"AgentConfig("
            f"subreddits={self.subreddits!r}, "
            f"keywords={self.keywords!r}, "
            f"worthiness_threshold={self.worthiness_threshold!r}, "
            f"poll_interval_seconds={self.poll_interval_seconds!r}, "
            f"mode={self.mode!r}, "
            f"reddit_source={self.reddit_source!r}, "
            f"llm_provider={self.llm_provider!r}, "
            f"llm_api_key={secret_redaction}, "
            f"llm_model={self.llm_model!r}, "
            f"llm_cost_cap_usd={self.llm_cost_cap_usd!r}, "
            f"database_url={self.database_url!r}, "
            f"reddit_client_id={secret_redaction}, "
            f"reddit_client_secret={secret_redaction}, "
            f"reddit_username={self.reddit_username!r}, "
            f"reddit_user_agent={self.reddit_user_agent!r}, "
            f"max_retry_attempts={self.max_retry_attempts!r})"
        )