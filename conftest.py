"""Root conftest for pytest collection control.

``test_source.py`` is a non-test source module whose name collides with pytest's
``test_*.py`` pattern. Ignore it to avoid a PytestCollectionWarning while still
letting ``tests/test_sources.py`` import it.
"""

collect_ignore = ["reddit_agent/sources/test_source.py"]