"""`ci-doctor matchers …`: the registry author's commands and the user's `matchers:` editor.

Every command exits 1 on failure (a `SetupError`, invariant #3; a crash too, see
`_SetupGroup`), so a registry's CI step goes red on a stale index and a user typo never
silently edits the config. Commands that change the config touch only its `matchers:`
block (`config_edit.py`) and fetch before they write, so a bad registry or name leaves
the file as it was.
"""

import click
import typer
from typer.core import TyperGroup

from ci_doctor.errors import SetupError
from ci_doctor.matchers.cli import add, index, info, list, remove, search, verify
from ci_doctor.matchers.cli.registry.cli import app as registry_app


class _SetupGroup(TyperGroup):
    """Every failure of a `matchers` command is a setup failure, a crash included.

    `main()` maps an unexpected exception to exit 0 so a crash never changes a
    pipeline's outcome (invariant #3). These commands never analyze anything: they
    *are* the registry's CI check, and a crash that exits 0 turns that check green.
    """

    def invoke(self, ctx: click.Context) -> object:
        """Run the command, turning any exception that is not already a usage or setup error into a `SetupError`."""
        try:
            return super().invoke(ctx)
        except (SetupError, click.ClickException, click.exceptions.Exit, click.Abort):
            raise
        except Exception as err:
            raise SetupError(f"internal error: {type(err).__name__}: {err}") from err


app = typer.Typer(cls=_SetupGroup, no_args_is_help=True, help="Browse, select and build matchers.")
for command in (index, verify, add, remove, list, search, info):
    app.command(command.NAME)(command.run)
app.add_typer(registry_app, name="registry")
