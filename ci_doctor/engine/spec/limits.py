"""Every cap a matcher file is held to, in one place."""

#: Caps on what a matcher may cost, set from the 38 that once shipped, with headroom: the
#: largest RE2 program was 109, the longest pattern 104 characters.
MAX_PATTERN_CHARS = 500
MAX_PROGRAM_SIZE = 1000
DESCRIPTION_CHARS = (20, 250)

# Schema 1. Starting points, set wide enough for every row on the Matchers page.
MAX_OUTPUTS = 20
MAX_VARIANTS = 10
#: Nodes in one `match` tree, relations' inner nodes included.
MAX_NODES = 32
#: How deep `all` / `any` / `not` / relations may nest.
MAX_DEPTH = 4
#: `within` and `max_lines`.
MAX_REACH = 500
MAX_DEFINES = 20
MAX_TAGS = 5
