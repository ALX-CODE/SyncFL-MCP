"""Persistent bounded recording jobs; only the owned worker touches FL transport."""

from typing import Literal

from pydantic import StrictBool

from fl_studio_mcp.utils.recording_jobs import recording_status, start_recording, stop_recording
from fl_studio_mcp.utils.recording_windows import configure_recording_file


def register_recording_job_tools(mcp):
    @mcp.tool()
    async def fl_recording_configure_file(
        track: int, expected_name: str, expected_revision: str, recording_file: str,
        apply: StrictBool = False, receipt_file: str | None = None,
    ) -> dict:
        """Unavailable in backend-only SyncFL. Setting a recording filename has no backend
        implementation. Use native recording inspection and arm controls for existing
        targets.
        """
        return await configure_recording_file(
            track, expected_name, expected_revision, recording_file, apply, receipt_file)

    @mcp.tool()
    def fl_recording_start(
        track: int, expected_name: str, expected_revision: str, recording_file: str,
        directory: str, duration_seconds: float,
        source_kind: Literal["internal", "microphone"], source_description: str,
        apply: StrictBool = False, expected_sample_rate: int | None = None,
        expected_channels: int | None = None, minimum_audio_seconds: float = 0.1,
        require_non_silent: StrictBool = True,
    ) -> dict:
        """Preview/start a bounded 1..600 second native mixer recording into a NEW exact WAV.

        First use fl_recording_configure_file for the filename, configure input/pickup
        in FL, and arm only
        the target with fl_recording_arm. Inspect its fresh per-track recording revision.
        Playback and record latch must be off; precount and start-on-input must be off.
        The native catalog has no input setters. Caller source descriptions remain
        explicitly unverified. Use a NEW job directory separate from the WAV location.

        Apply launches a persistent worker, returning queued, not a completed take.
        It owns the DAW lease, revalidates everything, starts once and schedules Stop.
        Poll fl_recording_status or request an early stop with fl_recording_stop.
        Verified output requires stopped transport, a decoded stable WAV and requested
        format/non-silence checks. Playlist placement/latency remain unverified.
        """
        return start_recording(
            track, expected_name, expected_revision, recording_file, directory,
            duration_seconds, source_kind, source_description, apply,
            expected_sample_rate, expected_channels, minimum_audio_seconds, require_non_silent,
        )

    @mcp.tool()
    def fl_recording_status(directory: str) -> dict:
        """Read persistent recording status and take evidence without touching FL.

        queued/recording are not completed takes. verified/stopped_early with
        recorded_take_verified=true includes decoded audio/hash evidence. Stale worker
        heartbeat or needs_inspection means transport may still be active; inspect
        FL explicitly. Never replay a possibly delivered record/start/stop command.
        """
        return recording_status(directory)

    @mcp.tool()
    def fl_recording_stop(directory: str) -> dict:
        """Request that the job's owned worker stop recording early; never toggles FL directly.

        A saved request is not proof recording stopped. Poll fl_recording_status for
        transport_stopped_verified and decoded take evidence. Repeated requests are
        idempotent; a dead worker requires explicit host inspection/recovery.
        """
        return stop_recording(directory)
