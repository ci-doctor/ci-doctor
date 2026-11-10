"""What a matcher's output is, and how its windows rank under budget pressure."""

from typing import Literal

#: What a matcher's output is; ranks its windows (`RANK`).
#:
#: - `issue`: the environment failed (a full disk, OOM, the network), matched only on
#:   standard strings. The tool that hit it is a victim.
#: - `tool`: the failing tool printing the cause.
#: - `gate`: a deliberate check failing the job on purpose: coverage, a scanner, a formatter.
#: - `wrapper`: an orchestrator reporting that something it ran failed (npm, make, gradle).
#: - `fallback`: the generic catch-all.
Role = Literal["issue", "tool", "gate", "wrapper", "fallback"]

#: Budget rank of a role: higher survives a cut. An issue outranks the tool it broke:
#: shed the issue and the model blames the code; shed the tool and the cause is still
#: there. A gate ranks below the tool (a failing test fails coverage too) and above a
#: wrapper, which only relays a failure. A wrapper prints *after* the tool it ran
#: (`npm ERR!` trails tsc's diagnosis), so a tool must outrank it whatever their
#: positions; the fallback is the guess for a tool with no matcher at all.
RANK = {"fallback": 0, "wrapper": 1, "gate": 2, "tool": 3, "issue": 4}
