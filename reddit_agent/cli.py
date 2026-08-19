import click

from reddit_agent.db.connection import get_connection, run_migrations


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


@cli.command("check-env")
def check_env():
    """Validate all required environment variables are set."""
    # Read .env.example, check each var is present in current env
    # Print PASS/FAIL for each — never print values
    pass