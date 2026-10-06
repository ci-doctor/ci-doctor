"""Generate the docs site's matcher catalogue and phase map from the shipped defaults.

The catalogue is the shipped packs, which change as a body, so a hand-written table
is stale by the next commit. This reads `config/defaults.yml` — each pack carries
its own `description` and `classification` — and writes the JSON the site imports.
The section->phase map is generated for the same reason.

The one thing a pack cannot say about itself is how its *ecosystem* reads as a
heading, so that lives in :data:`GROUPS` here, keyed by a pack's first
`classification` tag. An ecosystem without an entry fails the generator rather
than rendering a raw tag.

Run: ``mise run docs:data``. `test_docs_data_is_current` fails when the committed
JSON drifts from the config.
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "ci_doctor" / "config" / "defaults.yml"
OUT = ROOT / "docs" / "site" / "src" / "data" / "matchers.json"
PHASES_OUT = ROOT / "docs" / "site" / "src" / "data" / "phases.json"

#: `  step_script: script` -> ("step_script", "script"), inside the `phases:` block.
_PHASE_ENTRY = re.compile(r"^ {2}(\w+):\s*(\w+)\s*$")
#: The comment in `phases:` that separates the GitLab names from the GitHub ones.
_GITHUB_MARKER = re.compile(r"^\s*#.*GitHub", re.IGNORECASE)

#: Heading for each ecosystem, the first `classification` tag of a pack. Groups
#: render in the order their first pack appears in defaults.yml.
GROUPS = {
    "python": "Python",
    "js": "JavaScript / TypeScript",
    "go": "Go",
    "rust": "Rust",
    "jvm": "JVM (Maven / Gradle)",
    "dotnet": ".NET",
    "ruby": "Ruby",
    "php": "PHP",
    "c": "C / C++",
    "container": "Containers",
    "iac": "Infrastructure as code",
    "any": "Language-agnostic",
}


def build() -> list[dict]:
    """Read the shipped matchers into the site's render-ready shape.

    Returns:
        One entry per ecosystem, in declaration order, each with its packs.

    Raises:
        KeyError: If a pack's ecosystem has no :data:`GROUPS` heading.
    """
    matchers = yaml.safe_load(DEFAULTS.read_text())["extraction"]["matchers"]
    ordered: dict[str, list[dict]] = {}
    for m in matchers:
        eco = m["classification"][0]
        if eco not in GROUPS:
            raise KeyError(f"ecosystem {eco!r} of {m['id']!r} has no GROUPS heading in {Path(__file__).name}")
        entry = {
            "id": m["id"],
            "kind": "block" if m.get("start") else "anchor",
            "role": m.get("role", "tool"),
            "tags": m["classification"][1:],
            "match": m.get("start") or m.get("pattern") or "",
            "end": m.get("end") or "",
            "before": m.get("before", 0),
            "after": m.get("after", 0),
            "note": m["description"],
        }
        ordered.setdefault(GROUPS[eco], []).append(entry)
    return [{"group": g, "packs": p} for g, p in ordered.items()]


def build_phases() -> list[dict]:
    """Read the shipped section-name -> phase map, in declaration order.

    The map is keyed by the section names a segmenter emits, and defaults.yml
    groups them by CI system with a comment. That grouping is the useful part for
    a reader — which names come from *their* provider — so it is carried through.

    Returns:
        ``[{"section", "phase", "provider"}]``, in the order defaults.yml lists them.
    """
    out: list[dict] = []
    provider, inside = "GitLab", False
    for line in DEFAULTS.read_text().splitlines():
        if line.startswith("phases:"):
            inside = True
            continue
        if not inside:
            continue
        if line and not line.startswith((" ", "#")):
            break  # next top-level key ends the block
        if _GITHUB_MARKER.match(line):
            provider = "GitHub Actions"
        elif m := _PHASE_ENTRY.match(line):
            out.append({"section": m.group(1), "phase": m.group(2), "provider": provider})
    return out


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    catalogue, phases = build(), build_phases()
    OUT.write_text(json.dumps(catalogue, indent=2) + "\n")
    PHASES_OUT.write_text(json.dumps(phases, indent=2) + "\n")
    total = sum(len(g["packs"]) for g in catalogue)
    print(f"wrote {OUT.relative_to(ROOT)} — {total} packs in {len(catalogue)} groups")
    print(f"wrote {PHASES_OUT.relative_to(ROOT)} — {len(phases)} section names")
