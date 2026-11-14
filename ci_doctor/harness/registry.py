"""Every agent `ci-doctor mcp install` knows, and how a `--target` value picks among them."""

from ci_doctor.errors import SetupError
from ci_doctor.harness.targets.antigravity import Antigravity
from ci_doctor.harness.targets.base import Target
from ci_doctor.harness.targets.claude import ClaudeCode
from ci_doctor.harness.targets.codex import Codex
from ci_doctor.harness.targets.cursor import Cursor
from ci_doctor.harness.targets.gemini import GeminiCli
from ci_doctor.harness.targets.hermes import Hermes
from ci_doctor.harness.targets.kiro import Kiro
from ci_doctor.harness.targets.opencode import Opencode

#: Adding an agent: one module in `targets/` and one line here.
TARGETS: dict[str, Target] = {
    target.id: target
    for target in (ClaudeCode(), Cursor(), Codex(), Opencode(), Hermes(), GeminiCli(), Antigravity(), Kiro())
}


def select(spec: str) -> list[Target]:
    """The targets a `--target` value names.

    Args:
        spec: `auto` (the agents found on this machine), `all`, or comma-separated ids.

    Returns:
        The targets, in registry order.

    Raises:
        SetupError: On an unknown id.
    """
    if spec == "auto":
        return [t for t in TARGETS.values() if t.detected()]
    if spec == "all":
        return list(TARGETS.values())
    ids = [part.strip() for part in spec.split(",") if part.strip()]
    if unknown := [i for i in ids if i not in TARGETS]:
        raise SetupError(f"unknown agent {unknown[0]!r}; choose from: {', '.join(TARGETS)}, auto, all")
    return [TARGETS[i] for i in TARGETS if i in ids]
