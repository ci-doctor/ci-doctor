"""ci-doctor's own exceptions. Only `SetupError` may change the exit code.

Invariant #3: a broken *setup* (a registry that fails its own checks, an
unreachable registry, an invalid config) exits 1, so the person who broke it
sees a red step. Anything that goes wrong once analysis has started still exits
0. `main()` maps this class to 1 and everything else to 0.
"""


class SetupError(Exception):
    """A setup problem the user must fix; the message says what and where."""


class LogUnavailable(Exception):
    """A job's log exists, or may exist, but could not be read: a refusal, an outage, a timeout.

    Not the same as a job that has no log. A job that never got a runner wrote
    none, and a provider answers that with `None`; attribution reads it as "never
    ran". A log that could not be *read* says nothing about the job, so it is an
    error the analysis carries on past (invariant #3), recorded on the job.
    """
