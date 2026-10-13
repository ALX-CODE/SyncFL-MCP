#pragma once
#include <windows.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>

namespace background {
constexpr DWORD magic = 0x414c5842;
constexpr DWORD version = 3;
constexpr size_t path_capacity = 4096;
constexpr wchar_t message_name[] = L"ALX.FL.ExperimentalBackgroundDrop.v1";
enum class State : LONG { pending, running, probed, entered, dropped, rejected, unsupported, unknown };
enum class Operation : DWORD { probe, drop };
struct Request {
    DWORD signature = magic;
    DWORD schema = version;
    DWORD bytes = sizeof(Request);
    DWORD target_pid = 0;
    DWORD target_tid = 0;
    Operation operation = Operation::probe;
    ULONG_PTR target_hwnd = 0;
    ULONGLONG process_created = 0;
    ULONGLONG deadline = 0;
    std::array<unsigned char, 16> nonce{};
    POINT point{};
    wchar_t image[path_capacity]{};
    wchar_t file[path_capacity]{};
    volatile LONG state = static_cast<LONG>(State::pending);
    ULONG_PTR property = 0;
    DWORD memory_valid = 0;
    DWORD error = 0;
    HRESULT query_hr = E_PENDING;
    HRESULT enter_hr = E_PENDING;
    HRESULT over_hr = S_FALSE; // Intentionally skipped; no DragOver callback is sent.
    HRESULT drop_hr = E_PENDING;
    DWORD effect = 0;
};
static_assert(sizeof(void*) == 8);
inline std::wstring mapping_name(const std::array<unsigned char, 16>& nonce) {
    constexpr wchar_t digits[] = L"0123456789abcdef";
    std::wstring name = L"Local\\ALX.FL.Background.";
    for (auto value : nonce) { name += digits[value >> 4]; name += digits[value & 15]; }
    return name;
}
inline ULONGLONG creation_time(HANDLE process) {
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(process, &created, &exited, &kernel, &user)) return 0;
    return (static_cast<ULONGLONG>(created.dwHighDateTime) << 32) | created.dwLowDateTime;
}
inline const char* state_name(State state) {
    switch (state) {
    case State::pending: return "pending"; case State::running: return "running";
    case State::probed: return "probed"; case State::entered: return "entered";
    case State::dropped: return "dropped"; case State::rejected: return "rejected";
    case State::unsupported: return "unsupported"; default: return "unknown";
    }
}
struct Config {
    DWORD pid = 0;
    HWND hwnd = nullptr;
    POINT point{};
    std::wstring expected_image;
    std::wstring file;
    bool drop = false;
    DWORD timeout_ms = 5000;
};
struct Result {
    State state = State::rejected;
    DWORD error = 0;
    ULONG_PTR property = 0;
    bool memory_valid = false;
    HRESULT query_hr = E_PENDING, enter_hr = E_PENDING, over_hr = S_FALSE, drop_hr = E_PENDING;
    DWORD effect = 0;
};
Result run(const Config& config);
void print_result(const Result& result);
}
