from pathlib import Path
import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def build() -> None:
    """Build derived geography tables. Source adapters to be implemented by Codex."""
    typer.echo("Build pipeline scaffold created; implement source adapters per docs/SPEC.md")


@app.command()
def qa() -> None:
    """Run QA checks against built outputs."""
    typer.echo("QA pipeline scaffold created")


if __name__ == "__main__":
    app()
