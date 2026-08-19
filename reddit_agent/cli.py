import click

from reddit_agent.db.connection import get_connection, run_migrations

ENABLED_LABEL = "Kill switch: ENABLED \u26d4"
DISABLED_LABEL = "Kill switch: DISABLED \u2705"


@click.group()
def cli():
    """Reddit Troubleshooting Monitor & Reply Agent — DAXVORA-RAJAT-2026-08-A01"""
    pass


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


@cli.command("check-env")
def check_env():
    """Validate all required environment variables are set."""
    # Read .env.example, check each var is present in current env
    # Print PASS/FAIL for each — never print values
    pass