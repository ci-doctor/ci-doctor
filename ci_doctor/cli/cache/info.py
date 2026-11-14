"""`ci-doctor cache info`."""

import time
from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.data import JSON_OPTION, emit
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.registry.cache import Cache, ago, root_for


class InfoCommand(Command):
    """Show where the cache is, its size, and how old each registry's cached index is."""

    name = "info"
    examples = (
        "ci-doctor cache info",
        "ci-doctor cache info --json",
    )

    @dataclass(frozen=True)
    class _Registry(Row):
        registry: str
        index_age: str
        files: str

        def line(self) -> str:
            return f"{self.registry}: index fetched {self.index_age} ago, {self.files} files cached"

    @override
    def run(self, json_output: bool = JSON_OPTION) -> None:
        cfg = editable().matchers
        root = root_for(cfg)
        cache = Cache(root) if root.is_dir() else None
        blobs = cache.blob_paths() if cache else []
        cached = {p.name for p in blobs}
        now = time.time()
        rows = [
            self._Registry(
                i.location, ago(now - i.fetched_at), f"{len(i.hashes() & cached)}/{len(i.hashes())}"
            )
            for i in (cache.indexes() if cache else [])
        ]
        size = sum(p.stat().st_size for p in blobs)
        if json_output:
            emit(
                {
                    "root": str(root),
                    "enabled": cfg.cache,
                    "files": len(blobs),
                    "bytes": size,
                    "registries": rows,
                }
            )
            return
        state = "" if cfg.cache else " (off: matchers.cache is false)"
        if cache is None:
            say(f"cache: {root}{state}, empty")
            return
        say(f"cache: {root}{state}, {len(blobs)} files, {size / 1024:.0f} KiB")
        if rows:
            table(self._Registry, rows)
