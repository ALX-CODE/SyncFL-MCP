#pragma once
// The only WinMM surface the transport may use. Tests substitute a fake to
// exercise failure paths without touching real MIDI endpoints. There is
// deliberately no midiOutReset (it emits note-off traffic) and no long-message
// entry point: the transport can only ever emit the single short wakeup.

#include <windows.h>
#include <mmsystem.h>

namespace syncfl::midi {

class MidiOutputApi {
public:
    virtual ~MidiOutputApi() = default;
    virtual UINT DeviceCount() noexcept = 0;
    virtual MMRESULT DeviceCaps(UINT device, MIDIOUTCAPSW& caps) noexcept = 0;
    // Capabilities of the device an open handle is bound to; immune to index shifts.
    virtual MMRESULT HandleCaps(HMIDIOUT handle, MIDIOUTCAPSW& caps) noexcept = 0;
    virtual MMRESULT Open(HMIDIOUT& handle, UINT device) noexcept = 0;
    virtual MMRESULT ShortMessage(HMIDIOUT handle, DWORD message) noexcept = 0;
    virtual MMRESULT Close(HMIDIOUT handle) noexcept = 0;
};

// Process-wide binding to winmm.dll. Stateless; safe to share between threads.
MidiOutputApi& SystemWinMm() noexcept;

}  // namespace syncfl::midi
