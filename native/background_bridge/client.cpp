#include "protocol.hpp"
#include <bcrypt.h>
#include <sddl.h>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <vector>

namespace background {
namespace {
struct Handle {
    HANDLE value = nullptr;
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};
struct Library {
    HMODULE value = nullptr;
    ~Library() { if (value) FreeLibrary(value); }
};
struct View {
    void* value = nullptr;
    ~View() { if (value) UnmapViewOfFile(value); }
};
struct LocalMemory {
    void* value = nullptr;
    ~LocalMemory() { if (value) LocalFree(value); }
};
std::wstring full_path(const std::wstring& path) {
    wchar_t buffer[path_capacity]{};
    DWORD count = GetFullPathNameW(path.c_str(), static_cast<DWORD>(path_capacity), buffer, nullptr);
    if (!count || count >= path_capacity) throw std::runtime_error("path too long");
    return buffer;
}
bool allowed_file(const std::wstring& path) {
    if (!std::filesystem::path(path).is_absolute()) return false;
    auto extension = std::filesystem::path(path).extension().wstring();
    return !_wcsicmp(extension.c_str(), L".fst") || !_wcsicmp(extension.c_str(), L".wav") ||
           !_wcsicmp(extension.c_str(), L".flp") || !_wcsicmp(extension.c_str(), L".fsc");
}
}
Result run(const Config& config) {
    Result result;
    if (!config.pid || !config.hwnd || config.timeout_ms < 100 || config.timeout_ms > 30000 ||
        config.expected_image.empty()) { result.error = ERROR_INVALID_PARAMETER; return result; }
    DWORD pid = 0;
    DWORD tid = GetWindowThreadProcessId(config.hwnd, &pid);
    if (!tid || pid != config.pid) { result.error = ERROR_INVALID_WINDOW_HANDLE; return result; }
    Handle process{OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid)};
    if (!process.value) { result.error = GetLastError(); return result; }
    wchar_t image[path_capacity]{}; DWORD count = static_cast<DWORD>(path_capacity);
    if (!QueryFullProcessImageNameW(process.value, 0, image, &count)) {
        result.error = GetLastError(); return result;
    }
    auto expected_image = full_path(config.expected_image);
    if (_wcsicmp(expected_image.c_str(), image) || WaitForSingleObject(process.value, 0) != WAIT_TIMEOUT) {
        result.error = ERROR_ACCESS_DENIED; return result;
    }
    USHORT machine = 0, native_machine = 0;
    if (!IsWow64Process2(process.value, &machine, &native_machine) || machine != IMAGE_FILE_MACHINE_UNKNOWN ||
        native_machine != IMAGE_FILE_MACHINE_AMD64) { result.error = ERROR_BAD_EXE_FORMAT; return result; }
    ULONGLONG created = creation_time(process.value);
    if (!created) { result.error = GetLastError(); return result; }
    Handle file;
    std::wstring filename;
    if (config.drop) {
        if (!allowed_file(config.file)) { result.error = ERROR_INVALID_PARAMETER; return result; }
        filename = full_path(config.file);
        file.value = CreateFileW(filename.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                 FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file.value == INVALID_HANDLE_VALUE) { result.error = GetLastError(); return result; }
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(file.value, &info) || (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
            result.error = ERROR_INVALID_DATA; return result;
        }
    }
    Handle token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value)) {
        result.error = GetLastError(); return result;
    }
    DWORD size = 0;
    GetTokenInformation(token.value, TokenUser, nullptr, 0, &size);
    std::vector<BYTE> token_data(size);
    if (!size || !GetTokenInformation(token.value, TokenUser, token_data.data(), size, &size)) {
        result.error = GetLastError(); return result;
    }
    LocalMemory sid;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(token_data.data())->User.Sid,
                               reinterpret_cast<LPWSTR*>(&sid.value))) {
        result.error = GetLastError(); return result;
    }
    std::wstring descriptor = L"D:P(A;;GA;;;" + std::wstring(static_cast<wchar_t*>(sid.value)) + L")";
    LocalMemory security;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(descriptor.c_str(), SDDL_REVISION_1,
            reinterpret_cast<PSECURITY_DESCRIPTOR*>(&security.value), nullptr)) {
        result.error = GetLastError(); return result;
    }
    SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), security.value, FALSE};
    std::array<unsigned char, 16> nonce{};
    if (BCryptGenRandom(nullptr, nonce.data(), static_cast<ULONG>(nonce.size()),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
        result.error = ERROR_GEN_FAILURE; return result;
    }
    auto name = mapping_name(nonce);
    Handle mapping{CreateFileMappingW(INVALID_HANDLE_VALUE, &attributes, PAGE_READWRITE, 0,
                                     sizeof(Request), name.c_str())};
    DWORD mapping_error = GetLastError();
    if (!mapping.value || mapping_error == ERROR_ALREADY_EXISTS) {
        result.error = mapping_error; return result;
    }
    View view{MapViewOfFile(mapping.value, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(Request))};
    if (!view.value) { result.error = GetLastError(); return result; }
    auto request = new(view.value) Request{};
    request->target_pid = pid; request->target_tid = tid;
    request->target_hwnd = reinterpret_cast<ULONG_PTR>(config.hwnd);
    request->process_created = created; request->nonce = nonce;
    request->operation = config.drop ? Operation::drop : Operation::probe;
    request->point = config.point; request->deadline = GetTickCount64() + config.timeout_ms;
    wcscpy_s(request->image, image); wcscpy_s(request->file, filename.c_str());
    wchar_t module[path_capacity]{};
    count = GetModuleFileNameW(nullptr, module, static_cast<DWORD>(path_capacity));
    if (!count || count >= path_capacity) { result.error = ERROR_BAD_PATHNAME; return result; }
    auto dll = std::filesystem::path(module).parent_path() / L"alx-background-hook.dll";
    Library library{LoadLibraryExW(dll.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                                  LOAD_LIBRARY_SEARCH_SYSTEM32)};
    if (!library.value) { result.error = GetLastError(); return result; }
    auto procedure = reinterpret_cast<HOOKPROC>(GetProcAddress(library.value, "BackgroundHook"));
    if (!procedure) { result.error = GetLastError(); return result; }
    HHOOK hook = SetWindowsHookExW(WH_CALLWNDPROC, procedure, library.value, tid);
    if (!hook) { result.error = GetLastError(); return result; }
    WPARAM low = 0; LPARAM high = 0;
    std::memcpy(&low, nonce.data(), 8); std::memcpy(&high, nonce.data() + 8, 8);
    UINT message = RegisterWindowMessageW(message_name);
    DWORD_PTR response = 0;
    SetLastError(ERROR_SUCCESS);
    LRESULT sent = message ? SendMessageTimeoutW(config.hwnd, message, low, high,
                         SMTO_ABORTIFHUNG | SMTO_BLOCK, config.timeout_ms, &response) : 0;
    DWORD error = GetLastError();
    UnhookWindowsHookEx(hook); // Does not cancel an already running callback.
    if (!sent) {
        InterlockedCompareExchange(&request->state, static_cast<LONG>(State::unknown),
                                    static_cast<LONG>(State::pending));
        result.state = State::unknown; result.error = error ? error : WAIT_TIMEOUT;
        return result; // No retry and no reading fields a late callback may still write.
    }
    result.state = static_cast<State>(InterlockedCompareExchange(&request->state, 0, 0));
    if (result.state == State::pending || result.state == State::running || result.state == State::entered) {
        result.state = State::unknown; result.error = ERROR_IO_INCOMPLETE; return result;
    }
    result.error = request->error; result.property = request->property;
    result.memory_valid = request->memory_valid != 0;
    result.query_hr = request->query_hr; result.enter_hr = request->enter_hr;
    result.over_hr = request->over_hr;
    result.drop_hr = request->drop_hr; result.effect = request->effect;
    return result;
}
void print_result(const Result& result) {
    std::printf("{\"experimental\":true,\"state\":\"%s\",\"error\":%lu,\"property\":%llu,"
                "\"memory_valid\":%s,\"query_hr\":%ld,\"enter_hr\":%ld,\"over_hr\":%ld,\"drop_hr\":%ld,"
                "\"over_skipped\":true,\"effect\":%lu,\"native_state_verified\":false}\n", state_name(result.state),
                result.error, static_cast<unsigned long long>(result.property),
                result.memory_valid ? "true" : "false", result.query_hr, result.enter_hr, result.over_hr,
                result.drop_hr, result.effect);
}
}
