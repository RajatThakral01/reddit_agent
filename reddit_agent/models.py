"""Domain models shared across the pipeline (not DB models).

:class:`NormalizedPost` is the canonical representation of a Reddit post after
normalization. All pipeline stages work with this type rather than raw API
payloads, so the rest of the system has a stable data contract.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass
class NormalizedPost:
    """Canonical representation of a Reddit post after normalization.

    All pipeline stages work with this type, not raw API payloads.
    """

    id: str                    # Reddit fullname, e.g. "t3_abc123"
    title: str
    body: str                  # selftext, empty string if link post or removed
    author: str                # username, "[deleted]" if deleted
    subreddit: str             # subreddit name without r/
    created_utc: datetime
    url: str
    flair: str | None          # post flair text, None if no flair
    score: int                 # reddit upvote score
    is_locked: bool
    is_deleted: bool           # author deleted the post
    is_removed: bool           # moderator removed the post
    is_archived: bool          # too old to comment
    _scenario: str = ""        # for fixture files: human-readable scenario name

    def content_hash(self) -> str:
        """SHA-256 of title + body — used for edit detection (FR-4)."""
        return hashlib.sha256(f"{self.title}{self.body}".encode()).hexdigest()

    def is_actionable(self) -> bool:
        """Return False if the post cannot be replied to for structural reasons.

        Actionable means: not locked, not deleted, not removed, not archived,
        and has a non-empty body.
        """
        return (
            not self.is_locked
            and not self.is_deleted
            and not self.is_removed
            and not self.is_archived
            and bool(self.body.strip())
        )