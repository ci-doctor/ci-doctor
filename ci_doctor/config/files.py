"""The config files a surface reads and the one it edits, with no terminal in it.

The command line fills this from `-f` and `--write-to`; anything else that edits
a config (an MCP tool) builds its own, and both get the same rules.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ci_doctor.config.edit import edit_block, read_block
from ci_doctor.config.loader import load_config, user_config
from ci_doctor.config.schemas import Config


@dataclass(frozen=True)
class ConfigFiles:
    """Which files make up the config, and which one an edit lands in.

    Attributes:
        files: The files to layer, lowest precedence first.
        write_to: The file edits go to, or None for the user config.
    """

    files: list[Path]
    write_to: Path | None = None

    def paths(self) -> list[Path]:
        """The files to read: `files`, then `write_to` once it exists.

        Returns:
            The files, empty when none were given.
        """
        if self.write_to is not None and self.write_to.is_file() and self.write_to not in self.files:
            return [*self.files, self.write_to]
        return self.files

    def target(self) -> Path:
        """The file edits go to: `write_to`, else the user config.

        Returns:
            The path, which may not exist yet.
        """
        return self.write_to or user_config()

    def editable(self) -> Config:
        """The effective config, tolerating a `target()` that doesn't exist yet.

        Returns:
            The effective config.

        Raises:
            SetupError: If it does not validate, or a file other than the target is missing.
        """
        edited = self.target()
        return load_config([p for p in self.paths() if p != edited or p.is_file()])

    def read(self, key: str) -> dict:
        """The target file's own `key:` block, `{}` when absent.

        Args:
            key: A top-level config key.

        Returns:
            A copy of the block, `{}` when the file or the key is absent.
        """
        return read_block(self.target(), key)

    def edit(self, key: str, mutate: Callable[[dict], None]) -> None:
        """Change the target file's `key:` block in place.

        Creates the user config's directory, never a `write_to` one: a typo should fail.

        Args:
            key: A top-level config key.
            mutate: Changes the block in place.
        """
        path = self.target()
        if path == user_config():
            path.parent.mkdir(parents=True, exist_ok=True)
        edit_block(path, key, mutate)
