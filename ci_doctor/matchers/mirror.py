"""Copy a whole registry into a folder: the one-command air-gapped setup.

Every matcher and bundle is fetched through the cache and checked against its
index hash, then written byte for byte, so the copy hashes exactly like the
original and any ci-doctor can use it as a `file://` registry, or serve it.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.lib.files import atomic_write_bytes
from ci_doctor.matchers.cache import Cache
from ci_doctor.matchers.models import INDEX
from ci_doctor.matchers.registry import (
    FILE_CAP,
    fetch_file,
    index_text,
    parse_index,
    registry_dir,
    session,
    unknown_registry,
)
from ci_doctor.matchers.validate import contained


def mirror(cfg: MatchersConfig, alias: str, dest: Path, client: httpx.Client | None = None) -> int:
    """Write every file registry `alias` serves into `dest`, the index last.

    Args:
        cfg: The `matchers:` block (its registries, cache and fetch settings).
        alias: The registry to copy.
        dest: An empty or missing folder, or an earlier mirror to refresh.
        client: The HTTP client, or None for a fresh one.

    Returns:
        How many matchers and bundles were written.

    Raises:
        SetupError: On an unknown alias, a `dest` that holds something other than
            a registry, or any fetch or hash failure (nothing is written then).
    """
    if alias not in cfg.registries:
        raise unknown_registry(alias, cfg.registries)
    if dest.exists() and (not dest.is_dir() or (any(dest.iterdir()) and not (dest / INDEX).is_file())):
        raise SetupError(f"{dest} is not an empty folder or an earlier mirror; mirror into one that is")
    location = registry_dir(alias, cfg.registries[alias])
    cache = Cache.open(cfg)
    with session(cfg, client) as http:
        text = index_text(alias, location, http, cache)
        index = parse_index(alias, location, text)
        entries = [*index.matchers.values(), *index.bundles.values()]
        with ThreadPoolExecutor(max_workers=cfg.fetch_workers, thread_name_prefix="ci-doctor-fetch") as pool:
            bodies = list(
                pool.map(
                    lambda e: fetch_file(
                        alias, location, e.path, cap=FILE_CAP, client=http, sha256=e.sha256, cache=cache
                    ),
                    entries,
                )
            )
    dest.mkdir(parents=True, exist_ok=True)
    for entry, body in zip(entries, bodies, strict=True):
        path = contained(dest, entry.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, body.encode("utf-8"))
    atomic_write_bytes(dest / INDEX, text.encode("utf-8"))  # last: an index never lists a missing file
    return len(entries)
