"""Every agent `ci-doctor agent install` knows, and how a `--target` value picks among them."""

from ci_doctor.agent.harness.targets.antigravity import Antigravity
from ci_doctor.agent.harness.targets.base import Target
from ci_doctor.agent.harness.targets.claude import ClaudeCode
from ci_doctor.agent.harness.targets.codex import Codex
from ci_doctor.agent.harness.targets.cursor import Cursor
from ci_doctor.agent.harness.targets.gemini import GeminiCli
from ci_doctor.agent.harness.targets.hermes import Hermes
from ci_doctor.agent.harness.targets.kiro import Kiro
from ci_doctor.agent.harness.targets.opencode import Opencode
from ci_doctor.agent.harness.targets.pi import Pi
from ci_doctor.errors import SetupError

#: Adding an agent: one module in `targets/` and one line here.
TARGETS: dict[str, Target] = {
    target.id: target
    for target in (
        ClaudeCode(),
        Cursor(),
        Codex(),
        Opencode(),
        Hermes(),
        GeminiCli(),
        Antigravity(),
        Kiro(),
        Pi(),
    )
}


#: Taken when no agent is found on this machine: the most common one.
FALLBACK = "claude"


def found() -> list[Target]:
    """The agents found on this machine, or Claude Code when none is (as CodeGraph's installer does).

    Returns:
        The targets, in registry order; never empty.
    """
    return [t for t in TARGETS.values() if t.detected()] or [TARGETS[FALLBACK]]


def select(spec: str) -> list[Target]:
    """The targets a `--target` value names.

    Args:
        spec: `auto` (the agents found here, Claude Code if none), `all`, or comma-separated ids.

    Returns:
        The targets, in registry order.

    Raises:
        SetupError: On an unknown id.
    """
    if spec == "auto":
        return found()
    if spec == "all":
        return list(TARGETS.values())
    ids = [part.strip() for part in spec.split(",") if part.strip()]
    if unknown := [i for i in ids if i not in TARGETS]:
        raise SetupError(f"unknown agent {unknown[0]!r}; choose from: {', '.join(TARGETS)}, auto, all")
    return [TARGETS[i] for i in TARGETS if i in ids]
