"""`ci-doctor matchers verify`."""

from pathlib import Path
from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._console.output.message import done
from ci_doctor.cli._lib.command import Command
from ci_doctor.matchers.verify import Policy, verify

_DEFAULT = Policy()


class VerifyCommand(Command):
    """Run every matcher over its fixtures and compare each output with its committed .expected file.

    `--only` checks just the matchers or outputs it names, with every rule; cross-fire
    still runs them over every fixture in the registry. A mismatch prints a diff.
    """

    name = "verify"
    examples = (
        "ci-doctor matchers verify   # the registry in this folder",
        "ci-doctor matchers verify --update   # rewrite the .expected files, then review them",
        "ci-doctor matchers verify --only ruff --only pytest.failures",
        "ci-doctor matchers verify --update --only ruff   # rewrite only ruff's .expected files",
        "ci-doctor matchers verify --cross-fire --json verify.json",
    )

    @override
    def run(
        self,
        root: Annotated[
            Path, typer.Argument(help="The registry directory (the one holding index.yaml).")
        ] = Path("."),
        update: Annotated[
            bool,
            typer.Option(
                "--update",
                help="Write each fixture's .expected files from the current output instead of comparing.",
            ),
        ] = False,
        min_positive: Annotated[
            int, typer.Option("--min-positive", min=0, help="Fixtures each output must fire on.")
        ] = _DEFAULT.min_positive,
        min_negative: Annotated[
            int, typer.Option("--min-negative", min=0, help="Fixtures each output must stay silent on.")
        ] = _DEFAULT.min_negative,
        exempt: Annotated[
            str,
            typer.Option(
                "--exempt", help="File in the registry naming matchers held to one positive fixture instead."
            ),
        ] = _DEFAULT.exempt,
        cross_fire: Annotated[
            bool, typer.Option("--cross-fire", help="Fail when an output fires on another matcher's fixture.")
        ] = False,
        only: Annotated[
            list[str] | None,
            typer.Option(
                "--only", help="Check only this matcher (NAME) or output (NAME.OUTPUT). Repeatable."
            ),
        ] = None,
        json_out: Annotated[
            Path | None,
            typer.Option(
                "--json", help="Also write the full report (counts, variant coverage, cross-fires) here."
            ),
        ] = None,
    ) -> None:
        policy = Policy(min_positive, min_negative, exempt, cross_fire)
        n = verify(root, update=update, policy=policy, json_out=json_out, only=only or None)
        done(f"{n} matchers verified" + (" (.expected files written; review the diff)" if update else ""))
