#include "ipc.hpp"
#include <fp_plugclass.h>
#include <atomic>
#include <cstring>
#include <memory>

// Supply the small base-class lifetime functions locally. No SDK .cpp or sample
// implementation is linked or copied into this project.
TFruityPlug::TFruityPlug() : HostTag{}, Info{}, EditorHandle{}, MonoRender{}, Reserved{} {}
TFruityPlug::~TFruityPlug() = default;
void __stdcall TFruityPlug::DestroyObject() { delete this; }

static_assert(sizeof(TNoteParams) == 32 && offsetof(TNoteParams, Vol) == 12);
static_assert(offsetof(TNotesParams, NoteParams) == 20 && sizeof(TNotesParams) == 52);
static_assert(std::is_same_v<decltype(TNoteParams::Vol), int>);

namespace {
char long_name[] = "ALX FL Host Bridge";
char short_name[] = "ALX Host";
TFruityPlugInfo information = [] {
    TFruityPlugInfo info{};
    info.SDKVersion = CurrentSDKVersion;
    info.LongName = long_name;
    info.ShortName = short_name;
    info.Flags = FPF_Type_Visual | FPF_NoWindow | FPF_CantSmartDisable;
    return info;
}();

class Bridge final : public TFruityPlug {
public:
    Bridge(TFruityPlugHost* host, TPluginTag tag)
        : host_(host), thread_(GetCurrentThreadId()), instance_(alx::random_id()) {
        HostTag = tag;
        Info = &information;
        // One endpoint per FL process. Duplicate instances remain inert.
        try { server_ = std::make_unique<alx::Server>(); }
        catch (...) { return; }
        host_->Dispatcher(HostTag, FHD_WantIdle, 0, 2);
    }
    intptr_t __stdcall Dispatcher(intptr_t id, intptr_t, intptr_t value) override {
        if (id == FPD_SetPlaying) playing_.store(value ? 1 : 0);
        if (id == FPD_SetTimeSig && value) {
            const auto* sig = reinterpret_cast<const TTimeSigInfo*>(value);
            ppq_.store(sig->PPQ > 0 ? sig->PPQ : 0);
        }
        if (id == FPD_ProjLoaded || id == FPD_ProjectInfoChanged) ++generation_;
        return 0;
    }
    void __stdcall Idle_Public() override {
        if (!server_) return;
        auto job = server_->take();
        if (!job) return;
        auto& result = job->response;
        result.instance = instance_;
        result.generation = generation_.load();
        result.host_version = host_->HostVersion;
        result.ppq = ppq_.load();
        result.playing = playing_.load();
        result.gui_thread = thread_;
        if (GetCurrentThreadId() != thread_) {
            result.status = alx::Status::wrong_thread;
            alx::detail(result, "Host work requires the plugin creation/GUI thread");
        } else if (alx::validate(job->request, result)) {
            if (job->request.operation == alx::Operation::notes_preview) {
                if (job->request.expected_instance != instance_
                    || job->request.expected_generation != result.generation) {
                    result.status = alx::Status::stale;
                    alx::detail(result, "Plugin instance or observed project generation changed");
                } else {
                    result.status = alx::Status::unsupported_abi;
                    alx::detail(result, "Notes disabled: official C++ int Vol conflicts with Delphi float Vol");
                }
            } else {
                result.status = alx::Status::ok;
                alx::detail(result, "Callback metadata only; project text and note-write ABI are unqualified");
            }
        }
        server_->complete(job);
    }
    void __stdcall SaveRestoreState(IStream*, BOOL save) override {
        // This plugin has no persistent user state or saved IPC credentials.
        if (!save) ++generation_;
    }
    void __stdcall GetName(int, int, int, char* name) override { if (name) name[0] = 0; }
    int __stdcall ProcessEvent(int, int, int) override { return 0; }
    int __stdcall ProcessParam(int, int, int) override { return 0; }
    void __stdcall Eff_Render(PWAV32FS source, PWAV32FS destination, int length) override {
        if (source && destination && source != destination && length > 0)
            std::memmove(destination, source, static_cast<std::size_t>(length) * sizeof(TWAV32FS));
    }
    void __stdcall Gen_Render(PWAV32FS, int& length) override { length = 0; }
    TVoiceHandle __stdcall TriggerVoice(PVoiceParams, intptr_t) override { return 0; }
    void __stdcall Voice_Release(TVoiceHandle) override {}
    void __stdcall Voice_Kill(TVoiceHandle) override {}
    int __stdcall Voice_ProcessEvent(TVoiceHandle, intptr_t, intptr_t, intptr_t) override { return 0; }
    int __stdcall Voice_Render(TVoiceHandle, PWAV32FS, int&) override { return 0; }
    void __stdcall NewTick() override {}
    void __stdcall MIDITick() override {}
    void __stdcall MIDIIn(int&) override {}
    void __stdcall MsgIn(intptr_t) override {}
    int __stdcall OutputVoice_ProcessEvent(TOutVoiceHandle, intptr_t, intptr_t, intptr_t) override {
        return 0;
    }
    void __stdcall OutputVoice_Kill(TVoiceHandle) override {}
private:
    TFruityPlugHost* host_;
    DWORD thread_;
    std::uint64_t instance_;
    std::atomic<std::uint64_t> generation_{1};
    std::atomic<std::int32_t> ppq_{0};
    std::atomic<std::int32_t> playing_{-1};
    std::unique_ptr<alx::Server> server_;
};
} // namespace

extern "C" __declspec(dllexport) TFruityPlug* __stdcall
CreatePlugInstance(TFruityPlugHost* host, intptr_t tag) {
    if (!host) return nullptr;
    try { return new Bridge(host, tag); }
    catch (...) { return nullptr; }
}
