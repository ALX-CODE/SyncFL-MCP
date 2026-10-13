# ALX FL Bridge

This small native helper creates a pair of endpoints in the Windows MIDI
Services already built into Windows. MIDI traffic goes through Windows, so
neither this helper nor loopMIDI needs to remain running. It does not install a
driver, edit registry values, change service settings, or require elevation.

Use the bundled x64 runtime:

```powershell
.\verify-runtime.ps1 -Probe
.\runtime\alx-midi-service.exe ensure --json
```

In FL Studio's MIDI input settings enable **ALX FL Bridge (FL Studio)** and
assign the FL Studio MCP controller script. The MCP sends its command wakeup
to the output **ALX FL Bridge (Send)**. These are opposite sides of a pair:
sending to Send arrives at the FL Studio input. Select the precise names;
each pair also exposes ports in the opposite direction.

`status --json` is read-only. `ensure --json` reuses the exact existing pair or
creates it. `remove --json` removes only the pair with both exact ALX endpoint
IDs and expected names; it refuses altered identities. Do not remove endpoints
while FL Studio is using them. The helper serializes concurrent management calls.
Exit codes: 0 success, 2 invalid command, 3 transport unavailable, 4 creation
failed, 5 Windows/WinRT error, 6 ownership or other local error. Errors go to
stderr; successful JSON reports the association, endpoint IDs and names.

The endpoints survive helper exit, and expire when Windows MIDI Services
restarts or Windows reboots. Run `ensure` before opening FL Studio after either
event. Stable unique IDs allow the pair to retain its identity when recreated.
This is a transient runtime setup; it intentionally does not save system-wide
MIDI configuration. There is no resident custom server.

## Requirements and deployment

- Windows x64 with Windows MIDI Services and its loopback transport available.
  `status` checks the actual transport instead of guessing from the OS version.
- Microsoft's x64 Visual C++ runtime (`MSVCP140.dll`, `VCRUNTIME140.dll`, and
  `VCRUNTIME140_1.dll`), required by the Microsoft SDK DLL. No .NET runtime is used.
- Keep `alx-midi-service.exe`, `Windows.Devices.Midi2.dll`, and
  `Windows.Devices.Midi2.pri` together. The metadata `.winmd` is a build input
  and is not needed at runtime. The three runtime files total about 6.9 MiB.

The committed `runtime/` is portable, with per-file SHA-256 values and the pinned
SDK source/package hash in `manifest.json`. `verify-runtime.ps1` checks the exact
file list and checksums. This detects accidental corruption; the adjacent
manifest is not a cryptographic signature. `THIRD-PARTY-NOTICES.md` contains the
Microsoft SDK's MIT license.

## Rebuild and package

Build with Visual Studio 2026 C++ tools, a Windows SDK, PowerShell, and CMake:

```powershell
.\build.ps1 -FetchDependencies
.\install-runtime.ps1
.\package.ps1
```

The first command explicitly downloads about 99 MiB of pinned Microsoft SDK and
C++/WinRT build packages into ignored `build/deps/`; it installs nothing. SHA-256
checks run before extraction. Further builds need no network. `install-runtime`
copies the Release build into the stable repository runtime folder and refreshes
its manifest. `package` creates a portable ZIP under `build/`.

CTest checks read-only status, command validation, and an actual WinMM
cross-process transfer. The transfer test creates a uniquely named temporary
pair, sends the exact `0x007f7f90` wakeup, verifies it arrives exactly once, and
removes only that test pair. It does not use or remove the production ALX pair.
Transport-dependent tests report skipped when Windows MIDI Services is absent.

## Verified compatibility

On the development host (Windows build 26200.9457; `midisrv.exe` 10.0.26100.7705),
the bundled SDK 0.99.92-preview.11 created the endpoints without elevation.
Separate helper processes found the same pair after creator exit. Native
WinMM sender/receiver processes delivered the exact wakeup once. The parent
integration run also cold-started FL Studio 2026 with the loopMIDI process absent
and verified an MCP bridge ping through this pair. Other Windows service builds
must pass the runtime probe; the helper does not switch Windows MIDI modes.

Official references:

- [API compatibility and loopback support](https://microsoft.github.io/MIDI/kb/api-back-compat/)
- [Loopback manager and transient lifetime](https://microsoft.github.io/MIDI/sdk-reference/Transports/Loopback/MidiLoopbackManager/)
- [Pinned Microsoft release](https://github.com/microsoft/MIDI/releases/tag/inbox-preview-11)
- [Official C++ loopback sample](https://github.com/microsoft/MIDI/blob/inbox-preview-11/samples/cpp-winrt/loopback-endpoints/main_loopback_endpoints.cpp)
