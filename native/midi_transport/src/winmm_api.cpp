#include "winmm_api.hpp"

namespace syncfl::midi {
namespace {

class SystemMidiOutput final : public MidiOutputApi {
public:
    UINT DeviceCount() noexcept override { return midiOutGetNumDevs(); }
    MMRESULT DeviceCaps(UINT device, MIDIOUTCAPSW& caps) noexcept override {
        return midiOutGetDevCapsW(device, &caps, sizeof(caps));
    }
    MMRESULT HandleCaps(HMIDIOUT handle, MIDIOUTCAPSW& caps) noexcept override {
        // midiOutGetDevCaps documents that a properly cast handle may be passed.
        return midiOutGetDevCapsW(reinterpret_cast<UINT_PTR>(handle), &caps, sizeof(caps));
    }
    MMRESULT Open(HMIDIOUT& handle, UINT device) noexcept override {
        return midiOutOpen(&handle, device, 0, 0, CALLBACK_NULL);
    }
    MMRESULT ShortMessage(HMIDIOUT handle, DWORD message) noexcept override {
        return midiOutShortMsg(handle, message);
    }
    MMRESULT Close(HMIDIOUT handle) noexcept override { return midiOutClose(handle); }
};

}  // namespace

MidiOutputApi& SystemWinMm() noexcept {
    static SystemMidiOutput api;  // stateless; its destructor does no work at unload
    return api;
}

}  // namespace syncfl::midi
