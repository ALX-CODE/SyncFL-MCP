#include <windows.h>
#include <mmsystem.h>
#include <objbase.h>
#include <atomic>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>
#include <algorithm>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Devices.Midi2.h>
#include <winrt/Windows.Devices.Midi2.Transports.Loopback.h>

using namespace winrt::Windows::Devices::Midi2::Transports::Loopback;

void Require(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
struct Received { HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr); std::atomic<DWORD> value{0}; std::atomic<unsigned> count{0}; ~Received() { CloseHandle(event); } };
void CALLBACK Receive(HMIDIIN, UINT type, DWORD_PTR instance, DWORD_PTR value, DWORD_PTR) {
    if (type != MIM_DATA) return;
    auto& data = *reinterpret_cast<Received*>(instance);
    data.value = static_cast<DWORD>(value); ++data.count; SetEvent(data.event);
}
UINT InputId(const wchar_t* name) {
    UINT found = UINT_MAX;
    for (UINT i = 0; i < midiInGetNumDevs(); ++i) {
        MIDIINCAPSW caps{}; Require(midiInGetDevCapsW(i, &caps, sizeof(caps)) == 0, "input caps failed");
        if (std::wstring(caps.szPname) == name) { Require(found == UINT_MAX, "ambiguous input name"); found = i; }
    }
    Require(found != UINT_MAX, "input not found"); return found;
}
UINT OutputId(const wchar_t* name) {
    UINT found = UINT_MAX;
    for (UINT i = 0; i < midiOutGetNumDevs(); ++i) {
        MIDIOUTCAPSW caps{}; Require(midiOutGetDevCapsW(i, &caps, sizeof(caps)) == 0, "output caps failed");
        if (std::wstring(caps.szPname) == name) { Require(found == UINT_MAX, "ambiguous output name"); found = i; }
    }
    Require(found != UINT_MAX, "output not found"); return found;
}
int wmain(int argc, wchar_t** argv) {
    HMIDIIN input{}; HMIDIOUT output{};
    MidiLoopbackEntry created{nullptr};
    Received data;
    try {
        if (argc == 3 && std::wstring(argv[1]) == L"--send") {
            Require(midiOutOpen(&output, OutputId(argv[2]), 0, 0, CALLBACK_NULL) == 0, "child output open failed");
            Require(midiOutShortMsg(output, 0x007f7f90) == 0, "child trigger send failed");
            Require(midiOutClose(output) == 0, "child output close failed"); return 0;
        }
        winrt::init_apartment();
        if (!MidiLoopbackManager::IsTransportAvailable()) { std::printf("SKIP: Windows MIDI Services loopback transport is unavailable.\n"); return 77; }
        GUID guid{}; Require(CoCreateGuid(&guid) == S_OK, "test identity failed");
        std::wstring identity = winrt::to_hstring(guid).c_str();
        identity.erase(std::remove_if(identity.begin(), identity.end(), [](wchar_t ch) { return ch == L'{' || ch == L'}' || ch == L'-'; }), identity.end());
        const std::wstring outputName = L"ALXTest Send " + identity.substr(0, 8);
        const std::wstring inputName = L"ALXTest In " + identity.substr(0, 8);
        MidiLoopbackEndpointDefinition a, b;
        a.Name(outputName); a.UniqueId(identity); b.Name(inputName); b.UniqueId(identity);
        auto response = MidiLoopbackManager::CreateTransientLoopback(MidiLoopbackCreationConfig(a, b));
        Require(response && response.Success(), "test endpoint creation failed");
        created = response.CreatedLoopbackEntry();
        Require(midiInOpen(&input, InputId(inputName.c_str()), reinterpret_cast<DWORD_PTR>(&Receive), reinterpret_cast<DWORD_PTR>(&data), CALLBACK_FUNCTION) == 0, "input open failed");
        Require(midiInStart(input) == 0, "input start failed");
        wchar_t executable[32768]; Require(GetModuleFileNameW(nullptr, executable, 32768) != 0, "path failed");
        std::wstring command = L"\"" + std::wstring(executable) + L"\" --send \"" + outputName + L"\"";
        STARTUPINFOW startup{sizeof(startup)}; PROCESS_INFORMATION process{};
        Require(CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process) != FALSE, "child failed");
        const DWORD wait = WaitForSingleObject(process.hProcess, 5000);
        DWORD exitCode = 1; GetExitCodeProcess(process.hProcess, &exitCode);
        if (wait != WAIT_OBJECT_0) TerminateProcess(process.hProcess, 99);
        CloseHandle(process.hProcess); CloseHandle(process.hThread);
        Require(wait == WAIT_OBJECT_0 && exitCode == 0, "sender failed/timed out");
        Require(WaitForSingleObject(data.event, 5000) == WAIT_OBJECT_0, "trigger did not arrive");
        Require(midiInStop(input) == 0 && midiInReset(input) == 0, "input stop/reset failed");
        Require(midiInClose(input) == 0, "input close failed"); input = nullptr;
        Require(data.count == 1 && data.value == 0x007f7f90, "payload changed or duplicated");
        auto removal = MidiLoopbackManager::RemoveTransientLoopback(MidiLoopbackRemovalConfig(created.AssociationId()));
        Require(removal && removal.Success(), "test endpoint cleanup failed");
        created = nullptr;
        std::printf("PASS: actual Windows MIDI Services WinMM cross-process trigger 0x007f7f90 received exactly once.\n"); return 0;
    } catch (...) {
        if (input) { midiInStop(input); midiInReset(input); midiInClose(input); }
        if (output) midiOutClose(output);
        if (created) MidiLoopbackManager::RemoveTransientLoopback(MidiLoopbackRemovalConfig(created.AssociationId()));
        try { throw; }
        catch (const winrt::hresult_error& error) { std::fwprintf(stderr, L"FAIL: %s (0x%08x)\n", error.message().c_str(), static_cast<unsigned>(error.code())); }
        catch (const std::exception& error) { std::fprintf(stderr, "FAIL: %s\n", error.what()); }
        catch (...) { std::fprintf(stderr, "FAIL: unexpected exception\n"); }
        return 1;
    }
}
