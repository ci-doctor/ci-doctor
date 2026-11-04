"""`output:`: where the report goes."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict


class OutputConfig(Strict):
    """Where the report goes."""

    terminal: bool = Field(default=True, description="Render the report to the terminal.")
    markdown_path: str = Field(default="report.md", description="Path the Markdown report is written to.")
    json_path: str = Field(default="report.json", description="Path the JSON report is written to.")
    mr_note: bool = Field(
        default=False,
        description="Post or update a note on the MR/PR. Needs a live pipeline, not an offline log replay.",
    )
