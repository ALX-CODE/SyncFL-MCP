#include "protocol.hpp"
#include "data_object.hpp"
#include <shellapi.h>
#include <cstdio>
#include <filesystem>

namespace {
std::wstring expected_file;
int enters = 0, overs = 0, drops = 0;
bool correct_data = true;
class Target final : public IDropTarget {
    ULONG refs_ = 1;
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr; if (iid != IID_IUnknown && iid != IID_IDropTarget) return E_NOINTERFACE;
        *out = static_cast<IDropTarget*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override { return --refs_; }
    HRESULT STDMETHODCALLTYPE DragEnter(IDataObject* data, DWORD, POINTL, DWORD* effect) override {
        ++enters;
        FORMATETC format{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        *effect = SUCCEEDED(data->QueryGetData(&format)) ? DROPEFFECT_COPY : DROPEFFECT_NONE;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE DragOver(DWORD, POINTL, DWORD* effect) override {
        ++overs;
        *effect = DROPEFFECT_NONE; return E_UNEXPECTED;
    }
    HRESULT STDMETHODCALLTYPE DragLeave() override { return S_OK; }
    HRESULT STDMETHODCALLTYPE Drop(IDataObject* data, DWORD, POINTL, DWORD* effect) override {
        ++drops;
        FORMATETC format{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        STGMEDIUM first{}, second{};
        if (FAILED(data->GetData(&format, &first)) || FAILED(data->GetData(&format, &second))) {
            correct_data = false;
        } else {
            wchar_t file[background::path_capacity]{};
            UINT count = DragQueryFileW(static_cast<HDROP>(first.hGlobal), 0xffffffff, nullptr, 0);
            DragQueryFileW(static_cast<HDROP>(first.hGlobal), 0, file,
                           static_cast<UINT>(background::path_capacity));
            correct_data = count == 1 && file == expected_file && first.hGlobal != second.hGlobal;
        }
        if (first.tymed) ReleaseStgMedium(&first);
        if (second.tymed) ReleaseStgMedium(&second);
        *effect = correct_data ? DROPEFFECT_COPY : DROPEFFECT_NONE; return S_OK;
    }
};
LRESULT CALLBACK window_proc(HWND hwnd, UINT message, WPARAM first, LPARAM second) {
    return DefWindowProcW(hwnd, message, first, second);
}
std::wstring executable() {
    wchar_t path[background::path_capacity]{};
    DWORD count = GetModuleFileNameW(nullptr, path, static_cast<DWORD>(background::path_capacity));
    return count && count < background::path_capacity ? path : L"";
}
int child(HWND hwnd, int mode, const std::wstring& file) {
    std::wstring command = L"\"" + executable() + L"\" --client " +
        std::to_wstring(GetCurrentProcessId()) + L" " +
        std::to_wstring(reinterpret_cast<ULONG_PTR>(hwnd)) + L" " + std::to_wstring(mode) +
        L" \"" + file + L"\"";
    STARTUPINFOW startup{sizeof(startup)};
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                        nullptr, nullptr, &startup, &process)) return 20;
    CloseHandle(process.hThread);
    if (mode == 3) Sleep(400); // Deliberately do not pump until the sender's deadline expires.
    ULONGLONG deadline = GetTickCount64() + 10000;
    while (GetTickCount64() < deadline) {
        DWORD status = MsgWaitForMultipleObjects(1, &process.hProcess, FALSE, 100, QS_ALLINPUT);
        if (status == WAIT_OBJECT_0) break;
        MSG message{};
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message); DispatchMessageW(&message);
        }
    }
    DWORD code = 21;
    GetExitCodeProcess(process.hProcess, &code);
    if (code == STILL_ACTIVE) {
        TerminateProcess(process.hProcess, 21); WaitForSingleObject(process.hProcess, 2000); code = 21;
    }
    CloseHandle(process.hProcess); return static_cast<int>(code);
}
}
int wmain(int argc, wchar_t** argv) {
    if (argc == 6 && std::wstring(argv[1]) == L"--client") {
        background::Config config;
        config.pid = static_cast<DWORD>(std::wcstoul(argv[2], nullptr, 10));
        config.hwnd = reinterpret_cast<HWND>(std::wcstoull(argv[3], nullptr, 10));
        int mode = static_cast<int>(std::wcstol(argv[4], nullptr, 10));
        config.drop = mode == 1;
        if (mode == 3) config.timeout_ms = 100;
        config.expected_image = executable(); config.point = {17, 19};
        if (config.drop) config.file = argv[5];
        auto result = background::run(config); background::print_result(result);
        if (mode == 2) return result.state == background::State::unsupported ? 0 : 22;
        if (mode == 3) return result.state == background::State::unknown ? 0 : 22;
        return result.state == (config.drop ? background::State::dropped : background::State::probed)
            && result.memory_valid && result.over_hr == S_FALSE &&
            (config.drop || result.query_hr == E_PENDING) ? 0 : 22;
    }
    if (FAILED(OleInitialize(nullptr))) return 23;
    HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSW cls{}; cls.hInstance = instance; cls.lpfnWndProc = window_proc;
    cls.lpszClassName = L"ALXBackgroundOwnHostTest";
    RegisterClassW(&cls);
    // Intentionally never shown or activated: the hook must work without foreground input.
    HWND hwnd = CreateWindowW(cls.lpszClassName, L"ALX background test", WS_OVERLAPPED,
                              0, 0, 100, 100, nullptr, nullptr, instance, nullptr);
    Target target;
    if (!hwnd || FAILED(RegisterDragDrop(hwnd, &target))) return 24;
    if (GetPropW(hwnd, L"OleDropTargetInterface") != static_cast<IDropTarget*>(&target)) {
        std::puts("Unsupported: Windows OLE property differs from registered pointer");
        RevokeDragDrop(hwnd); DestroyWindow(hwnd); OleUninitialize(); return 77;
    }
    wchar_t temporary[MAX_PATH]{}; GetTempPathW(MAX_PATH, temporary);
    expected_file = (std::filesystem::path(temporary) /
        (L"ALX-background-own-host-" + std::to_wstring(GetCurrentProcessId()) + L".wav")).wstring();
    HANDLE file = CreateFileW(expected_file.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                              FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (file == INVALID_HANDLE_VALUE) return 25;
    DWORD written = 0; WriteFile(file, "own-host-test", 13, &written, nullptr); CloseHandle(file);
    POINT before{}; GetCursorPos(&before);
    HWND foreground = GetForegroundWindow(), capture = GetCapture();
    int probe = child(hwnd, 0, expected_file);
    bool probe_only = enters == 0 && overs == 0 && drops == 0;
    int mutation = child(hwnd, 1, expected_file);
    POINT after{}; GetCursorPos(&after);
    // A person may continue using the computer during this test. State changes
    // are observations, not failures or reasons to restore their input state.
    bool untouched = before.x == after.x && before.y == after.y &&
        foreground == GetForegroundWindow() && capture == GetCapture();
    std::printf("input before=(%ld,%ld,%llu,%llu) after=(%ld,%ld,%llu,%llu)\n",
        before.x, before.y, static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(foreground)),
        static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(capture)), after.x, after.y,
        static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(GetForegroundWindow())),
        static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(GetCapture())));
    RevokeDragDrop(hwnd);
    int unsupported = child(hwnd, 2, expected_file);
    int timeout = child(hwnd, 3, expected_file);
    bool okay = !probe && !mutation && !unsupported && !timeout && probe_only &&
        enters == 1 && overs == 0 && drops == 1 && correct_data;
    std::printf("own_host: probe=%d drop=%d enters=%d overs=%d drops=%d data=%d input_unchanged=%d\n",
                probe, mutation, enters, overs, drops, correct_data, untouched);
    std::printf("own_host: unsupported=%d timeout_unknown=%d\n", unsupported, timeout);
    DeleteFileW(expected_file.c_str()); DestroyWindow(hwnd); OleUninitialize();
    return okay ? 0 : 26;
}
