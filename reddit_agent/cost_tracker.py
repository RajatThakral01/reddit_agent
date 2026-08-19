"""In-memory cost tracker for the current run.

Tracks cumulative LLM spend and aborts the run (via ``CostCapExceeded``) when the
configured cap is exceeded. This is intentionally a simple in-memory counter —
resets on process restart (per FR-14).
"""

from reddit_agent.exceptions import CostCapExceeded


class CostTracker:
    """
    Tracks cumulative LLM cost for the current run.
    Raises CostCapExceeded if the cap is exceeded.
    This is a simple in-memory counter — resets on process restart (intentional).
    """

    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.total_usd = 0.0
        self.call_count = 0

    def add(self, cost_usd: float, tokens_used: int = 0):
        """Add cost. Raises CostCapExceeded if total exceeds cap."""
        self.total_usd += cost_usd
        self.call_count += 1
        if self.total_usd > self.cap_usd:
            raise CostCapExceeded(
                f"CostCapExceeded: run stopped at ${self.total_usd:.4f}",
                current_cost_usd=self.total_usd,
                cap_usd=self.cap_usd,
            )

    def summary(self) -> dict:
        """Return a summary of the run's spend so far."""
        return {
            "total_usd": self.total_usd,
            "call_count": self.call_count,
            "cap_usd": self.cap_usd,
        }