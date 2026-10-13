# Experimental background OLE bridge

Windows x64 laboratory prototype. Exposed by the explicitly experimental
`fl_background_effect_inspect/load` tools, with additional Python guards. No automatic
retry or physical-input fallback. `probe` is the CLI default; `--drop` is a real
mutation and needs a disposable saved FL project plus independent native readback.

**FL qualification status (2026-10-12):** Six earlier effect loads were verified
in one lab session, but they do not establish reliable general background
loading. A later protocol 3 test in a freshly restarted FL Studio 2026 process
targeted an empty slot on the currently selected, visible Master track.
QueryInterface, DragEnter and Drop returned `S_OK`, but Drop returned
`DROPEFFECT_NONE` (0); every inspected slot and the foreground window remained
unchanged. An occupied-slot test also declined the drop. Recovered source from
before the earlier successes has the same successful-call path: the queried
IDropTarget pointer, CF_HDROP data, key flags and UI-thread dispatch are unchanged.
**Cause found (2026-10-12, later the same day):** FL routes the drop by screen
position. In the failing sessions FL's maximized Playlist was in front of the
Mixer, so `WindowFromPoint` at the computed slot point resolved to the Playlist
(`TEventEditForm`), which declined the `.fst`. FL still reported the Mixer as
visible. After FL's own `ui.showWindow(0)` brought the Mixer to the front (no
synthesized input; the OS foreground window was unchanged), five consecutive
loads into empty Master slots verified on the first dispatch: Auto-Tune Pro,
Decapitator, EchoBoy, MicroShift and Little AlterBoy. The Python layer now
reports a covered slot as not ready and rechecks exposure immediately before
dispatch. The route remains experimental: it still needs the selected track's
slot to be visibly exposed, and the private interface is unsupported.

Build with the installed Visual Studio C++ workload, without downloads:

```powershell
cmake -S native/background_bridge -B native/background_bridge/build -G 'Visual Studio 18 2026' -A x64
cmake --build native/background_bridge/build --config Release
ctest --test-dir native/background_bridge/build -C Release --output-on-failure
```

The output directory contains `alx-background-bridge.exe`,
`alx-background-hook.dll` and `background-selftest.exe`. Keep the DLL beside both
executables. The self-test creates its own hidden OLE target in a separate process,
checks that Windows's property equals its registered interface, probes without
COM calls, drops a disposable own-host file, verifies two independent data buffers,
rejects an absent property and exercises a timed-out request without replay. It
records foreground/cursor/capture snapshots. It does not target FL. A separate
test checks the bridge and hook imports and source for input/focus mutation APIs.

```powershell
# Freshly inspect PID/HWND first. This installs a temporary hook even in probe mode.
.\alx-background-bridge.exe --pid 1234 --hwnd 5678
# Explicit lab-only mutation; coordinates are physical screen coordinates.
.\alx-background-bridge.exe --pid 1234 --hwnd 5678 --drop --x 1487 --y 240 --file 'C:\lab\Effect.fst'
```

`--expected-exe` defaults to `C:\Program Files\Image-Line\FL Studio 2026\FL64.exe`;
an override must be an absolute path whose filename is `FL64.exe`. PID/HWND, exact
image, process creation time, UI thread and x64 architecture are checked. The
private test client accepts only its own executable image and is not the FL CLI.

One thread-specific `WH_CALLWNDPROC` hook wakes on a registered message carrying a
random 128-bit nonce as scalar parameters. A fixed-size named mapping uses a
protected current-user DACL; its name, nonce, schema, PID, TID, HWND, image and
deadline must match. The hook claims one action atomically. No input, focus,
activation, clipboard, global hook or window-style changes are used. Source files
are read-locked for the caller's operation. Probe only reads property and memory
page/vtable metadata; it does not QueryInterface or call the object.

An explicit drop performs one DragEnter followed by one Drop at the same point,
checks Copy acceptance and reports their HRESULTs. It does not call DragOver:
there is no pointer movement between entry and drop. The normal Windows OLE loop
[calls DragOver when the pointer moves within a target](https://learn.microsoft.com/en-us/windows/win32/api/ole2/nf-ole2-dodragdrop).
This direct private-interface route remains experimental. A failed or expired
drag is left once where applicable, then reported without replay or fallback.
Protocol schema 3 retains `over_hr` as `S_FALSE` (1), a **skipped-operation
sentinel**, not a returned callback HRESULT; CLI JSON explicitly includes
`over_skipped: true`. Client and DLL must be updated together. The own-host
target deliberately fails any DragOver call and requires exactly one entry,
zero over calls and one drop.

**Unsupported private interface:** `OleDropTargetInterface` is not a documented
Microsoft API for retrieving IDropTarget. Wine stores a raw pointer under this
name for Windows compatibility. A direct `IDropTarget::Drop` bypasses the normal
DoDragDrop loop. Memory checks and exception guards reduce mistakes but cannot
make an incompatible object's code safe. The property check in the own-host test
proves only the installed Windows implementation, not FL's routing behavior.

States: pending/running, probed, entered, dropped, rejected, unsupported, unknown.
`dropped` means the COM callback returned Copy; it **never** proves the desired
plugin, slot, project or audio result. FL-specific identity/generation/slot guards
belong in the Python integration layer and are deliberately absent from this native helper. Keep no FL
pointer across calls. A timeout always reports unknown and never retries. It may
leave a callback running in FL after the caller returns; unhooking is not rollback
or cancellation. Never delete/replace the DLL while a target may still use it.

The own-host test records snapshots of input state. Actual human input can change
those observations and does not fail the test. No claim about the source of a
snapshot change is made; the prototype never alters input to restore a snapshot.
The import/source check covers the listed APIs, not all possible behavior of a
third-party COM callback. FL-specific tests must also cover occlusion, moved windows,
plugin dialogs, callback failures, duplicate requests and concurrent user edits.

Primary references:

- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexw
- https://learn.microsoft.com/en-us/windows/win32/winmsg/callwndproc
- https://learn.microsoft.com/en-us/windows/win32/api/oleidl/nf-oleidl-idroptarget-drop
- https://github.com/wine-mirror/wine/blob/master/dlls/ole32/ole2.c

This implementation is original code; no Wine code is included.
