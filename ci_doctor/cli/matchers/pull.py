"""`ci-doctor matchers pull`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.cache import root_for
from ci_doctor.matchers.registry import load


class PullCommand(Command):
    """Fetch matchers into the cache without analysing: warm a Docker image or a CI cache step."""

    name = "pull"

    @override
    def run(
        self,
        refs: Annotated[
            list[str] | None,
            typer.Argument(metavar="[ALIAS/NAME...]", help="What to fetch; matchers.include when omitted."),
        ] = None,
    ) -> None:
        cfg = editable().matchers
        if not cfg.cache:
            raise SetupError("the cache is off (matchers.cache: false); there is nothing to pull into")
        # Validated as config, so a bare name gets the same hint `include` does; `exclude` is the run's, not this.
        wanted = MatchersConfig.model_validate(
            cfg.model_dump() | {"include": refs or cfg.include, "exclude": []}
        )
        specs = load(wanted)
        registries = len({spec.id.split("/", 1)[0] for spec in specs})
        done(
            f"cached {len(specs)} matchers from {registries} registr{'y' if registries == 1 else 'ies'} in {root_for(cfg)}"
        )
