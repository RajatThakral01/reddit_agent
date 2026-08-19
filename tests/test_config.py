import pytest

from reddit_agent.config import AgentConfig
from reddit_agent.exceptions import ConfigError


def _set_baseline_env(monkeypatch) -> None:
    monkeypatch.setenv("SUBREDDITS", "techsupport")
    monkeypatch.setenv("KEYWORDS", "help")
    monkeypatch.setenv("WORTHINESS_THRESHOLD", "65")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("MODE", "DRY_RUN")
    monkeypatch.setenv("REDDIT_SOURCE", "test")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_COST_CAP_USD", "0.50")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/test")


def test_valid_1_subreddit(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("SUBREDDITS", "techsupport")
    config = AgentConfig()
    assert config.subreddits == ["techsupport"]


def test_valid_5_subreddits(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("SUBREDDITS", "a,b,c,d,e")
    config = AgentConfig()
    assert len(config.subreddits) == 5


def test_invalid_6_subreddits(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("SUBREDDITS", "a,b,c,d,e,f")
    with pytest.raises(ConfigError) as exc_info:
        AgentConfig()
    assert "at most 5 subreddits allowed, got 6" in str(exc_info.value)


def test_invalid_0_subreddits(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("SUBREDDITS", "")
    with pytest.raises(ConfigError) as exc_info:
        AgentConfig()
    assert "at least 1 subreddit required" in str(exc_info.value)


def test_invalid_threshold_over_100(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("WORTHINESS_THRESHOLD", "101")
    with pytest.raises(ValueError):
        AgentConfig()


def test_invalid_mode(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("MODE", "MAYBE")
    with pytest.raises(ValueError):
        AgentConfig()


def test_subreddits_whitespace_stripped(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("SUBREDDITS", " tech , support ")
    config = AgentConfig()
    assert config.subreddits == ["tech", "support"]


def test_repr_redacts_secrets(monkeypatch):
    _set_baseline_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key-12345")
    config = AgentConfig()
    assert "super-secret-key-12345" not in repr(config)
    assert "[REDACTED]" in repr(config)