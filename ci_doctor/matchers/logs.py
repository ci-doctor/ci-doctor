"""Reading a log the way `analyze` does, with its framing declared rather than guessed.

`<case>.github.log` is a raw GitHub Actions log, `<case>.gitlab.log` a raw GitLab trace,
and any other `.log` plain tool output. The framing is stripped by that provider's
segmenter, then the lines are denoised. `verify` reads fixtures this way and `examine`
reads a user's log this way, so both see what production sees.
"""

from functools import cache
from pathlib import Path
from typing import Literal

from ci_doctor.config.loader import default_config
from ci_doctor.core.denoise import denoise
from ci_doctor.core.models import walk_sections
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
    """A log as the engine receives it in a real run.

    Args:
        raw: The log text.
        framing: Which runner's framing to strip; "plain" strips none.

    Returns:
        Every content line in log order, framing stripped and denoised.
    """
    cfg = _shipped()
    if framing == "plain":
        return denoise(raw.splitlines(), cfg.denoise)
    sections = segmenter_for(framing).segment(raw)
    # Log order, not tree order: a segmenter files some lines under a synthetic
    # trailer that walks before later steps, which would skew before/after and `until`.
    lines = sorted((line for sec in walk_sections(sections) for line in sec.lines), key=lambda ln: ln.number)
    return denoise([line.text for line in lines], cfg.denoise)


def read_fixture(path: Path) -> list[str]:
    """A fixture log, read with the framing its name declares.

    Args:
        path: The `.log` file.

    Returns:
        Its lines, as the engine sees them.
    """
    return read_log(path.read_text(encoding="utf-8", errors="replace"), framing_of(path))
