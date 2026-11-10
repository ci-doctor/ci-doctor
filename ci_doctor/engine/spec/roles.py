"""What a matcher's output is, and how its windows rank under budget pressure."""

from typing import Literal

#: What a matcher's output is; ranks its windows (`ROLE_RANK`).
Role = Literal["tool", "wrapper", "fallback"]

#: Budget rank of a matcher's role. A wrapper prints *after* the tool it ran
#: (`npm ERR!` trails tsc's diagnosis), so a tool must outrank it whatever their
#: positions; the fallback is the guess for a tool with no pack at all.
ROLE_RANK = {"fallback": 0, "wrapper": 1, "tool": 2}
