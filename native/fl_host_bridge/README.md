# FL native host bridge research prototype

This is original, Windows x64 C++ code for a native FL plugin and a small local
CLI. It establishes a background command path through the documented plugin
idle callback. **It has not been installed or loaded into FL Studio. Note
writes are disabled; it does not replace the production piano-roll bridge.**

The only host dispatcher call is `FHD_WantIdle` with value `2`, made when the
plugin instance is constructed. The worker thread transfers fixed-size messages;
the instance's `Idle_Public` callback handles requests. No keyboard, mouse,
window activation, clipboard, code injection, or arbitrary dispatcher endpoint
is present. Loading this plugin once into FL would still require a separately
verified loading workflow. An FL channel or wrapper can still exist: the SDK's
`FPF_NoWindow` does not mean a plugin is absent from FL's user interface.

## Why note writes and project strings are blocked

The [official Image-Line SDK post](https://forum.image-line.com/viewtopic.php?t=12092)
provides `fpsdk_20210311b.zip`, dated March 11, 2021 and described as targeting
FL Studio 20.8. Its headers contain incompatible contracts:

| Contract | C++ header | Delphi header | Consequence |
|---|---|---|---|
| Note velocity | `source/c/fp_plugclass.h:214`: `TNoteParams.Vol` is `int` | `source/delphi/FP_PlugClass.pas:134`: `Vol` is `Single`, with a fractional default | Same 32-byte structure and offset 12, different value representation. No note payload is sent. |
| Project info result | C++ line 774 describes `PWideChar` | Delphi line 677 describes UTF-8 `PAnsiChar` | No returned string pointer is requested or dereferenced. |

The matching documented structure layout is 32 bytes per note, with integer
position/length described as PPQ units, and a 20-byte `TNotesParams` header.
`FHD_AddNotesToPR=17` takes that header and its notes. `Target=1` selects piano
roll; `PatNum=-1` means current pattern; `ChanNum=-1` means the plugin's or selected
channel. `NPF_EmptyFirst=1` clears existing notes. This prototype rejects implicit
targets and validates integer tick overflow, pitch 0..127 and normalized velocity
0..1000, but **neither the add nor replace mode invokes the host**. The explicit
index bases, bounds, mutation acknowledgement, and reliable independent readback
also need qualification before an apply operation can exist. Normalized velocity
in this protocol is our own input format, not an assertion about SDK encoding.

`FHD_GetProjectInfo` offers title, author, comments, URL and a filename selector;
the C++ header calls the latter a naked filename. This does not substantiate an
absolute project path even if the string encoding is resolved.

On October 12, 2026, the current [developer landing page](https://www.image-line.com/developers)
still linked this same SDK archive. A fresh download matched the SHA256 below.
The public [IL-Group organization](https://github.com/il-group) listed only its
unmaintained Python API stubs, not a newer native SDK.

There is useful independent vendor evidence: the separate [official VST2
extension](https://forum.image-line.com/viewtopic.php?t=132491) defines both its
C++ and Delphi note velocity as float/single, in the same 32-byte layout. Its
archive SHA256 is
`02c648b33a417d5f9dd3b8405a6b62ee31ad584f50b2d40838415f2a3f7757bd`.
This supports the inference that float is intended, but it documents
`audioMasterVendorSpecific` / `ILC_AddNotesToPR`, not the native
`FHD_AddNotesToPR` ABI. It does not remove the need to qualify the latter.
No native note enumeration API was found in the supplied host interface:
`FPD_GetParamInfo` queries a plugin's parameter description, `FHD_GetParamMenuEntry`
describes its menu, and `FPV_GetInfo` describes a playing voice. None is an
independent readback of saved piano-roll notes. No note mutation example was
found in the supplied SDK sample implementations.

## Implemented protocol

The endpoint is `\\.\pipe\ALX.FLHostBridge.<process-id>`, available only while
one plugin instance in that process owns it. Duplicate instances stay inert.

| Operation | Result |
|---|---|
| `ping` | Correlated response from the idle callback, random instance ID, callback generation, host version, creation thread ID. |
| `project-info` | The same metadata plus PPQ from `FPD_SetTimeSig` and playing state from `FPD_SetPlaying`. It never requests project strings. |
| `notes-preview` | Validates explicit target, bounded notes, instance and generation; returns `unsupported_abi`. There is no apply command. |

`ppq=0` and `playing=-1` mean the corresponding host callback has not been seen.
Generation increments on `FPD_ProjLoaded`, `FPD_ProjectInfoChanged` and restored
state. **It is not a full project revision and does not detect arbitrary note,
channel or arrangement edits.** Instance ID changes on each plugin construction.

`include/wire.hpp` is the complete versioned binary contract: little-endian,
2112-byte requests and 316-byte responses, with no pointers or variable strings
in requests. The maximum is 128 notes. A nonzero correlation ID and expiry are
required; more than 30 seconds of validity is rejected. The CLI uses a 5-second
expiry. Each pipe transfer and wait for idle is bounded. There are no automatic
retries and no persistent state or credentials in FL project files.

The pipe's protected DACL grants access to the current token's user SID and
rejects remote clients. The client checks the actual server process ID and uses
identification-only security quality of service. Other processes owned by that
same Windows user are inside the trust boundary. The server thread never calls
FL, and destruction cancels I/O, wakes the worker, then joins it. Calling idle
on a thread different from the plugin's construction thread fails closed; that
construction/GUI-thread assumption still requires a real FL test.

## Build and tests

Prerequisites: Windows x64, CMake, Visual Studio 2026 C++ build tools, and a local
extraction of the official SDK. From this repository:

```powershell
.\native\fl_host_bridge\build.ps1 -SdkRoot 'C:\path\to\extracted\source'
```

The supplied root must contain `source\c\fp_plugclass.h`. CMake checks the
reviewed header hashes and rejects a different SDK until its contract has been
reviewed. Build output remains in the ignored `build` directory:

- `build\Release\ALX FL Host Bridge.dll`: research plugin.
- `build\Release\alx-fl-host.exe`: CLI.
- `build\Release\host-bridge-tests.exe`: fake-host test harness.

The tests load our DLL only into that harness, exercise a separate CLI process,
and verify hidden idle scheduling, callback metadata, instance and expiry
guards, duplicate ownership, malformed input, disabled writes, wrong-thread
rejection, and cancellation on unload. Import/source checks reject desktop
input and foreign-process injection APIs. These tests prove our IPC and SDK
interface construction against a fake host; they do not prove compatibility
with FL Studio 2026 or a successful note edit.

For a separately qualified plugin process, the CLI syntax would be:

```powershell
.\alx-fl-host.exe --pid 1234 ping
.\alx-fl-host.exe --pid 1234 project-info
.\alx-fl-host.exe --pid 1234 notes-preview 123456 1 1 0 add 0:96:60:800
```

The last example describes an inert proposal, with explicit pattern/channel
integers and `position:length:pitch:velocity_permille`. The shown IDs and indices
are illustrative, not discoveries about a live project. Exit code 0 means an
`ok` read response, 2 is a protocol rejection (including disabled notes), and 1
is an argument or transport failure.

## Source provenance and qualification boundary

No SDK headers, SDK implementations, vendor sample source, or built artifacts
are committed here. The SDK title page states Image-Line copyright and all
rights reserved; no permissive redistribution license was identified. The
original implementation includes the separately obtained headers locally and
does not link their `.cpp` files. This is a plugin, and the fake host is a test
harness; it is not an alternate commercial FL plugin host.

Reviewed SDK archive SHA256:
`a29c5bfbfd576ab9798574920ce911a7b4fe68c2aa7d11bec2546cdf4a6b35b1`.
Pinned C++ headers:

- `fp_plugclass.h`: `d04e33ac267801da0d5e8492271f8fc11ce4182dca7b8cfa1e6ec49226275444`
- `fp_def.h`: `b291de22a41940b6d89fab785a4d7e26bb429e96f63e3ed358e901add4c0bda8`

Before production integration, resolve the conflicting contracts with an
authoritative corrected SDK/vendor answer, then qualify the real FL host in a
disposable project. A future write path also needs exact target identity, a
fresh production revision, stopped/recording guards, independent note readback,
and durable no-retry receipts. Current read-only generation and fixed requests
are not enough to claim those safeguards. No runtime enable flag bypasses the
disabled note path in this prototype.
