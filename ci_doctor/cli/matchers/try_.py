"""`ci-doctor matchers try`."""

from pathlib import Path

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.engine import Spec
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import Resolved, check_ref, fetch_matcher, session
from ci_doctor.matchers.try_ import framing_for, try_matcher
from ci_doctor.matchers.validate import load_matcher


class TryCommand(Command):
    """Run one matcher over one log and show what each output selects, exactly as verify would."""

    name = "try"

    @override
    def run(
        self,
        matcher: str = typer.Argument(..., help="A matcher.yaml file, or `<registry>/<name>`."),
        log: Path = typer.Argument(
            ..., help="The log. `.github.log` / `.gitlab.log` strip that runner's framing."
        ),
        framing: str | None = typer.Option(
            None, "--framing", help="github, gitlab or plain; overrides the log's name. Default: plain."
        ),
    ) -> None:
        if framing not in (None, "github", "gitlab", "plain"):
            raise SetupError(f"--framing must be github, gitlab or plain, not {framing!r}")
        try:
            raw = log.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            raise SetupError(f"{log}: {err}") from None
        results = try_matcher(self._load(matcher), raw, framing_for(log, framing))
        for t in results:
            variants = f" ({', '.join(t.variants)})" if t.variants else ""
            say(f"{t.output.id}{variants} [{t.output.role}]", "bold")
            if not t.lines:
                say("  no match", "dim")
            for line in t.lines:
                say(line)
        if not any(t.lines for t in results):
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
        seen = Resolved()
        with session(cfg) as http:
            return fetch_matcher(check_ref(matcher, cfg, http, seen), seen, http)
