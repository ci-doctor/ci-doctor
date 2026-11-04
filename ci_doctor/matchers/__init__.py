"""Matcher registries: static directories of matchers and bundles, built and tested here.

Not `core/`: this package reads files (and, later, the network). A registry is
untrusted input, so everything it serves goes through :mod:`.validate` first.
"""
