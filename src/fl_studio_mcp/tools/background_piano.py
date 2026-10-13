"""Backend-only, script-free persistent piano notes."""

from fl_studio_mcp.utils.background_piano import (
    inspect_background_piano,
    select_background_piano,
    write_background_piano,
)


def register_background_piano_tools(mcp):
    @mcp.tool()
    def fl_background_piano_inspect(project_path: str, channel: int) -> dict:
        """Inspect a saved FL26 score and its live native target without menus.

        Supply the exact active .flp path and zero-based channel. Requires a saved,
        stopped project. Returns the pattern/name, generator or sample source,
        saved generator-state fingerprint, mixer routing, effect slots and revision.
        This reads saved notes; the write tool verifies its exact disk path after save.
        """
        return inspect_background_piano(project_path, channel)

    @mcp.tool()
    def fl_background_piano_select(project_path: str, channel: int, pattern: int,
                                   expected_revision: str) -> dict:
        """Select an existing pattern and sound through native APIs, then verify both.

        Inspect the target channel first; pass its revision. Returns the selected
        pattern name, generator identity, saved preset fingerprint, mixer routing
        and actual effect slots. Opens its piano roll and selects its routed
        mixer track without menu clicks, so subsequent effect operations match.
        FL marks pattern selection dirty, so a changed pattern is saved with
        before/after recovery copies and exact verification of all existing notes.
        """
        return select_background_piano(project_path, channel, pattern, expected_revision)

    @mcp.tool()
    def fl_background_piano_write(project_path: str, channel: int, notes: list[dict],
                                  expected_revision: str, expected_channel: str,
                                  receipt_file: str, apply: bool = False) -> dict:
        """Replace one piano-roll score through native FSC import; no ComposeWithLLM.

        Uses the current pattern and explicit channel. Basic note fields: midi,
        time/duration in beats or time_ticks/length_ticks, velocity, pan, release,
        color and slide. Saves the active project and checks exact notes plus all
        other channel/pattern notes in the supplied .flp. Keeps before/after copies.
        Native panel selection is automatic. No keyboard, mouse or interactive fallback.
        """
        return write_background_piano(project_path, channel, notes, expected_revision,
                                      expected_channel, receipt_file, apply)
