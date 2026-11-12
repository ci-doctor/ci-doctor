"""`ci-doctor matchers cache info`."""

import time
from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.matchers.cache import Cache, ago, root_for


class InfoCommand(Command):
    """Show where the cache is, its size, and how old each registry's cached index is."""

    name = "info"

    @dataclass(frozen=True)
    class _Registry(Row):
        registry: str
        index_age: str
        files: str

        def line(self) -> str:
            return f"{self.registry}: index fetched {self.index_age} ago, {self.files} files cached"

    @override
    def run(self) -> None:
        cfg = editable().matchers
        root = root_for(cfg)
        state = "" if cfg.cache else " (off: matchers.cache is false)"
        if not root.is_dir():
            say(f"cache: {root}{state}, empty")
            return
        cache = Cache(root)
        blobs = cache.blob_paths()
        size = sum(p.stat().st_size for p in blobs)
        say(f"cache: {root}{state}, {len(blobs)} files, {size / 1024:.0f} KiB")
        cached = {p.name for p in blobs}
        now = time.time()
        rows = [
            self._Registry(
                i.location, ago(now - i.fetched_at), f"{len(i.hashes() & cached)}/{len(i.hashes())}"
            )
            for i in cache.indexes()
        ]
        if rows:
            table(self._Registry, rows)
