"""`ci-doctor matchers examine`."""

from pathlib import Path

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.engine import Spec
from ci_doctor.errors import SetupError
from ci_doctor.matchers.cache import Cache
from ci_doctor.matchers.examine import examine
from ci_doctor.matchers.logs import Framing, framing_of
from ci_doctor.matchers.registry import Resolved, check_ref, fetch_matcher, session
from ci_doctor.matchers.validate import load_matcher


class ExamineCommand(Command):
    """Run one matcher over one log and show what each output selects, exactly as verify would."""

    name = "examine"

    @override
    def run(
        self,
        matcher: str = typer.Argument(..., help="A matcher.yaml file, or `<registry>/<name>`."),
        log: Path = typer.Argument(
            ..., help="The log. `.github.log` / `.gitlab.log` strip that runner's framing."
        ),
        framing: Framing | None = typer.Option(
            None, "--framing", help="The runner framing to strip; overrides the log's name. Default: plain."
        ),
    ) -> None:
        try:
            raw = log.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            raise SetupError(f"{log}: {err}") from None
        results = examine(self._load(matcher), raw, framing or framing_of(log))
        for e in results:
            variants = f" ({', '.join(e.variants)})" if e.variants else ""
            say(f"{e.output.id}{variants} [{e.output.role}]", "bold")
            if not e.lines:
                say("  no match", "dim")
            for line in e.lines:
                say(line)
        if not any(e.lines for e in results):
            say("nothing matched", "yellow")

    @staticmethod
    def _load(matcher: str) -> Spec:
        """A matcher file on disk, or one a configured registry serves.

        Args:
            matcher: A path, or `<registry>/<name>`.

        Returns:
            The loaded matcher.
        """
        path = Path(matcher)
        if path.is_file():
            return load_matcher(path)
        cfg = editable().matchers
        seen = Resolved(cache=Cache.open(cfg))
        with session(cfg) as http:
            return fetch_matcher(check_ref(matcher, cfg, http, seen), seen, http)
