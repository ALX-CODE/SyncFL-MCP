#include "transport.hpp"

#include "utf.hpp"

#include <cwchar>
#include <utility>

namespace syncfl::midi {
namespace {

// WinMM offers no atomic device snapshot; retry a changing set a few times.
constexpr int kSnapshotAttempts = 4;
constexpr UINT kMaxDevices = 4096;
// A 31-code-unit name needs at most 4 UTF-8 bytes per unit; reject larger input
// before decoding it.
constexpr std::size_t kMaxRequestBytes = kMaxNameUnits * 4;

Result Fail(Status status, std::string message, MMRESULT native = MMSYSERR_NOERROR) {
    return Result{status, static_cast<std::uint32_t>(native), std::move(message)};
}

std::string Quote(std::wstring_view name) { return "'" + Utf16ToUtf8Lossy(name) + "'"; }

// Fields that identify a device rather than describe its capabilities.
bool SameDevice(const MIDIOUTCAPSW& a, const MIDIOUTCAPSW& b) {
    return a.wMid == b.wMid && a.wPid == b.wPid && a.vDriverVersion == b.vDriverVersion &&
           a.wTechnology == b.wTechnology && CapsName(a) == CapsName(b);
}

// Closes an opened handle on every early return until ownership is released.
class HandleGuard {
public:
    HandleGuard(MidiOutputApi& api, HMIDIOUT handle) noexcept : api_(api), handle_(handle) {}
    ~HandleGuard() {
        if (handle_) api_.Close(handle_);
    }
    HandleGuard(const HandleGuard&) = delete;
    HandleGuard& operator=(const HandleGuard&) = delete;
    HMIDIOUT Release() noexcept { return std::exchange(handle_, nullptr); }

private:
    MidiOutputApi& api_;
    HMIDIOUT handle_;
};

}  // namespace

bool NameMatches(std::wstring_view device, std::wstring_view requested,
                 std::uint32_t flags) noexcept {
    if (device == requested) return true;
    if ((flags & kAllowDecimalSuffix) == 0) return false;
    // "<requested> <ASCII digits>", matching (?: [0-9]+)? in the Python layer.
    if (device.size() < requested.size() + 2 || device.substr(0, requested.size()) != requested ||
        device[requested.size()] != L' ')
        return false;
    for (const wchar_t ch : device.substr(requested.size() + 1))
        if (ch < L'0' || ch > L'9') return false;
    return true;
}

std::wstring CapsName(const MIDIOUTCAPSW& caps) {
    return std::wstring(caps.szPname, wcsnlen(caps.szPname, MAXPNAMELEN));
}

Result Transport::Snapshot(std::vector<Endpoint>& endpoints) {
    for (int attempt = 0; attempt < kSnapshotAttempts; ++attempt) {
        const UINT count = api_.DeviceCount();
        if (count > kMaxDevices)
            return Fail(Status::Native, "WinMM reported an implausible number of MIDI outputs (" +
                                            std::to_string(count) + ")");
        std::vector<Endpoint> current;
        current.reserve(count);
        bool changed = false;
        for (UINT device = 0; device < count; ++device) {
            Endpoint endpoint;
            endpoint.device = device;
            const MMRESULT read = api_.DeviceCaps(device, endpoint.caps);
            if (read == MMSYSERR_BADDEVICEID) {
                changed = true;  // a device vanished mid-enumeration
                break;
            }
            if (read != MMSYSERR_NOERROR)
                return Fail(Status::Native,
                            "cannot read the capabilities of MIDI output " +
                                std::to_string(device) + ", so uniqueness cannot be established",
                            read);
            endpoint.name = CapsName(endpoint.caps);
            current.push_back(std::move(endpoint));
        }
        if (changed || api_.DeviceCount() != count) continue;
        endpoints = std::move(current);
        return Result::Success();
    }
    return Fail(Status::DeviceSetChanged,
                "the set of WinMM MIDI outputs kept changing during enumeration; try again");
}

Result Transport::Validate(std::string_view name, std::uint32_t flags, std::wstring& requested) {
    if ((flags & ~kKnownFlags) != 0) return Fail(Status::InvalidArgument, "unknown open flags");
    if (name.empty()) return Fail(Status::InvalidArgument, "the endpoint name is empty");
    if (name.size() > kMaxRequestBytes)
        return Fail(Status::InvalidArgument, "the endpoint name is too long for WinMM");
    auto decoded = Utf8ToUtf16Strict(name);
    if (!decoded) return Fail(Status::InvalidArgument, "the endpoint name is not valid UTF-8");
    if (decoded->size() > kMaxNameUnits)
        return Fail(Status::InvalidArgument,
                    "the endpoint name exceeds WinMM's 31-character product-name limit and can "
                    "never match exactly");
    requested = std::move(*decoded);
    return Result::Success();
}

Result Transport::Match(const std::vector<Endpoint>& endpoints, const std::wstring& requested,
                        std::uint32_t flags, Endpoint& endpoint) {
    const Endpoint* found = nullptr;
    std::size_t matches = 0;
    for (const auto& candidate : endpoints) {
        if (NameMatches(candidate.name, requested, flags)) {
            ++matches;
            found = &candidate;
        }
    }
    const std::string spelled =
        Quote(requested) + ((flags & kAllowDecimalSuffix) ? " (or with a decimal suffix)" : "");
    if (matches == 0)
        return Fail(Status::NotFound, "no WinMM MIDI output is named " + spelled);
    if (matches > 1)
        return Fail(Status::Ambiguous, std::to_string(matches) + " WinMM MIDI outputs match " +
                                           spelled + "; refusing to choose one");
    endpoint = *found;
    return Result::Success();
}

Result Transport::ListOutputs(std::vector<std::string>& names) {
    std::vector<Endpoint> endpoints;
    if (auto listed = Snapshot(endpoints); !listed.ok()) return listed;
    names.clear();
    names.reserve(endpoints.size());
    for (const auto& endpoint : endpoints) names.push_back(Utf16ToUtf8Lossy(endpoint.name));
    return Result::Success();
}

Result Transport::Resolve(std::string_view name, std::uint32_t flags, Endpoint& endpoint) {
    std::wstring requested;
    if (auto valid = Validate(name, flags, requested); !valid.ok()) return valid;
    std::vector<Endpoint> endpoints;
    if (auto listed = Snapshot(endpoints); !listed.ok()) return listed;
    return Match(endpoints, requested, flags, endpoint);
}

Result Transport::Open(std::string_view name, std::uint32_t flags, std::uint64_t& session) {
    session = 0;
    std::wstring requested;
    if (auto valid = Validate(name, flags, requested); !valid.ok()) return valid;
    std::vector<Endpoint> endpoints;
    if (auto listed = Snapshot(endpoints); !listed.ok()) return listed;
    Endpoint endpoint;
    if (auto matched = Match(endpoints, requested, flags, endpoint); !matched.ok()) return matched;

    const std::string target = Quote(endpoint.name);
    HMIDIOUT raw = nullptr;
    const MMRESULT opened = api_.Open(raw, endpoint.device);
    switch (opened) {
        case MMSYSERR_NOERROR:
            break;
        case MMSYSERR_BADDEVICEID:
        case MMSYSERR_NODRIVER:
            return Fail(Status::EndpointChanged, target + " disappeared before it could be opened",
                        opened);
        case MMSYSERR_ALLOCATED:
            return Fail(Status::Busy, target + " is allocated by another client", opened);
        case MMSYSERR_NOMEM:
            return Fail(Status::OutOfMemory, "WinMM ran out of memory opening " + target, opened);
        default:
            return Fail(Status::Native, "midiOutOpen failed for " + target, opened);
    }
    HandleGuard guard(api_, raw);

    // The device index may have shifted between enumeration and open: verify the
    // device behind the handle, then confirm the name still resolves uniquely.
    MIDIOUTCAPSW actual{};
    const MMRESULT identity = api_.HandleCaps(raw, actual);
    if (identity != MMSYSERR_NOERROR)
        return Fail(Status::Native, "cannot verify the identity of the opened output " + target,
                    identity);
    if (!SameDevice(actual, endpoint.caps))
        return Fail(Status::EndpointChanged, "the MIDI outputs changed while opening " + target +
                                                 "; the handle belongs to " +
                                                 Quote(CapsName(actual)));
    std::vector<Endpoint> after;
    if (auto listed = Snapshot(after); !listed.ok()) return listed;
    Endpoint again;
    const Result recheck = Match(after, requested, flags, again);
    if (!recheck.ok() || !SameDevice(again.caps, endpoint.caps))
        return Fail(Status::EndpointChanged,
                    "the match for " + Quote(requested) + " changed while opening" +
                        (recheck.ok() ? std::string() : ": " + recheck.message));

    auto entry = std::make_shared<Session>();
    entry->endpoint = std::move(endpoint);
    entry->name_utf8 = Utf16ToUtf8Lossy(entry->endpoint.name);
    std::lock_guard lock(table_mutex_);
    const std::uint64_t token = next_session_++;  // never reused, even if insertion fails
    sessions_.emplace(token, entry);
    entry->handle = guard.Release();  // unreachable to other threads until the lock drops
    session = token;
    return Result::Success();
}

std::shared_ptr<Transport::Session> Transport::Find(std::uint64_t session) {
    std::lock_guard lock(table_mutex_);
    const auto found = sessions_.find(session);
    return found == sessions_.end() ? nullptr : found->second;
}

Result Transport::SessionName(std::uint64_t session, std::string& name) {
    const auto entry = Find(session);
    if (!entry) return Fail(Status::InvalidHandle, "unknown or closed MIDI session");
    std::lock_guard lock(entry->mutex);
    if (!entry->handle) return Fail(Status::InvalidHandle, "the MIDI session was closed");
    name = entry->name_utf8;
    return Result::Success();
}

Result Transport::SendWakeup(std::uint64_t session, bool& attempted) {
    attempted = false;
    const auto entry = Find(session);
    if (!entry) return Fail(Status::InvalidHandle, "unknown or closed MIDI session");
    std::lock_guard lock(entry->mutex);
    if (!entry->handle) return Fail(Status::InvalidHandle, "the MIDI session was closed");
    attempted = true;  // set before the call: a failure below may still have delivered
    const MMRESULT sent = api_.ShortMessage(entry->handle, kWakeupMessage);
    if (sent != MMSYSERR_NOERROR)
        return Fail(Status::SendFailed,
                    "midiOutShortMsg failed for '" + entry->name_utf8 +
                        "'; delivery is unknown and the wakeup must not be retried blindly",
                    sent);
    return Result::Success();
}

Result Transport::Close(std::uint64_t session) {
    std::shared_ptr<Session> entry;
    {
        std::lock_guard lock(table_mutex_);
        const auto found = sessions_.find(session);
        if (found == sessions_.end())
            return Fail(Status::InvalidHandle, "unknown or already closed MIDI session");
        entry = std::move(found->second);
        sessions_.erase(found);
    }
    std::lock_guard lock(entry->mutex);  // waits for an in-flight send to finish
    const HMIDIOUT handle = std::exchange(entry->handle, nullptr);
    const MMRESULT closed = api_.Close(handle);
    if (closed != MMSYSERR_NOERROR)
        return Fail(Status::Native,
                    "midiOutClose failed for '" + entry->name_utf8 +
                        "'; the session is closed regardless",
                    closed);
    return Result::Success();
}

std::size_t Transport::OpenSessionCount() {
    std::lock_guard lock(table_mutex_);
    return sessions_.size();
}

}  // namespace syncfl::midi
