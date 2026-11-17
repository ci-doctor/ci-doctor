"""Matcher files: what a matcher, bundle and index is, and how a matcher is tested.

Not `core/`: this package reads files. A matcher file is untrusted input, so
everything goes through :mod:`.validate` first. Fetching matchers from a registry is
`registry/`, which imports the rest of this package and never the other way round.
"""
