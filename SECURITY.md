# Security policy

## Reporting a vulnerability

Please **don't open a public issue**. Email **work@cidoc.dev** with:

- what the problem is and what an attacker could do with it,
- the steps or a proof of concept that shows it,
- the version (`ci-doctor --version`) and how you run it (pip, Docker image, CI job).

You'll get a reply within 5 working days. We'll keep you updated while we work on a fix,
and credit you in the release notes unless you'd rather stay anonymous. Please give us
a chance to ship the fix before you disclose it publicly.

## Supported versions

ci-doctor is in alpha. Only the latest release gets security fixes. Upgrade before
reporting if you can.

## What's in scope

ci-doctor reads untrusted input: CI job logs, and matcher files fetched from registries.
We especially want to hear about:

- secrets from a log reaching the report, the MR/PR note or an LLM prompt unredacted,
- a registry file escaping its folder, or a fetch going somewhere it shouldn't,
- anything that makes ci-doctor write to your CI or git host beyond its single MR/PR note,
- a crafted log or matcher that makes ci-doctor hang, crash the job, or change its exit code.

Matcher files themselves (a regex that misfires) belong to
[ci-doctor/matchers](https://github.com/ci-doctor/matchers).
