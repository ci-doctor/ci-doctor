"""Reading a log the way `analyze` does, with its framing declared rather than guessed.

`<case>.github.log` is a raw GitHub Actions log, `<case>.gitlab.log` a raw GitLab trace,
and any other `.log` plain tool output. A framed log is segmented by that provider's
segmenter and attributed, and only the blamed section is kept, as `analyze` does; then the
lines are denoised. `verify` reads fixtures this way and `examine`
reads a user's log this way, so both see what production sees.
"""

from functools import cache
from pathlib import Path
from typing import Literal

from ci_doctor.config.loader import default_config
from ci_doctor.core.analyze import evidence_lines
from ci_doctor.core.attribution import attribute
from ci_doctor.core.denoise import denoise
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from ci_doctor.providers.registry import segmenter_for

Framing = Literal["github", "gitlab", "plain"]
FRAMINGS: tuple[Framing, ...] = ("github", "gitlab", "plain")


#: The shipped config, read once: it is the same for every fixture, and it is all a
#: fixture is read with (no repo file, no env), so a verify run is reproducible anywhere.
_shipped = cache(default_config)


def framing_of(path: Path) -> Framing:
    """A fixture's framing, from its name.

    Args:
        path: `<case>.github.log`, `<case>.gitlab.log` or `<case>.log`.

    Returns:
        The framing its name declares.
    """
    tag = path.name.removesuffix(".log").rpartition(".")[2]
    return tag if tag in FRAMINGS else "plain"


def read_log(raw: str, framing: Framing) -> list[str]:
    """A log as the engine receives it in a real run of a failed job.

    A framed log goes the way `analyze` takes it: segmented, phases assigned,
    attributed, and only the blamed section kept. Plain output is one section.

    Args:
        raw: The log text.
        framing: Which runner's framing to strip; "plain" strips none.

    Returns:
        The lines the engine would match, denoised.
    """
    cfg = _shipped()
    if framing == "plain":
        return denoise(raw.splitlines(), cfg.denoise)
    sections = segmenter_for(framing).segment(raw)
    assign_phases(sections, cfg.phases)
    job = Job(
        id="fixture", name="fixture", status="failed", failure_reason=FailureReason.SCRIPT_FAILURE, log=raw
    )
    return denoise(evidence_lines(sections, attribute(job, sections).phase), cfg.denoise)


def read_fixture(path: Path) -> list[str]:
    """A fixture log, read with the framing its name declares.

    Args:
        path: The `.log` file.

    Returns:
        Its lines, as the engine sees them.
    """
    return read_log(path.read_text(encoding="utf-8", errors="replace"), framing_of(path))
