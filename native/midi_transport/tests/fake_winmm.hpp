#pragma once
// Deterministic, thread-safe stand-in for WinMM. It records every call with a
// global sequence number so tests can prove exactly what would reach the wire.
// Handles are opaque tokens that are never dereferenced.

#include "winmm_api.hpp"

#include <cstdint>
#include <cwchar>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

class FakeWinMm final : public syncfl::midi::MidiOutputApi {
public:
    struct Device {
        std::wstring name;
        WORD mid = 1;
        WORD pid = 1;
        MMVERSION version = 0x0100;
        MMRESULT caps_error = MMSYSERR_NOERROR;
        bool unterminated = false;  // fill every szPname slot without a NUL
    };
    enum class Kind { Open, Send, Close };
    struct Call {
        Kind kind;
        std::uintptr_t handle;
        DWORD message;
        std::uint64_t seq;
    };

    // Configuration. Hooks run without the internal lock so they may edit devices.
    std::vector<Device> devices;
    MMRESULT open_result = MMSYSERR_NOERROR;
    MMRESULT handle_caps_result = MMSYSERR_NOERROR;
    MMRESULT send_result = MMSYSERR_NOERROR;
    MMRESULT close_result = MMSYSERR_NOERROR;
    std::function<void(FakeWinMm&, unsigned)> on_count;  // index of the DeviceCount call
    std::function<void(FakeWinMm&)> before_open;
    std::function<void(FakeWinMm&)> after_open;

    FakeWinMm() = default;
    explicit FakeWinMm(std::vector<std::wstring> names) {
        for (auto& name : names) devices.push_back(Device{std::move(name)});
    }

    UINT DeviceCount() noexcept override {
        unsigned index;
        {
            std::lock_guard lock(mutex_);
            index = count_calls_++;
        }
        if (on_count) on_count(*this, index);
        std::lock_guard lock(mutex_);
        return static_cast<UINT>(devices.size());
    }

    MMRESULT DeviceCaps(UINT device, MIDIOUTCAPSW& caps) noexcept override {
        std::lock_guard lock(mutex_);
        ++caps_calls_;
        if (device >= devices.size()) return MMSYSERR_BADDEVICEID;
        if (devices[device].caps_error != MMSYSERR_NOERROR) return devices[device].caps_error;
        Fill(caps, devices[device]);
        return MMSYSERR_NOERROR;
    }

    MMRESULT HandleCaps(HMIDIOUT handle, MIDIOUTCAPSW& caps) noexcept override {
        std::lock_guard lock(mutex_);
        if (handle_caps_result != MMSYSERR_NOERROR) return handle_caps_result;
        const auto found = live_.find(Key(handle));
        if (found == live_.end()) return MMSYSERR_INVALHANDLE;
        Fill(caps, found->second);
        return MMSYSERR_NOERROR;
    }

    MMRESULT Open(HMIDIOUT& handle, UINT device) noexcept override {
        if (before_open) before_open(*this);
        {
            std::lock_guard lock(mutex_);
            if (open_result != MMSYSERR_NOERROR) return open_result;
            if (device >= devices.size()) return MMSYSERR_BADDEVICEID;
            const std::uintptr_t key = 0x1000 + (next_handle_++ * 0x10);
            handle = reinterpret_cast<HMIDIOUT>(key);
            live_[key] = devices[device];  // a handle stays bound to this device
            calls_.push_back({Kind::Open, key, 0, ++seq_});
        }
        if (after_open) after_open(*this);
        return MMSYSERR_NOERROR;
    }

    MMRESULT ShortMessage(HMIDIOUT handle, DWORD message) noexcept override {
        std::lock_guard lock(mutex_);
        calls_.push_back({Kind::Send, Key(handle), message, ++seq_});
        if (!live_.count(Key(handle))) {
            ++sends_on_dead_handles_;
            return MMSYSERR_INVALHANDLE;
        }
        return send_result;
    }

    MMRESULT Close(HMIDIOUT handle) noexcept override {
        std::lock_guard lock(mutex_);
        calls_.push_back({Kind::Close, Key(handle), 0, ++seq_});
        if (close_result != MMSYSERR_NOERROR) return close_result;
        if (live_.erase(Key(handle)) == 0) {
            ++closes_of_dead_handles_;
            return MMSYSERR_INVALHANDLE;
        }
        return MMSYSERR_NOERROR;
    }

    std::vector<Call> Calls() {
        std::lock_guard lock(mutex_);
        return calls_;
    }
    std::size_t Count(Kind kind) {
        std::lock_guard lock(mutex_);
        std::size_t total = 0;
        for (const auto& call : calls_) total += call.kind == kind;
        return total;
    }
    std::size_t LiveHandles() {
        std::lock_guard lock(mutex_);
        return live_.size();
    }
    unsigned CountCalls() {
        std::lock_guard lock(mutex_);
        return count_calls_;
    }
    std::size_t SendsOnDeadHandles() {
        std::lock_guard lock(mutex_);
        return sends_on_dead_handles_;
    }
    std::size_t ClosesOfDeadHandles() {
        std::lock_guard lock(mutex_);
        return closes_of_dead_handles_;
    }
    void AddDevice(Device device) {
        std::lock_guard lock(mutex_);
        devices.push_back(std::move(device));
    }
    void ReplaceDevice(std::size_t index, Device device) {
        std::lock_guard lock(mutex_);
        devices.at(index) = std::move(device);
    }

private:
    static std::uintptr_t Key(HMIDIOUT handle) { return reinterpret_cast<std::uintptr_t>(handle); }

    static void Fill(MIDIOUTCAPSW& caps, const Device& device) {
        caps = MIDIOUTCAPSW{};
        caps.wMid = device.mid;
        caps.wPid = device.pid;
        caps.vDriverVersion = device.version;
        caps.wTechnology = MOD_MIDIPORT;
        caps.wChannelMask = 0xFFFF;
        if (device.unterminated) {
            for (std::size_t i = 0; i < MAXPNAMELEN; ++i)
                caps.szPname[i] = device.name.empty() ? L'X' : device.name[i % device.name.size()];
            return;
        }
        // WinMM truncates product names to MAXPNAMELEN - 1 characters.
        const std::size_t length = device.name.size() < MAXPNAMELEN - 1 ? device.name.size()
                                                                         : MAXPNAMELEN - 1;
        std::wmemcpy(caps.szPname, device.name.data(), length);
        caps.szPname[length] = L'\0';
    }

    std::mutex mutex_;
    std::map<std::uintptr_t, Device> live_;
    std::vector<Call> calls_;
    std::uint64_t seq_ = 0;
    std::uintptr_t next_handle_ = 1;
    unsigned count_calls_ = 0;
    std::size_t caps_calls_ = 0;
    std::size_t sends_on_dead_handles_ = 0;
    std::size_t closes_of_dead_handles_ = 0;
};
