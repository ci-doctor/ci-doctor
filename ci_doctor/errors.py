"""The one error class allowed to change ci-doctor's exit code.

Invariant #3: a broken *setup* (a registry that fails its own checks, an
unreachable registry, an invalid config) exits 1, so the person who broke it
sees a red step. Anything that goes wrong once analysis has started still exits
0. `main()` maps this class to 1 and everything else to 0.
"""


class SetupError(Exception):
    """A setup problem the user must fix; the message says what and where."""
