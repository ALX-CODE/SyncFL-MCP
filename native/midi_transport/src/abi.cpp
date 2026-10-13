// Exported C ABI. Each entry point validates its pointers, runs inside the
// exception firewall and records the per-thread last error.

#include "abi_internal.hpp"
#include "utf.hpp"
#include "syncfl_midi_transport.h"

#include <cstring>
#include <memory>
#include <string>
#include <vector>

namespace syncfl::midi::abi {
namespace {

struct LastError {
    std::int32_t status = SYNCFL_MIDI_OK;
    std::uint32_t native = 0;
    std::string message;
};
thread_local LastError t_last_error;

// Upper bound when measuring caller strings; longer input is rejected anyway.
constexpr std::size_t kScanLimit = 4096;

#if defined(SYNCFL_MIDI_TEST_SEAM)
std::unique_ptr<Transport>& TestTransport() {
    static std::unique_ptr<Transport> transport;
    return transport;
}
#endif

Transport& Process() {
#if defined(SYNCFL_MIDI_TEST_SEAM)
    if (auto& transport = TestTransport()) return *transport;
#endif
    // Intentionally never destroyed: destructors at DLL unload run under the
    // loader lock, where closing WinMM handles is unsafe.
    static Transport* const transport = new Transport(SystemWinMm());
    return *transport;
}

Result BadArgument(const char* message) { return Result{Status::InvalidArgument, 0, message}; }

std::string_view Measure(const char* text) { return {text, strnlen(text, kScanLimit)}; }

// bytes already include their terminators.
Result CopyOut(const std::string& bytes, char* buffer, size_t capacity, size_t* required) {
    *required = bytes.size();
    if (capacity < bytes.size())
        return Result{Status::BufferTooSmall, 0,
                      "buffer too small; " + std::to_string(bytes.size()) + " bytes required"};
    std::memcpy(buffer, bytes.data(), bytes.size());
    return Result::Success();
}

}  // namespace

std::int32_t Store(const Result& result) noexcept {
    t_last_error.status = static_cast<std::int32_t>(result.status);
    t_last_error.native = result.native;
    try {
        t_last_error.message = result.message;
    } catch (...) {
        t_last_error.message.clear();
    }
    return t_last_error.status;
}

#if defined(SYNCFL_MIDI_TEST_SEAM)
void UseApiForTesting(MidiOutputApi* api) {
    TestTransport().reset(api ? new Transport(*api) : nullptr);
}
#endif

}  // namespace syncfl::midi::abi

using syncfl::midi::Endpoint;
using syncfl::midi::Result;
using syncfl::midi::Status;
using syncfl::midi::abi::BadArgument;
using syncfl::midi::abi::CopyOut;
using syncfl::midi::abi::Guarded;
using syncfl::midi::abi::Measure;
using syncfl::midi::abi::Process;

extern "C" {

SYNCFL_MIDI_API uint32_t SYNCFL_MIDI_CALL syncfl_midi_abi_version(void) {
    return SYNCFL_MIDI_ABI_VERSION;
}

SYNCFL_MIDI_API const char* SYNCFL_MIDI_CALL syncfl_midi_status_name(int32_t status) {
    switch (status) {
        case SYNCFL_MIDI_OK: return "SYNCFL_MIDI_OK";
        case SYNCFL_MIDI_E_INVALID_ARGUMENT: return "SYNCFL_MIDI_E_INVALID_ARGUMENT";
        case SYNCFL_MIDI_E_BUFFER_TOO_SMALL: return "SYNCFL_MIDI_E_BUFFER_TOO_SMALL";
        case SYNCFL_MIDI_E_NOT_FOUND: return "SYNCFL_MIDI_E_NOT_FOUND";
        case SYNCFL_MIDI_E_AMBIGUOUS: return "SYNCFL_MIDI_E_AMBIGUOUS";
        case SYNCFL_MIDI_E_ENDPOINT_CHANGED: return "SYNCFL_MIDI_E_ENDPOINT_CHANGED";
        case SYNCFL_MIDI_E_INVALID_HANDLE: return "SYNCFL_MIDI_E_INVALID_HANDLE";
        case SYNCFL_MIDI_E_BUSY: return "SYNCFL_MIDI_E_BUSY";
        case SYNCFL_MIDI_E_SEND_FAILED: return "SYNCFL_MIDI_E_SEND_FAILED";
        case SYNCFL_MIDI_E_NATIVE: return "SYNCFL_MIDI_E_NATIVE";
        case SYNCFL_MIDI_E_OUT_OF_MEMORY: return "SYNCFL_MIDI_E_OUT_OF_MEMORY";
        case SYNCFL_MIDI_E_DEVICE_SET_CHANGED: return "SYNCFL_MIDI_E_DEVICE_SET_CHANGED";
        case SYNCFL_MIDI_E_INTERNAL: return "SYNCFL_MIDI_E_INTERNAL";
        default: return "SYNCFL_MIDI_E_UNKNOWN";
    }
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_list_outputs(
    char* buffer, size_t capacity, size_t* required, uint32_t* count) {
    return Guarded([&]() -> Result {
        if (!required || !count) return BadArgument("required and count must not be NULL");
        if (!buffer && capacity != 0) return BadArgument("buffer is NULL but capacity is not 0");
        *required = 0;
        *count = 0;
        std::vector<std::string> names;
        if (auto listed = Process().ListOutputs(names); !listed.ok()) return listed;
        std::string bytes;
        for (const auto& name : names) {
            bytes += name;
            bytes.push_back('\0');
        }
        bytes.push_back('\0');
        *count = static_cast<uint32_t>(names.size());  // bounded by the device limit
        return CopyOut(bytes, buffer, capacity, required);
    });
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_resolve_output(
    const char* name, uint32_t flags, char* matched, size_t capacity, size_t* required) {
    return Guarded([&]() -> Result {
        if (!name) return BadArgument("name must not be NULL");
        if (!required && (matched || capacity != 0))
            return BadArgument("a matched-name buffer requires a required pointer");
        if (!matched && capacity != 0) return BadArgument("matched is NULL but capacity is not 0");
        if (required) *required = 0;
        Endpoint endpoint;
        if (auto resolved = Process().Resolve(Measure(name), flags, endpoint); !resolved.ok())
            return resolved;
        if (!required) return Result::Success();
        return CopyOut(syncfl::midi::Utf16ToUtf8Lossy(endpoint.name) + '\0', matched, capacity,
                       required);
    });
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_open_output(
    const char* name, uint32_t flags, syncfl_midi_session* session) {
    if (session) *session = 0;
    return Guarded([&]() -> Result {
        if (!name || !session) return BadArgument("name and session must not be NULL");
        std::uint64_t token = 0;
        Result opened = Process().Open(Measure(name), flags, token);
        *session = token;  // 0 unless opened
        return opened;
    });
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_session_name(
    syncfl_midi_session session, char* buffer, size_t capacity, size_t* required) {
    return Guarded([&]() -> Result {
        if (!required) return BadArgument("required must not be NULL");
        if (!buffer && capacity != 0) return BadArgument("buffer is NULL but capacity is not 0");
        *required = 0;
        std::string name;
        if (auto found = Process().SessionName(session, name); !found.ok()) return found;
        name.push_back('\0');
        return CopyOut(name, buffer, capacity, required);
    });
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_send_wakeup(
    syncfl_midi_session session, uint32_t* delivery_attempted) {
    // Tracked outside the firewall so the flag stays truthful even if an
    // exception follows the midiOutShortMsg call.
    bool attempted = false;
    const int32_t status = Guarded([&]() -> Result {
        if (!delivery_attempted) return BadArgument("delivery_attempted must not be NULL");
        return Process().SendWakeup(session, attempted);
    });
    if (delivery_attempted) *delivery_attempted = attempted ? 1u : 0u;
    return status;
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_close(syncfl_midi_session session) {
    return Guarded([&]() -> Result { return Process().Close(session); });
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_last_error(
    uint32_t* native_code, char* buffer, size_t capacity, size_t* required) {
    // Read-only access to the stored error; nothing here allocates or throws.
    if (!buffer && capacity != 0) return SYNCFL_MIDI_E_INVALID_ARGUMENT;
    const auto& last = syncfl::midi::abi::t_last_error;
    const size_t needed = last.message.size() + 1;
    if (required) *required = needed;
    if (native_code) *native_code = last.native;
    if (capacity < needed) return SYNCFL_MIDI_E_BUFFER_TOO_SMALL;
    std::memcpy(buffer, last.message.data(), last.message.size());
    buffer[last.message.size()] = '\0';
    return SYNCFL_MIDI_OK;
}

SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_last_status(void) {
    return syncfl::midi::abi::t_last_error.status;
}

}  // extern "C"
