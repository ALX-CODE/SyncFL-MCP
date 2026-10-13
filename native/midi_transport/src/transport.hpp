#pragma once
// Core of the transport: deterministic endpoint resolution, the session table
// and the single-wakeup send path. The C ABI in abi.cpp is a thin wrapper.

#include "winmm_api.hpp"

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace syncfl::midi {

// Mirrors the SYNCFL_MIDI_* status codes in the public header.
enum class Status : std::int32_t {
    Ok = 0,
    InvalidArgument = 1,
    BufferTooSmall = 2,
    NotFound = 3,
    Ambiguous = 4,
    EndpointChanged = 5,
    InvalidHandle = 6,
    Busy = 7,
    SendFailed = 8,
    Native = 9,
    OutOfMemory = 10,
    DeviceSetChanged = 11,
    Internal = 12,
};

struct Result {
    Status status = Status::Ok;
    std::uint32_t native = 0;  // MMRESULT when a WinMM call failed
    std::string message;       // English, UTF-8

    bool ok() const noexcept { return status == Status::Ok; }
    static Result Success() { return {}; }
};

inline constexpr std::uint32_t kAllowDecimalSuffix = 1u;
inline constexpr std::uint32_t kKnownFlags = kAllowDecimalSuffix;

// Channel 0 note-on (0x90), note 127, velocity 127, packed for midiOutShortMsg.
inline constexpr BYTE kWakeupStatus = 0x90;
inline constexpr BYTE kWakeupNote = 127;
inline constexpr BYTE kWakeupVelocity = 127;
inline constexpr DWORD kWakeupMessage =
    static_cast<DWORD>(kWakeupStatus) | (static_cast<DWORD>(kWakeupNote) << 8) |
    (static_cast<DWORD>(kWakeupVelocity) << 16);
static_assert(kWakeupMessage == 0x007F7F90);

// WinMM product names hold at most MAXPNAMELEN - 1 characters.
inline constexpr std::size_t kMaxNameUnits = MAXPNAMELEN - 1;

struct Endpoint {
    UINT device = 0;
    std::wstring name;
    MIDIOUTCAPSW caps{};
};

// Exact equality, or "<requested> <ASCII digits>" when kAllowDecimalSuffix is set.
bool NameMatches(std::wstring_view device, std::wstring_view requested,
                 std::uint32_t flags) noexcept;

// The device name stored in caps, bounded by MAXPNAMELEN even if unterminated.
std::wstring CapsName(const MIDIOUTCAPSW& caps);

class Transport {
public:
    explicit Transport(MidiOutputApi& api) noexcept : api_(api) {}
    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;

    Result ListOutputs(std::vector<std::string>& names);
    Result Resolve(std::string_view name, std::uint32_t flags, Endpoint& endpoint);
    Result Open(std::string_view name, std::uint32_t flags, std::uint64_t& session);
    Result SessionName(std::uint64_t session, std::string& name);
    // attempted becomes true only once midiOutShortMsg has been called.
    Result SendWakeup(std::uint64_t session, bool& attempted);
    Result Close(std::uint64_t session);

    std::size_t OpenSessionCount();

private:
    struct Session {
        std::mutex mutex;  // serialises send and close on this session
        HMIDIOUT handle = nullptr;
        Endpoint endpoint;
        std::string name_utf8;
    };

    Result Snapshot(std::vector<Endpoint>& endpoints);
    Result Validate(std::string_view name, std::uint32_t flags, std::wstring& requested);
    Result Match(const std::vector<Endpoint>& endpoints, const std::wstring& requested,
                 std::uint32_t flags, Endpoint& endpoint);
    std::shared_ptr<Session> Find(std::uint64_t session);

    MidiOutputApi& api_;
    std::mutex table_mutex_;
    std::unordered_map<std::uint64_t, std::shared_ptr<Session>> sessions_;
    std::uint64_t next_session_ = 1;
};

}  // namespace syncfl::midi
