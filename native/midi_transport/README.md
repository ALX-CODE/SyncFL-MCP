# Native MIDI output transport

SyncFL's C++20 Windows x64 DLL replaces the Windows RtMidi output client. It
enumerates WinMM outputs, opens exactly one named output, sends the fixed
controller wakeup (`90 7F 7F`), and closes its opaque session. It has no runtime
dependency beyond Windows system DLLs and does not use desktop input APIs.

This component uses the endpoint created by `native/midi_bridge`; Windows MIDI
Services still supplies the virtual endpoint and driver. The transport does not
create a server, install a driver, host plugins, or replace the FL controller.
Mido remains in the Python dependencies for MIDI file reading/writing. Non-Windows
output still uses Mido/RtMidi. No speed improvement is claimed without measurement.

## Build and test

Use Visual Studio's C++ workload and a Windows SDK:

```powershell
cmake -S native/midi_transport -B build/midi_transport -A x64
cmake --build build/midi_transport --config Release
ctest --test-dir build/midi_transport -C Release --output-on-failure
```

The DLL and CLI are produced in `build/midi_transport/bin/Release`. The source
build uses warnings as errors, a static CRT, CFG, CET, ASLR and DEP. PDB references
contain only filenames. CTest includes deterministic fake-WinMM tests for
selection, Unicode, hot-plug changes, ownership, concurrent send/close and errors,
a real-DLL enumeration smoke test, CLI checks and a binary build-path audit.
These tests do not open live endpoints or send MIDI.

```powershell
.\build\midi_transport\bin\Release\syncfl-midi.exe list --json
.\build\midi_transport\bin\Release\syncfl-midi.exe resolve 'ALX FL Bridge (Send)' --json
```

`wakeup <name> --confirm-send` is an explicit live MIDI operation. Use the Python
connection for normal commands: it publishes the correlated protocol-2 mailbox
request, applies interaction policy and locks, then verifies FL's response.
A standalone wakeup does not publish a new command or prove FL executed one.

## Binding and distribution

[The C header](include/syncfl_midi_transport.h) is the ABI contract: UTF-8,
caller-owned buffers, status codes, exact matching and thread/lifetime rules.
The `cdecl` Python binding declares every argument/result width, loads only on
demand, checks ABI 1.0, and verifies both distributed binaries against the runtime
manifest. A missing or corrupt Windows bundle fails explicitly; it never falls
back to another DLL, RtMidi or keyboard/mouse input.

Names match exactly. Optional decimal-suffix matching is available only through
an explicit ABI/CLI flag; the application selects an unambiguous name before
opening. The handle's device identity is rechecked after open. WinMM truncates
product names to 31 UTF-16 code units; longer requests are rejected.

Every send makes at most one `midiOutShortMsg` call. A failure after that call has
an unknown delivery outcome and is never retried. The Python layer preserves that
distinction, invalidates the failed connection and retains the request receipt.
Every output must be closed before library unload; unloading with live sessions
is unsupported. No MIDI reset or extra note-off messages are emitted on close.

Shipped DLL/EXE files are pinned in `runtime/manifest.json`; PDBs and build trees
are not distributed. Rebuilds must be tested and scanned for private paths before
the manifest is updated. Python tests cover the binding with synthetic functions
and do not touch hardware. Actual FL handshake evidence is recorded separately
in the repository's validation guide.

References: [WinMM output](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-midioutshortmsg),
[Python ctypes](https://docs.python.org/3.13/library/ctypes.html).
