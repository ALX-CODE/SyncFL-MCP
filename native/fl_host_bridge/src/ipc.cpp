#include "ipc.hpp"
#include <sddl.h>
#include <bcrypt.h>
#include <chrono>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <vector>

namespace alx {
namespace {
std::runtime_error error(const char* operation) {
    return std::runtime_error(std::string(operation) + " failed: " + std::to_string(GetLastError()));
}

HANDLE create_pipe() {
    HANDLE raw_token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &raw_token)) throw error("OpenProcessToken");
    Handle token(raw_token);
    DWORD required = 0;
    GetTokenInformation(token.get(), TokenUser, nullptr, 0, &required);
    std::vector<std::byte> data(required);
    if (!required || !GetTokenInformation(token.get(), TokenUser, data.data(), required, &required))
        throw error("GetTokenInformation");
    LPWSTR sid = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(data.data())->User.Sid, &sid))
        throw error("ConvertSidToStringSidW");
    const std::wstring sddl = L"D:P(A;;GA;;;" + std::wstring(sid) + L")";
    LocalFree(sid);
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr)) throw error("Security descriptor");
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), descriptor, FALSE};
    const auto pipe = CreateNamedPipeW(pipe_name(GetCurrentProcessId()).c_str(),
        PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, sizeof(Response), sizeof(Request), 3000, &security);
    LocalFree(descriptor);
    if (pipe == INVALID_HANDLE_VALUE) throw error("CreateNamedPipeW");
    return pipe;
}

bool finish(HANDLE handle, OVERLAPPED& overlapped, HANDLE stop, DWORD timeout, DWORD& bytes) {
    HANDLE waits[]{overlapped.hEvent, stop};
    const DWORD result = WaitForMultipleObjects(stop ? 2 : 1, waits, FALSE, timeout);
    if (result != WAIT_OBJECT_0) {
        CancelIoEx(handle, &overlapped);
        GetOverlappedResult(handle, &overlapped, &bytes, TRUE);
        return false;
    }
    return GetOverlappedResult(handle, &overlapped, &bytes, FALSE) != FALSE;
}

bool transfer(HANDLE handle, void* buffer, DWORD size, bool write, HANDLE stop, DWORD timeout) {
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event.get()) return false;
    OVERLAPPED overlapped{};
    overlapped.hEvent = event.get();
    DWORD bytes = 0;
    const BOOL immediate = write
        ? WriteFile(handle, buffer, size, &bytes, &overlapped)
        : ReadFile(handle, buffer, size, &bytes, &overlapped);
    if (!immediate) {
        if (GetLastError() != ERROR_IO_PENDING || !finish(handle, overlapped, stop, timeout, bytes))
            return false;
    }
    return bytes == size;
}
} // namespace

std::uint64_t now_ms() {
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}
std::uint64_t random_id() {
    std::uint64_t value = 0;
    if (BCryptGenRandom(nullptr, reinterpret_cast<PUCHAR>(&value), sizeof(value),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 || !value)
        throw std::runtime_error("Secure instance identity unavailable");
    return value;
}
std::wstring pipe_name(std::uint32_t pid) {
    return L"\\\\.\\pipe\\ALX.FLHostBridge." + std::to_wstring(pid);
}
const char* status_name(Status status) {
    switch (status) {
    case Status::ok: return "ok";
    case Status::invalid: return "invalid";
    case Status::expired: return "expired";
    case Status::stale: return "stale";
    case Status::unsupported_abi: return "unsupported_abi";
    case Status::unavailable: return "unavailable";
    case Status::wrong_thread: return "wrong_thread";
    default: return "invalid";
    }
}
void detail(Response& response, const char* text) {
    strncpy_s(response.detail, text, _TRUNCATE);
}
bool validate(const Request& request, Response& response) {
    response.id = request.id;
    response.status = Status::invalid;
    if (request.signature != magic || request.protocol != version || request.bytes != sizeof(Request)
        || !request.id) {
        detail(response, "Invalid fixed-width protocol envelope");
        return false;
    }
    const auto now = now_ms();
    if (request.expires_unix_ms <= now || request.expires_unix_ms - now > 30000) {
        response.status = Status::expired;
        detail(response, "Request expired or exceeds the 30-second validity limit");
        return false;
    }
    if (request.operation == Operation::ping || request.operation == Operation::project_info) {
        if (request.count || request.replace || request.pattern != -1 || request.channel != -1
            || request.expected_instance || request.expected_generation) {
            detail(response, "Read requests must not contain note targets");
            return false;
        }
        return true;
    }
    if (request.operation != Operation::notes_preview || request.pattern < 0 || request.channel < 0
        || request.replace > 1 || !request.count || request.count > max_notes
        || !request.expected_instance) {
        detail(response, "Explicit nonnegative targets, instance, mode and 1..128 notes required");
        return false;
    }
    for (std::uint32_t i = 0; i < request.count; ++i) {
        const auto& n = request.notes[i];
        if (n.position_ticks < 0 || n.length_ticks <= 0 || n.pitch < 0 || n.pitch > 127
            || n.velocity_permille < 0 || n.velocity_permille > 1000
            || n.position_ticks > std::numeric_limits<std::int32_t>::max() - n.length_ticks) {
            detail(response, "Invalid integer note position, length, pitch or normalized velocity");
            return false;
        }
    }
    return true;
}

Server::Server() : pipe_(create_pipe()) {
    if (!stop_.get()) throw error("CreateEventW");
    // A failed background allocation must not unwind out of an in-host thread.
    worker_ = std::thread([this] { try { run(); } catch (...) {} });
}
Server::~Server() {
    SetEvent(stop_.get());
    CancelIoEx(pipe_.get(), nullptr);
    if (worker_.joinable()) worker_.join();
}
std::shared_ptr<Job> Server::take() {
    std::lock_guard lock(mutex_);
    auto job = std::move(pending_);
    pending_.reset();
    return job && !job->cancelled.load() ? job : nullptr;
}
void Server::complete(const std::shared_ptr<Job>& job) { SetEvent(job->done.get()); }
void Server::run() {
    while (WaitForSingleObject(stop_.get(), 0) == WAIT_TIMEOUT) {
        Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.get()) break;
        OVERLAPPED overlapped{};
        overlapped.hEvent = event.get();
        DWORD bytes = 0;
        bool connected = ConnectNamedPipe(pipe_.get(), &overlapped) != FALSE;
        if (!connected) {
            const auto code = GetLastError();
            connected = code == ERROR_PIPE_CONNECTED
                || (code == ERROR_IO_PENDING
                    && finish(pipe_.get(), overlapped, stop_.get(), INFINITE, bytes));
        }
        if (!connected) continue;
        auto job = std::make_shared<Job>();
        if (!job->done.get()) break;
        if (transfer(pipe_.get(), &job->request, sizeof(Request), false, stop_.get(), 3000)) {
            if (validate(job->request, job->response)) {
                {
                    std::lock_guard lock(mutex_);
                    pending_ = job;
                }
                HANDLE waits[]{job->done.get(), stop_.get()};
                if (WaitForMultipleObjects(2, waits, FALSE, 3000) != WAIT_OBJECT_0) {
                    job->cancelled.store(true);
                    std::lock_guard lock(mutex_);
                    if (pending_ == job) pending_.reset();
                    // No late response or automatic retry after an unobserved callback.
                    DisconnectNamedPipe(pipe_.get());
                    continue;
                }
            }
            transfer(pipe_.get(), &job->response, sizeof(Response), true, stop_.get(), 3000);
        }
        DisconnectNamedPipe(pipe_.get());
    }
}

Response call(std::uint32_t pid, const Request& request, std::uint32_t timeout_ms) {
    if (!pid || !timeout_ms || timeout_ms > 10000) throw std::invalid_argument("Invalid PID or timeout");
    const auto name = pipe_name(pid);
    if (!WaitNamedPipeW(name.c_str(), timeout_ms)) throw error("WaitNamedPipeW");
    Handle pipe(CreateFileW(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                           FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
                           nullptr));
    if (pipe.get() == INVALID_HANDLE_VALUE) throw error("CreateFileW");
    ULONG server_pid = 0;
    if (!GetNamedPipeServerProcessId(pipe.get(), &server_pid) || server_pid != pid)
        throw std::runtime_error("Pipe server PID does not match requested FL process");
    DWORD mode = PIPE_READMODE_MESSAGE;
    if (!SetNamedPipeHandleState(pipe.get(), &mode, nullptr, nullptr)) throw error("Pipe read mode");
    Request copy = request;
    Response response;
    if (!transfer(pipe.get(), &copy, sizeof(copy), true, nullptr, timeout_ms)
        || !transfer(pipe.get(), &response, sizeof(response), false, nullptr, timeout_ms))
        throw std::runtime_error("Pipe transfer timed out or returned an invalid message size");
    if (response.signature != magic || response.protocol != version
        || response.bytes != sizeof(Response) || response.id != request.id
        || response.detail[sizeof(response.detail) - 1] != '\0')
        throw std::runtime_error("Invalid or uncorrelated pipe response");
    return response;
}
} // namespace alx
