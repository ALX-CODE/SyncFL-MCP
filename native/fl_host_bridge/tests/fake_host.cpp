#include "ipc.hpp"
#include <fp_plugclass.h>
#include <future>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
class FakeHost final : public TFruityPlugHost {
public:
    FakeHost() { HostVersion = 26000000; }
    int wanted_idle{};
    int forbidden_calls{};
    intptr_t __stdcall Dispatcher(TPluginTag, intptr_t id, intptr_t index, intptr_t value) override {
        if (id == FHD_WantIdle && index == 0 && value == 2) { ++wanted_idle; return 1; }
        ++forbidden_calls;
        return 0;
    }
    void __stdcall OnParamChanged(TPluginTag, int, int) override { ++forbidden_calls; }
    void __stdcall OnHint(TPluginTag, char*) override { ++forbidden_calls; }
    void __stdcall ComputeLRVol_Old(float&, float&, int, float) override { ++forbidden_calls; }
    void __stdcall Voice_Release(intptr_t) override { ++forbidden_calls; }
    void __stdcall Voice_Kill(intptr_t, BOOL) override { ++forbidden_calls; }
    int __stdcall Voice_ProcessEvent(intptr_t, intptr_t, intptr_t, intptr_t) override { return bad(); }
    void __stdcall LockMix_Old() override { ++forbidden_calls; }
    void __stdcall UnlockMix_Old() override { ++forbidden_calls; }
    void __stdcall MIDIOut_Delayed(TPluginTag, intptr_t) override { ++forbidden_calls; }
    void __stdcall MIDIOut(TPluginTag, intptr_t) override { ++forbidden_calls; }
    void __stdcall AddWave_32FM_32FS_Ramp(void*, void*, int, float, float, float&, float&) override {
        ++forbidden_calls;
    }
    void __stdcall AddWave_32FS_32FS_Ramp(void*, void*, int, float, float, float&, float&) override {
        ++forbidden_calls;
    }
    bool __stdcall LoadSample(TSampleHandle&, char*, PWaveFormatExtensible, int) override {
        ++forbidden_calls; return false;
    }
    void* __stdcall GetSampleData(TSampleHandle, int&) override { ++forbidden_calls; return nullptr; }
    void __stdcall CloseSample(TSampleHandle) override { ++forbidden_calls; }
    int __stdcall GetSongMixingTime() override { return bad(); }
    double __stdcall GetSongMixingTime_A() override { return bad(); }
    double __stdcall GetSongPlayingTime() override { return bad(); }
    void __stdcall OnControllerChanged(TPluginTag, intptr_t, intptr_t) override { ++forbidden_calls; }
    void* __stdcall GetSendBuffer(intptr_t) override { ++forbidden_calls; return nullptr; }
    void __stdcall PlugMsg_Delayed(TPluginTag, intptr_t) override { ++forbidden_calls; }
    void __stdcall PlugMsg_Kill(TPluginTag, intptr_t) override { ++forbidden_calls; }
    void __stdcall GetSampleInfo(TSampleHandle, PSampleInfo) override { ++forbidden_calls; }
    void __stdcall DistWave_32FM(int, int, void*, int, float, float, float) override {
        ++forbidden_calls;
    }
    void* __stdcall GetMixBuffer(int) override { ++forbidden_calls; return nullptr; }
    void* __stdcall GetInsBuffer(TPluginTag, int) override { ++forbidden_calls; return nullptr; }
    BOOL __stdcall PromptEdit(int, int, char*, char*, int&) override { return bad(); }
    void __stdcall SuspendOutput_Old() override { ++forbidden_calls; }
    void __stdcall ResumeOutput_Old() override { ++forbidden_calls; }
    void __stdcall GetSampleRegion(TSampleHandle, int, PSampleRegion) override { ++forbidden_calls; }
    void __stdcall ComputeLRVol(float&, float&, float, float) override { ++forbidden_calls; }
    void __stdcall LockPlugin(TPluginTag) override { ++forbidden_calls; }
    void __stdcall UnlockPlugin(TPluginTag) override { ++forbidden_calls; }
    void __stdcall LockMix_Shared_Old() override { ++forbidden_calls; }
    void __stdcall UnlockMix_Shared_Old() override { ++forbidden_calls; }
    void __stdcall GetInBuffer(TPluginTag, intptr_t, PIOBuffer) override { ++forbidden_calls; }
    void __stdcall GetOutBuffer(TPluginTag, intptr_t, PIOBuffer) override { ++forbidden_calls; }
    TOutVoiceHandle __stdcall TriggerOutputVoice(TVoiceParams*, intptr_t, intptr_t) override {
        return bad();
    }
    void __stdcall OutputVoice_Release(TOutVoiceHandle) override { ++forbidden_calls; }
    void __stdcall OutputVoice_Kill(TOutVoiceHandle) override { ++forbidden_calls; }
    int __stdcall OutputVoice_ProcessEvent(TOutVoiceHandle, intptr_t, intptr_t, intptr_t) override {
        return bad();
    }
    BOOL __stdcall PromptEdit_Ex(int, int, const char*, char*, int&, int&, int&, int, int) override {
        return bad();
    }
    void __stdcall SuspendOutput(TPluginTag) override { ++forbidden_calls; }
    void __stdcall ResumeOutput(TPluginTag) override { ++forbidden_calls; }
private:
    int bad() { ++forbidden_calls; return 0; }
};

alx::Request request(alx::Operation operation) {
    alx::Request result;
    result.operation = operation;
    result.id = alx::random_id();
    result.expires_unix_ms = alx::now_ms() + 5000;
    return result;
}
alx::Response roundtrip(TFruityPlug* plugin, const alx::Request& input) {
    auto pending = std::async(std::launch::async, [&] { return alx::call(GetCurrentProcessId(), input); });
    const auto deadline = GetTickCount64() + 5000;
    while (pending.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready) {
        require(GetTickCount64() < deadline, "Fake GUI callback deadline");
        plugin->Idle_Public();
        Sleep(1);
    }
    return pending.get();
}
void cli_roundtrip(TFruityPlug* plugin, const std::wstring& exe) {
    std::wstring command = L"\"" + exe + L"\" --pid " + std::to_wstring(GetCurrentProcessId()) + L" ping";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    require(CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                           nullptr, nullptr, &startup, &child) != FALSE, "Create CLI process");
    alx::Handle process(child.hProcess), thread(child.hThread);
    const auto deadline = GetTickCount64() + 5000;
    while (WaitForSingleObject(process.get(), 0) == WAIT_TIMEOUT) {
        require(GetTickCount64() < deadline, "CLI deadline");
        plugin->Idle_Public();
        Sleep(1);
    }
    DWORD exit_code = 1;
    require(GetExitCodeProcess(process.get(), &exit_code) && exit_code == 0, "CLI successful ping");
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        require(argc == 3, "Pass plugin DLL and CLI paths");
        HMODULE module = LoadLibraryExW(argv[1], nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        require(module != nullptr, "Load original plugin");
        using Factory = TFruityPlug* (__stdcall*)(TFruityPlugHost*, intptr_t);
        const auto factory = reinterpret_cast<Factory>(GetProcAddress(module, "CreatePlugInstance"));
        require(factory != nullptr && factory(nullptr, 0) == nullptr, "Factory validates host");
        FakeHost host;
        TFruityPlug* plugin = factory(&host, 1234);
        require(plugin && plugin->Info && plugin->HostTag == 1234, "SDK instance initialized");
        require(host.wanted_idle == 1, "Always-hidden idle requested exactly once");
        require(plugin->Info->Flags & FPF_NoProcess, "No processing required");
        require(!(plugin->Info->Flags & FPF_CanStealKBFocus), "No keyboard focus flags");
        auto* duplicate = factory(&host, 5678);
        require(duplicate != nullptr && host.wanted_idle == 1,
                "Duplicate plugin does not replace an owned endpoint");
        duplicate->DestroyObject();
        auto ping = roundtrip(plugin, request(alx::Operation::ping));
        require(ping.status == alx::Status::ok && ping.instance && ping.generation == 1,
                "Correlated native IPC ping");
        require(ping.gui_thread == GetCurrentThreadId() && ping.notes_enabled == 0,
                "GUI-thread scheduling and notes remain disabled");
        TTimeSigInfo signature{16, 4, 96};
        plugin->Dispatcher(FPD_SetTimeSig, 0, reinterpret_cast<intptr_t>(&signature));
        plugin->Dispatcher(FPD_SetPlaying, 0, 0);
        auto info = roundtrip(plugin, request(alx::Operation::project_info));
        require(info.ppq == 96 && info.playing == 0 && info.host_version == host.HostVersion,
                "Callback-derived project metadata");
        auto notes = request(alx::Operation::notes_preview);
        notes.expected_instance = ping.instance;
        notes.expected_generation = ping.generation;
        notes.channel = 0;
        notes.pattern = 1;
        notes.count = 1;
        notes.notes[0] = {0, 96, 60, 800};
        require(roundtrip(plugin, notes).status == alx::Status::unsupported_abi,
                "Valid note request is fail-closed");
        notes.channel = -1;
        require(roundtrip(plugin, notes).status == alx::Status::invalid, "Implicit target rejected");
        notes.channel = 0;
        notes.notes[0].length_ticks = 0;
        require(roundtrip(plugin, notes).status == alx::Status::invalid, "Zero length rejected");
        notes.notes[0].length_ticks = 96;
        notes.notes[0].position_ticks = std::numeric_limits<std::int32_t>::max();
        require(roundtrip(plugin, notes).status == alx::Status::invalid, "Tick overflow rejected");
        notes.notes[0].position_ticks = 0;
        notes.notes[0].velocity_permille = 1001;
        require(roundtrip(plugin, notes).status == alx::Status::invalid, "Velocity overflow rejected");
        notes.notes[0].velocity_permille = 800;
        ++notes.expected_instance;
        require(roundtrip(plugin, notes).status == alx::Status::stale, "Wrong instance rejected");
        notes.expected_instance = ping.instance;
        notes.replace = 1;
        require(roundtrip(plugin, notes).status == alx::Status::unsupported_abi,
                "Replace never clears notes while ABI is unqualified");
        plugin->Dispatcher(FPD_ProjLoaded, 0, 0);
        require(roundtrip(plugin, notes).status == alx::Status::stale, "Old project guard rejected");
        auto expired = request(alx::Operation::ping);
        expired.expires_unix_ms = alx::now_ms() - 1;
        require(roundtrip(plugin, expired).status == alx::Status::expired, "Expired request rejected");
        auto invalid = request(static_cast<alx::Operation>(999));
        require(roundtrip(plugin, invalid).status == alx::Status::invalid, "Unknown operation rejected");
        invalid = request(alx::Operation::ping);
        --invalid.bytes;
        require(roundtrip(plugin, invalid).status == alx::Status::invalid, "Wrong declared size rejected");
        invalid = request(alx::Operation::ping);
        invalid.channel = 0;
        require(roundtrip(plugin, invalid).status == alx::Status::invalid, "Read target injection rejected");
        cli_roundtrip(plugin, argv[2]);
        require(host.forbidden_calls == 0, "No note, string, UI, render or arbitrary host calls");

        // Simulate an incorrectly scheduled idle callback on another thread.
        const auto wrong_input = request(alx::Operation::ping);
        auto wrong_result = std::async(std::launch::async, [&] {
            return alx::call(GetCurrentProcessId(), wrong_input);
        });
        std::thread wrong_gui([&] {
            const auto deadline = GetTickCount64() + 5000;
            while (wrong_result.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready
                   && GetTickCount64() < deadline) {
                plugin->Idle_Public();
                Sleep(1);
            }
        });
        wrong_gui.join();
        require(wrong_result.get().status == alx::Status::wrong_thread,
                "Incorrect callback thread fails closed");
        const auto before = GetTickCount64();
        plugin->DestroyObject();
        require(GetTickCount64() - before < 1000, "Unloaded while pipe worker awaits a client");

        // An unload must also cancel a client whose request never reaches idle.
        plugin = factory(&host, 9012);
        require(plugin != nullptr && host.wanted_idle == 2, "Endpoint can restart after unload");
        const auto queued_input = request(alx::Operation::ping);
        auto queued_client = std::async(std::launch::async, [&] {
            try { (void)alx::call(GetCurrentProcessId(), queued_input); return false; }
            catch (const std::exception&) { return true; }
        });
        Sleep(30);
        const auto queued_before = GetTickCount64();
        plugin->DestroyObject();
        require(GetTickCount64() - queued_before < 1000 && queued_client.get(),
                "Unloaded with an outstanding client without late work");
        FreeLibrary(module);
        std::cout << "PASS: SDK construction, hidden idle, IPC, process identity, expiry, metadata, "
                     "CLI child, fail-closed notes, clean unload; no real FL loaded\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
