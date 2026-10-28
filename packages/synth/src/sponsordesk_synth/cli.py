"""Command line entry point: ``sponsordesk-synth generate``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from sponsordesk_synth.generator import generate, write

app = typer.Typer(add_completion=False, help="Generate the fictional SponsorDesk dataset.")


@app.callback()
def main() -> None:
    """SponsorDesk synthetic data tools."""


@app.command("generate")
def generate_cmd(
    out: Path = typer.Option(Path("data/generated"), help="Output directory."),  # noqa: B008
    seed: int = typer.Option(42, help="Random seed. Same seed, same bytes."),
    sponsors: int = typer.Option(30, help="Number of plan sponsors."),
) -> None:
    manifest = write(generate(seed, sponsors=sponsors), out)
    table = Table(title=f"Dataset written to {out} (seed {seed})")
    table.add_column("Entity")
    table.add_column("Count", justify="right")
    for key, value in manifest["counts"].items():
        if isinstance(value, dict):
            for sub, n in value.items():
                table.add_row(f"  {sub}", str(n))
        else:
            table.add_row(key, str(value))
    Console().print(table)
