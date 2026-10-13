"""Native recording inspection and explicit desired mixer-arm state."""


def register_recording_state_tools(mcp):
    @mcp.tool()
    def fl_recording_inspect(tracks: list[int] | None = None) -> dict:
        """Read recording metadata and fresh per-track revisions without recording.

        Supply exact zero-based tracks to inspect an unarmed target. Without tracks,
        scans mixer tracks and returns compact armed-track entries.
        Each returned track has a revision accepted by fl_recording_arm.

        Native filenames are reported/proposed paths which can change with time; their
        existence is not checked and they are excluded from the arm revision. They do
        not prove a recorded take or configured input. Reports start-on-input when
        advertised. Input device/channel, monitoring,
        buffer size, recording latency and PDC remain explicitly unknown. Missing core
        read capabilities fail closed; unavailable optional metadata is labeled.
        """
        from fl_studio_mcp.utils.recording_state import inspect_recording

        return inspect_recording(tracks)

    @mcp.tool()
    def fl_recording_arm(
        track: int,
        expected_name: str,
        expected_revision: str,
        armed: bool,
        apply: bool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Set an exact mixer track's desired arm state; never starts recording.

        Use the target track's revision from fl_recording_inspect. Playback must be
        stopped and recording off. Preview by default. Apply requires a NEW absolute
        JSON receipt_file, including when the desired state is already satisfied.
        Already-satisfied state dispatches no toggle. Otherwise dispatches FL's native
        arm toggle ONCE, then independently verifies state in later callbacks.

        Missing arm/read capability fails closed. Timeout, modal dialog, identity change
        or uncertain completion retains a receipt; inspect before explicit recovery.
        No automatic retry or restore. Cooperating clients share the GUI lock; user/legacy
        actions may interleave. Arming does not prove a valid input or recorded take.
        """
        from fl_studio_mcp.utils.recording_state import set_recording_arm

        return set_recording_arm(
            track, expected_name, expected_revision, armed, apply, receipt_file
        )
