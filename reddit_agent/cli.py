import asyncio
import os
import re
import sys
from pathlib import Path

import click

from reddit_agent.config import AgentConfig
from reddit_agent.cost_tracker import CostTracker
from reddit_agent.db.connection import get_connection, run_migrations
from reddit_agent.exceptions import ConfigError, CostCapExceeded
from reddit_agent.pipeline import run_poll_cycle
from reddit_agent.sources import get_source

ENABLED_LABEL = "Kill switch: ENABLED \u26d4"
DISABLED_LABEL = "Kill switch: DISABLED \u2705"
ENV_VAR_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=")


@click.group()
def cli():
    """Reddit Troubleshooting Monitor & Reply Agent — DAXVORA-RAJAT-2026-08-A01"""
    pass


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@cli.command("run")
@click.option("--once", is_flag=True, help="Run one poll cycle and exit")
def run(once: bool):
    """Start the polling loop."""

    async def execute():
        try:
            config = AgentConfig()
        except (ConfigError, ValueError) as exc:
            click.echo(f"Config error: {exc}", err=True)
            raise SystemExit(1)

        with get_connection() as conn:
            run_migrations(conn)
            source = get_source(config)
            tracker = CostTracker(config.llm_cost_cap_usd)
            try:
                while True:
                    summary = await run_poll_cycle(config, source, tracker, conn)
                    click.echo(f"Poll cycle complete: {summary}")
                    if once:
                        break
                    await asyncio.sleep(config.poll_interval_seconds)
            finally:
                await source.close()

    try:
        asyncio.run(execute())
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    except CostCapExceeded as exc:
        click.echo(f"{exc}", err=True)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------


@cli.group()
def db():
    """Database commands."""
    pass


@db.command("migrate")
def db_migrate():
    """Apply all database migrations."""
    with get_connection() as conn:
        run_migrations(conn)
    click.echo("Migrations complete")


# ---------------------------------------------------------------------------
# kill-switch
# ---------------------------------------------------------------------------


@cli.group()
def kill_switch():
    """Kill switch commands. Stops all new post actions immediately."""
    pass


@kill_switch.command("enable")
def kill_switch_enable():
    """Enable the kill switch — blocks all new post actions."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE kill_switch SET enabled=TRUE, updated_at=NOW() WHERE id=1")
        conn.commit()
    click.echo(ENABLED_LABEL)


@kill_switch.command("disable")
def kill_switch_disable():
    """Disable the kill switch — posting may resume."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE kill_switch SET enabled=FALSE, updated_at=NOW() WHERE id=1")
        conn.commit()
    click.echo(DISABLED_LABEL)


@kill_switch.command("status")
def kill_switch_status():
    """Print current kill switch state."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT enabled FROM kill_switch WHERE id=1")
            row = cur.fetchone()
    enabled = row is not None and row[0]
    click.echo(ENABLED_LABEL if enabled else DISABLED_LABEL)


# ---------------------------------------------------------------------------
# check-env
# ---------------------------------------------------------------------------


@cli.command("check-env")
def check_env():
    """Validate all required environment variables are set."""
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    missing: list[str] = []
    checked = 0

    if not env_example.exists():
        click.echo(f"❌ .env.example not found at {env_example}", err=True)
        raise SystemExit(1)

    for line in env_example.read_text(encoding="utf-8").splitlines():
        match = ENV_VAR_RE.match(line.strip())
        if not match:
            continue
        name = match.group(1)
        checked += 1
        if not os.environ.get(name):
            missing.append(name)
            click.echo(f"❌ {name} (missing)")
        else:
            click.echo(f"✅ {name}")

    if missing:
        click.echo(f"Missing {len(missing)} of {checked} required variables.")
        raise SystemExit(1)
    click.echo(f"All {checked} environment variables present.")