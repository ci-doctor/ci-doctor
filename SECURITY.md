# Security policy

Don't open a public issue. Email **work@cidoc.dev** with what's wrong, how to reproduce
it, and the output of `ci-doctor report`. You'll get a reply within 5 working days.
Please wait for the fix before disclosing.

Only the latest release gets security fixes.

In scope: secrets reaching the report, the MR note or an LLM prompt unredacted; a
registry fetch escaping its folder or host; any write beyond the single MR/PR note; a
crafted log or matcher that hangs ci-doctor or changes a job's exit code. A misfiring
matcher belongs to [ci-doctor/matchers](https://github.com/ci-doctor/matchers).
