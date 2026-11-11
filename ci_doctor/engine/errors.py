"""The two ways a matcher file can be refused once it parses."""


class CompileError(ValueError):
    """A matcher that validates but can't be compiled: an undefined name, a regex RE2 rejects.

    Attributes:
        path: Where in the file, e.g. `ruff › outputs.check › variants.full › match.all[1]`.
        message: What is wrong there.
    """

    def __init__(self, path: str, message: str) -> None:
        """Keep the path and the message apart, and join them for `str()`.

        Args:
            path: Where in the file.
            message: What is wrong there.
        """
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


class UnknownSchema(ValueError):
    """A matcher written in a schema version this ci-doctor doesn't know.

    Attributes:
        version: The `schema` the file declares.
    """

    def __init__(self, version: object) -> None:
        """Record the version.

        Args:
            version: The `schema` the file declares; None when it declares none.
        """
        self.version = version
        if version is None:
            super().__init__("has no `schema`; write `schema: 1` (docs.cidoc.dev/reference/matcher-format/)")
        else:
            super().__init__(f"needs a newer ci-doctor (schema {version})")
