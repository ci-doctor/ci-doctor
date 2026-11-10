"""Every cap a matcher file is held to, in one place."""

#: Caps on what a matcher may cost, set from the 38 that once shipped, with headroom: the
#: largest RE2 program was 109, the longest pattern 104 characters.
MAX_PATTERN_CHARS = 500
MAX_PROGRAM_SIZE = 1000
DESCRIPTION_CHARS = (20, 250)
