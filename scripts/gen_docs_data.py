"""Generate the docs site's data: the phase map, and the matcher format's field tables.

The section->phase map is data in `config/defaults.yml`, and the matcher format's
fields live on the engine's spec models. A hand-written table is stale by the next
commit, so the site imports this generated JSON instead. (The matcher catalogue is
not here: matchers come from registries, and theirs is https://matchers.cidoc.dev.)

Run: ``mise run docs:data``. `tests/test_docs_data.py` fails when the committed JSON
drifts from its source.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "ci_doctor" / "config" / "defaults.yml"
PHASES_OUT = ROOT / "docs" / "site" / "src" / "data" / "phases.json"
MATCHER_OUT = ROOT / "docs" / "site" / "src" / "data" / "matcher-v1.json"

#: The format page's tables: a heading each, and the spec model behind it.
_MATCHER_TABLES = {
    "matcher": "Matcher",
    "output": "Output",
    "variant": "Variant",
    "window": "Window",
    "near": "Near",
    "block": "Block",
}

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


def build_matcher_fields() -> dict[str, list[dict]]:
    """Every field of a schema 1 matcher file, by the part of the file it sits in.

    Read from the published JSON Schema, by alias, so the docs show exactly the keys
    a file uses (`schema`, `from`, `while`), with the descriptions on the models.

    Returns:
        ``{table: [{"field", "required", "description"}]}``, fields in model order.
    """
    from ci_doctor.engine import MatcherV1

    schema = MatcherV1.model_json_schema(by_alias=True)
    defs = schema["$defs"] | {"Matcher": schema}
    out = {}
    for table, model in _MATCHER_TABLES.items():
        d = defs[model]
        required = set(d.get("required", []))
        out[table] = [
            {"field": name, "required": name in required, "description": prop.get("description", "")}
            for name, prop in d["properties"].items()
            if not (model == "Near" and name == "node")
        ]
    nodes = [
        {"field": key, "description": defs[model]["properties"][key].get("description") or defs[model].get("description", "")}
        for key, model in (
            ("regex", "RegexNode"),
            ("template", "TemplateNode"),
            ("all", "AllNode"),
            ("any", "AnyNode"),
            ("not", "NotNode"),
            ("inside", "InsideNode"),
            ("preceded_by", "PrecededByNode"),
            ("followed_by", "FollowedByNode"),
        )
    ]
    out["node"] = [{"field": n["field"], "required": False, "description": n["description"] or ""} for n in nodes]
    return out


if __name__ == "__main__":
    phases = build_phases()
    PHASES_OUT.write_text(json.dumps(phases, indent=2) + "\n")
    print(f"wrote {PHASES_OUT.relative_to(ROOT)} — {len(phases)} section names")
    fields = build_matcher_fields()
    MATCHER_OUT.write_text(json.dumps(fields, indent=2) + "\n")
    print(f"wrote {MATCHER_OUT.relative_to(ROOT)} — {sum(map(len, fields.values()))} fields")
