# Troubleshooting

Start with `scripts/doctor.py`, then use `--live` when FL is running. Pass the same custom FL root, MIDI output and Windows executable used by your client. Preserve complete errors and receipts locally; redact them before sharing.

| Symptom | Check and next action |
|---|---|
| No ALX MIDI endpoints | Run the native bridge's documented probe. Compatible Windows MIDI Services loopback support and x64 VC++ runtime are required. Do not assume every Windows build has the transport. |
| Endpoints existed before reboot | Start the server before FL so it recreates the pair. Refresh FL's MIDI devices or restart FL after current work finishes. |
| Port opens but requests time out | Confirm FL receives **ALX FL Bridge (FL Studio)**, is enabled and uses **FL Studio MCP Controller**. The server sends to **ALX FL Bridge (Send)**. |
| MIDI device disconnects or the handle closes | SyncFL rejects known closed handles and closes a handle after a send exception. A later explicit request can reconnect. The failed request is never replayed: inspect its receipt/state before repeating a mutation. |
| More than one matching output | Remove the ambiguous dedicated setup or explicitly configure a unique existing MIDI output. Do not guess a numeric backend suffix. |
| Updated files but old behavior/tools | Reload the FL controller and reconnect the MCP client. Compare loaded digests in the live doctor and `fl_build_info`. |
| “Interactive mode required” | The operation needs input. Keep background mode for native/offline work, or explicitly choose interactive mode for this workflow and stop unrelated typing. |
| Windows helper unavailable | Set `FL_MCP_WINDOWS_SERVER` to a compatible existing executable. Native MIDI and offline tools do not require it; specific window/UI workflows do. |
| Piano state unavailable | Windows script activation requires interactive mode, stopped transport and the exact target channel. Verify all six deployed files and the current selected script. |
| Stale revision/name/layout | Reinspect the actual target. Do not remove the expected identity or force another plugin's adapter. |
| Plugin physical write refused | Check unit, qualified range and mode dependency. Change selectors separately. Auto/INF and unqualified mappings may need explicit normalized access and fresh inspection. |
| Plugin normalized/display mismatch | Treat it as a verification failure. Preserve the receipt and inspect; caches can remain stale. Do not repeatedly write until a value happens to look right. |
| Background effect load declined | This route is experimental and selected-track/empty-slot-only. Do not retry uncertain dispatch or assume general loading works. Use an explicit interactive route if appropriate. |
| FFmpeg not found / loudness unavailable | Install an FFmpeg build with `loudnorm`, place it on PATH or set `FL_STUDIO_MCP_FFMPEG`. Silence/short audio can legitimately have unavailable LUFS/LRA. |
| Render command sent but no verified result | Poll `fl_render_status`. Inspect owned dialogs and output diagnostics. Start acknowledgment alone is not completion. |
| Native save succeeded but path is unknown | That result proves host clean state. Use the interactive exact-path save workflow when disk destination evidence is required. |
| Lock busy or pending piano request | Finish/recover the existing operation. Do not delete lock/queue files merely to force an update. |

## Useful read-only commands

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py --live
.\.venv\Scripts\python.exe scripts\verify_mcp_runtime.py --live
.\.venv\Scripts\python.exe -c "import mido; print(mido.get_output_names())"
```

If reporting a bug, include OS/FL/Python versions, exact tool/arguments with paths redacted, expected versus observed behavior, and whether the outcome was a preview, a failed preflight or an uncertain dispatched write. See [contributing](../CONTRIBUTING.md). Never post commercial samples, license keys or complete personal client configurations.
