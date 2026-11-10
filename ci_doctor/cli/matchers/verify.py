"""`ci-doctor matchers verify`."""

from pathlib import Path

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.matchers.verify import Policy, verify

_DEFAULT = Policy()


class VerifyCommand(Command):
    """Run every matcher over its fixtures and compare each output with its committed .expected file."""

    name = "verify"

    @override
    def run(
        self,
        root: Path = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml)."),
        update: bool = typer.Option(
            False,
            "--update",
            help="Write each fixture's .expected files from the current output instead of comparing.",
        ),
        min_positive: int = typer.Option(
            _DEFAULT.min_positive, "--min-positive", min=0, help="Fixtures each output must fire on."
        ),
        min_negative: int = typer.Option(
            _DEFAULT.min_negative, "--min-negative", min=0, help="Fixtures each output must stay silent on."
        ),
        exempt: str = typer.Option(
            _DEFAULT.exempt,
            "--exempt",
            help="File in the registry naming matchers held to one positive fixture instead.",
        ),
        cross_fire: bool = typer.Option(
            False, "--cross-fire", help="Fail when an output fires on another matcher's fixture."
        ),
        json_out: Path | None = typer.Option(
            None, "--json", help="Also write the full report (counts, variant coverage, cross-fires) here."
        ),
    ) -> None:
        policy = Policy(min_positive, min_negative, exempt, cross_fire)
        n = verify(root, update=update, policy=policy, json_out=json_out)
        done(f"{n} matchers verified" + (" (.expected files written; review the diff)" if update else ""))
