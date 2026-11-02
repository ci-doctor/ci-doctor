"""Generate the docs site's phase map from the shipped defaults.

The section->phase map is data in `config/defaults.yml`, and a hand-written table
is stale by the next commit, so the site imports this generated JSON instead.
(The matcher catalogue lived here too; matchers now come from registries, and
their catalogue is https://matchers.cidoc.dev.)

Run: ``mise run docs:data``. `test_docs_phase_map_is_current` fails when the
committed JSON drifts from the config.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "ci_doctor" / "config" / "defaults.yml"
PHASES_OUT = ROOT / "docs" / "site" / "src" / "data" / "phases.json"

#: `  step_script: script` -> ("step_script", "script"), inside the `phases:` block.
_PHASE_ENTRY = re.compile(r"^ {2}(\w+):\s*(\w+)\s*$")
#: The comment in `phases:` that separates the GitLab names from the GitHub ones.
_GITHUB_MARKER = re.compile(r"^\s*#.*GitHub", re.IGNORECASE)


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
    phases = build_phases()
    PHASES_OUT.write_text(json.dumps(phases, indent=2) + "\n")
    print(f"wrote {PHASES_OUT.relative_to(ROOT)} — {len(phases)} section names")
