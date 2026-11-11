"""Expanding a matcher's own `define` entries: `%{NAME}` in a regex, `$NAME` in a template.

The engine owns only the syntax, frozen with each schema version. What `$FILE` means
is the matcher's to say, so a registry controls its own behaviour: widening a shape
is a PR on that matcher, proven by its fixtures.
"""

import re
from collections.abc import Mapping

from ci_doctor.engine.errors import CompileError

_REF = re.compile(r"%\{([A-Z][A-Z0-9_]*)\}")
#: In a template: `$$`, `$NAME`, `...`, or a stray `$`.
_TOKEN = re.compile(r"\$\$|\$([A-Z][A-Z0-9_]*)|\.\.\.|\$")
#: What RE2 would read as syntax. Escaping only these keeps RE2 happy: it rejects
#: `\` before a letter or a space it doesn't know, which Python's `re.escape` emits.
_SPECIAL = frozenset("\\.^$|?*+()[]{}")


def _lookup(name: str, defines: Mapping[str, str], path: str) -> str:
    """One define, wrapped so its alternation can't leak into the pattern around it.

    Args:
        name: The placeholder.
        defines: The matcher's `define`.
        path: Where the reference is, for the error.

    Returns:
        `(?:<value>)`.

    Raises:
        CompileError: If `name` isn't defined.
    """
    if name not in defines:
        known = ", ".join(sorted(defines)) or "none"
        raise CompileError(path, f"{name} is not defined (define: {known})")
    return f"(?:{defines[name]})"


def expand_regex(regex: str, defines: Mapping[str, str], path: str) -> str:
    """Replace each `%{NAME}` with its define.

    Args:
        regex: As written.
        defines: The matcher's `define`.
        path: Where it is, for errors.

    Returns:
        Plain RE2.

    Raises:
        CompileError: On an undefined name.
    """
    return _REF.sub(lambda m: _lookup(m.group(1), defines, path), regex)


def escape(text: str) -> str:
    """Literal text as RE2.

    Args:
        text: Any text.

    Returns:
        A pattern matching exactly it.
    """
    return "".join(f"\\{c}" if c in _SPECIAL else c for c in text)


def expand_template(template: str, defines: Mapping[str, str], path: str) -> str:
    """Turn a template into RE2: literal text, `$NAME` a define, `...` anything, `$$` a `$`.

    Args:
        template: As written.
        defines: The matcher's `define`.
        path: Where it is, for errors.

    Returns:
        Plain RE2, matching anywhere in a line like a regex does.

    Raises:
        CompileError: On an undefined name, or a `$` that starts neither `$NAME` nor `$$`.
    """
    out, at = [], 0
    for m in _TOKEN.finditer(template):
        out.append(escape(template[at : m.start()]))
        piece = m.group(0)
        if piece == "$$":
            out.append(r"\$")
        elif piece == "...":
            out.append(".*")
        elif m.group(1):
            out.append(_lookup(m.group(1), defines, path))
        else:
            raise CompileError(path, f"a `$` must start `$NAME` (uppercase) or be written `$$`: {template!r}")
        at = m.end()
    out.append(escape(template[at:]))
    return "".join(out)
