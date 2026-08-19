import click


@click.group()
def cli():
    """Reddit Troubleshooting Monitor & Reply Agent — DAXVORA-RAJAT-2026-08-A01"""
    pass


@cli.command("check-env")
def check_env():
    """Validate all required environment variables are set."""
    # Read .env.example, check each var is present in current env
    # Print PASS/FAIL for each — never print values
    pass