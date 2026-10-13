"""Experimental selected-track effect loading without desktop input."""

from pydantic import StrictBool

from fl_studio_mcp.utils.background_loading import (
    inspect_background_effect,
    load_background_effect,
)


def register_background_loading_tools(mcp):
    @mcp.tool()
    def fl_background_effect_inspect(track: int, slot: int) -> dict:
        """Inspect an empty slot on the already selected visible mixer track.

        Returns a revision binding native project identity, all mixer slot names,
        selection and fresh HWND geometry. Does not select, show or focus anything.
        The private OLE route is experimental and requires a verified local runtime.
        """
        return inspect_background_effect(track, slot)

    @mcp.tool()
    def fl_background_effect_load(
        track: int,
        slot: int,
        path: str,
        expected_plugin: str,
        expected_revision: str,
        expected_window_title: str,
        apply: StrictBool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preview or load one valid .fst into an empty, already selected mixer slot.

        Pass the exact native plugin name, inspection revision and main-window title.
        Apply requires a NEW absolute JSON receipt, dispatches once, then checks all
        mixer slot identities in later native callbacks. Busy read callbacks may be
        retried; the drop never is. Does not change selection, mouse, keys or focus.
        Unknown callbacks or mismatches stay unverified; inspect before any recovery.
        """
        return load_background_effect(
            track,
            slot,
            path,
            expected_plugin,
            expected_revision,
            expected_window_title,
            apply,
            receipt_file,
        )
