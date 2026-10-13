#include "protocol.hpp"
#include "data_object.hpp"
#include <filesystem>

namespace {
using namespace background;
volatile LONG active = 0;
struct ActiveGuard {
    ~ActiveGuard() { InterlockedExchange(&active, 0); }
};
bool readable(const void* pointer, size_t bytes, bool executable = false) {
    MEMORY_BASIC_INFORMATION info{};
    if (!pointer || !VirtualQuery(pointer, &info, sizeof(info)) || info.State != MEM_COMMIT ||
        (info.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
    auto start = reinterpret_cast<ULONG_PTR>(pointer);
    auto end = reinterpret_cast<ULONG_PTR>(info.BaseAddress) + info.RegionSize;
    if (start > end || bytes > end - start) return false;
    DWORD access = info.Protect & 0xff;
    return executable ? (access == PAGE_EXECUTE || access == PAGE_EXECUTE_READ ||
        access == PAGE_EXECUTE_READWRITE || access == PAGE_EXECUTE_WRITECOPY) :
        (access == PAGE_READONLY || access == PAGE_READWRITE || access == PAGE_WRITECOPY ||
         access == PAGE_EXECUTE_READ || access == PAGE_EXECUTE_READWRITE ||
         access == PAGE_EXECUTE_WRITECOPY);
}
bool interface_memory(void* object) {
    if (!readable(object, sizeof(void*))) return false;
    void** table = nullptr; SIZE_T got = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), object, &table, sizeof(table), &got) ||
        got != sizeof(table) || !readable(table, 7 * sizeof(void*))) return false;
    void* methods[7]{};
    if (!ReadProcessMemory(GetCurrentProcess(), table, methods, sizeof(methods), &got) ||
        got != sizeof(methods)) return false;
    for (auto method : methods) if (!readable(method, 1, true)) return false;
    return true;
}
HRESULT query_target(void* object, IDropTarget** target) {
    __try { return static_cast<IUnknown*>(object)->QueryInterface(IID_IDropTarget,
                                                               reinterpret_cast<void**>(target)); }
    __except(EXCEPTION_EXECUTE_HANDLER) { return E_UNEXPECTED; }
}
HRESULT enter_target(IDropTarget* target, IDataObject* data, POINTL point, DWORD* effect) {
    __try { return target->DragEnter(data, MK_LBUTTON, point, effect); }
    __except(EXCEPTION_EXECUTE_HANDLER) { return E_UNEXPECTED; }
}
HRESULT drop_target(IDropTarget* target, IDataObject* data, POINTL point, DWORD* effect) {
    __try { return target->Drop(data, 0, point, effect); }
    __except(EXCEPTION_EXECUTE_HANDLER) { return E_UNEXPECTED; }
}
HRESULT leave_target(IDropTarget* target) {
    __try { return target->DragLeave(); }
    __except(EXCEPTION_EXECUTE_HANDLER) { return E_UNEXPECTED; }
}
void release_target(IDropTarget* target) {
    __try { target->Release(); } __except(EXCEPTION_EXECUTE_HANDLER) {}
}
struct TargetGuard {
    IDropTarget* value;
    ~TargetGuard() { release_target(value); }
};
struct DataGuard {
    FileData* value;
    ~DataGuard() { value->Release(); }
};
void finish(Request& request, State state, DWORD error = 0) {
    request.error = error;
    InterlockedExchange(&request.state, static_cast<LONG>(state));
}
void execute(Request& request, HWND hwnd, const std::array<unsigned char, 16>& nonce) {
    if (request.signature != magic || request.schema != version || request.bytes != sizeof(Request) ||
        request.target_pid != GetCurrentProcessId() || request.target_tid != GetCurrentThreadId() ||
        request.target_hwnd != reinterpret_cast<ULONG_PTR>(hwnd) || request.nonce != nonce ||
        request.process_created != creation_time(GetCurrentProcess()) ||
        !std::wmemchr(request.image, 0, path_capacity) ||
        !std::wmemchr(request.file, 0, path_capacity)) return;
    wchar_t image[path_capacity]{};
    DWORD count = GetModuleFileNameW(nullptr, image, static_cast<DWORD>(path_capacity));
    if (!count || count >= path_capacity || _wcsicmp(image, request.image) != 0) return;
    if (InterlockedCompareExchange(&request.state, static_cast<LONG>(State::running),
                                  static_cast<LONG>(State::pending)) != static_cast<LONG>(State::pending)) return;
    if (InterlockedCompareExchange(&active, 1, 0) != 0) {
        finish(request, State::rejected, ERROR_BUSY); return;
    }
    ActiveGuard active_guard;
    if (GetTickCount64() >= request.deadline) { finish(request, State::unknown, WAIT_TIMEOUT); return; }
    void* property = GetPropW(hwnd, L"OleDropTargetInterface");
    request.property = reinterpret_cast<ULONG_PTR>(property);
    request.memory_valid = interface_memory(property);
    if (!property || !request.memory_valid) { finish(request, State::unsupported); return; }
    if (request.operation == Operation::probe) { finish(request, State::probed); return; }
    if (request.operation != Operation::drop) { finish(request, State::rejected, ERROR_INVALID_DATA); return; }
    std::filesystem::path path(request.file);
    std::wstring extension = path.extension().wstring();
    DWORD attributes = GetFileAttributesW(request.file);
    if (!path.is_absolute() || attributes == INVALID_FILE_ATTRIBUTES ||
        (attributes & FILE_ATTRIBUTE_DIRECTORY) ||
        (_wcsicmp(extension.c_str(), L".fst") && _wcsicmp(extension.c_str(), L".wav") &&
         _wcsicmp(extension.c_str(), L".flp"))) {
        finish(request, State::rejected, ERROR_INVALID_DATA); return;
    }
    IDropTarget* target = nullptr;
    request.query_hr = query_target(property, &target);
    if (FAILED(request.query_hr) || !target) {
        finish(request, request.query_hr == E_UNEXPECTED ? State::unknown : State::unsupported);
        return;
    }
    if (!interface_memory(target)) { finish(request, State::unknown, ERROR_INVALID_ADDRESS); return; }
    TargetGuard target_guard{target};
    if (GetTickCount64() >= request.deadline) {
        finish(request, State::unknown, WAIT_TIMEOUT); return;
    }
    auto data = new FileData(request.file);
    DataGuard data_guard{data};
    POINTL point{request.point.x, request.point.y};
    DWORD effect = DROPEFFECT_COPY;
    request.enter_hr = enter_target(target, data, point, &effect);
    request.effect = effect;
    if (FAILED(request.enter_hr)) {
        finish(request, State::unknown); return;
    }
    InterlockedExchange(&request.state, static_cast<LONG>(State::entered));
    if (!(effect & DROPEFFECT_COPY) || GetTickCount64() >= request.deadline) {
        HRESULT left = leave_target(target);
        bool expired = GetTickCount64() >= request.deadline;
        finish(request, SUCCEEDED(left) && !expired ? State::rejected : State::unknown,
               expired ? WAIT_TIMEOUT : 0); return;
    }
    // The point and key state have not moved since entry. Drop directly;
    // a synthetic DragOver is neither a prerequisite nor a runtime fallback.
    effect = DROPEFFECT_COPY;
    request.drop_hr = drop_target(target, data, point, &effect);
    request.effect = effect;
    finish(request, SUCCEEDED(request.drop_hr) && effect == DROPEFFECT_COPY ?
           State::dropped : State::unknown);
}
}

extern "C" __declspec(dllexport) LRESULT CALLBACK BackgroundHook(int code, WPARAM first, LPARAM second) {
    if (code == HC_ACTION) {
        auto message = reinterpret_cast<const CWPSTRUCT*>(second);
        if (message && message->message == RegisterWindowMessageW(background::message_name)) {
            std::array<unsigned char, 16> nonce{};
            std::memcpy(nonce.data(), &message->wParam, 8);
            std::memcpy(nonce.data() + 8, &message->lParam, 8);
            auto name = background::mapping_name(nonce);
            HANDLE mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name.c_str());
            if (mapping) {
                auto request = static_cast<background::Request*>(MapViewOfFile(
                    mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(background::Request)));
                if (request) {
                    try { execute(*request, message->hwnd, nonce); }
                    catch (...) { finish(*request, background::State::unknown, ERROR_UNHANDLED_EXCEPTION); }
                    UnmapViewOfFile(request);
                }
                CloseHandle(mapping);
            }
        }
    }
    return CallNextHookEx(nullptr, code, first, second);
}
