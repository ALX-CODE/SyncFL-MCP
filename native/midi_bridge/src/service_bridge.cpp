#include <windows.h>
#include <mmsystem.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Devices.Midi2.h>
#include <winrt/Windows.Devices.Midi2.Transports.Loopback.h>
#include <winrt/Windows.Devices.Midi2.Enumeration.h>
#include <winrt/Windows.Data.Json.h>
#include <iostream>
#include <string>

using namespace winrt::Windows::Devices::Midi2;
using namespace winrt::Windows::Devices::Midi2::Transports::Loopback;
constexpr wchar_t kNameA[] = L"ALX FL Bridge (Send)";
constexpr wchar_t kNameB[] = L"ALX FL Bridge (FL Studio)";
constexpr wchar_t kUnique[] = L"ALXFLMCPBRIDGEV1";
constexpr wchar_t kIdA[] = L"\\\\?\\swd#midisrv#midiu_loop_a_alxflmcpbridgev1#{e7cce071-3c03-423f-88d3-f1045d02552b}";
constexpr wchar_t kIdB[] = L"\\\\?\\swd#midisrv#midiu_loop_b_alxflmcpbridgev1#{e7cce071-3c03-423f-88d3-f1045d02552b}";

void Print(const MidiLoopbackEntry& entry, bool available, bool created, bool removed, bool json) {
    if (json) {
        using namespace winrt::Windows::Data::Json;
        JsonObject value;
        value.Insert(L"transport_available", JsonValue::CreateBooleanValue(available));
        value.Insert(L"present", JsonValue::CreateBooleanValue(entry && !removed));
        value.Insert(L"created", JsonValue::CreateBooleanValue(created));
        value.Insert(L"removed", JsonValue::CreateBooleanValue(removed));
        if (entry) {
            value.Insert(L"association_id", JsonValue::CreateStringValue(winrt::to_hstring(entry.AssociationId())));
            value.Insert(L"output_name", JsonValue::CreateStringValue(entry.EndpointA().Name()));
            value.Insert(L"output_id", JsonValue::CreateStringValue(entry.EndpointA().EndpointDeviceId()));
            value.Insert(L"input_name", JsonValue::CreateStringValue(entry.EndpointB().Name()));
            value.Insert(L"input_id", JsonValue::CreateStringValue(entry.EndpointB().EndpointDeviceId()));
        }
        std::cout << winrt::to_string(value.Stringify()) << '\n'; return;
    }
    std::wcout << L"transport_available=" << (available ? L"true" : L"false") << L'\n'
               << L"present=" << (entry && !removed ? L"true" : L"false") << L'\n';
    if (created) std::wcout << L"created=true\n";
    if (removed) std::wcout << L"removed=true\n";
    if (!entry) return;
    std::wcout << L"association=" << winrt::to_hstring(entry.AssociationId()).c_str() << L'\n'
               << L"send_name=" << entry.EndpointA().Name().c_str() << L'\n'
               << L"send_id=" << entry.EndpointA().EndpointDeviceId().c_str() << L'\n'
               << L"input_name=" << entry.EndpointB().Name().c_str() << L'\n'
               << L"input_id=" << entry.EndpointB().EndpointDeviceId().c_str() << L'\n';
}
int wmain(int argc, wchar_t** argv) {
    try {
        winrt::init_apartment();
        const std::wstring command = argc >= 2 ? argv[1] : L"status";
        const bool json = argc == 3 && std::wstring(argv[2]) == L"--json";
        if (argc > 3 || (argc == 3 && !json) || (command != L"status" && command != L"ensure" && command != L"remove")) {
            std::wcerr << L"Usage: alx-midi-service [status|ensure|remove] [--json]\n"; return 2;
        }
        // Serialize management only; data does not pass through this process.
        HANDLE mutex = CreateMutexW(nullptr, FALSE, L"Local\\ALXFLMCPBridgeManagerV1");
        if (!mutex) throw std::runtime_error("Cannot open management mutex");
        struct Lock { HANDLE handle; ~Lock() { ReleaseMutex(handle); CloseHandle(handle); } } lock{mutex};
        DWORD waited = WaitForSingleObject(mutex, 10000);
        if (waited != WAIT_OBJECT_0 && waited != WAIT_ABANDONED) throw std::runtime_error("Another bridge manager is busy");
        const bool available = MidiLoopbackManager::IsTransportAvailable();
        if (!available) { Print(nullptr, false, false, false, json); return 3; }
        MidiLoopbackEntry owned{nullptr};
        for (const auto& entry : MidiLoopbackManager::GetActiveLoopbackEntries()) {
            const std::wstring a = entry.EndpointA().EndpointDeviceId().c_str();
            const std::wstring b = entry.EndpointB().EndpointDeviceId().c_str();
            auto lowercase = [](std::wstring value) { for (auto& ch : value) ch = static_cast<wchar_t>(towlower(ch)); return value; };
            if (lowercase(a) == kIdA && lowercase(b) == kIdB) {
                if (owned) throw std::runtime_error("Multiple ALX endpoint pairs found; refusing mutation");
                if (entry.EndpointA().Name() != kNameA || entry.EndpointB().Name() != kNameB)
                    throw std::runtime_error("ALX identity exists with different names; refusing mutation");
                owned = entry;
            }
        }
        if (command == L"remove") {
            if (!owned) { Print(nullptr, true, false, false, json); return 0; }
            auto response = MidiLoopbackManager::RemoveTransientLoopback(MidiLoopbackRemovalConfig(owned.AssociationId()));
            if (!response || !response.Success()) throw std::runtime_error("Owned endpoint removal failed");
            Print(owned, true, false, true, json); return 0;
        }
        bool created = false;
        if (!owned && command == L"ensure") {
            MidiLoopbackEndpointDefinition a, b;
            a.Name(kNameA); a.UniqueId(kUnique); a.Description(L"ALX FL Studio MCP command wakeup sender");
            b.Name(kNameB); b.UniqueId(kUnique); b.Description(L"ALX FL Studio MCP controller input");
            auto response = MidiLoopbackManager::CreateTransientLoopback(MidiLoopbackCreationConfig(a, b));
            if (!response || !response.Success()) {
                if (response) std::wcerr << L"error_code=" << static_cast<unsigned>(response.ErrorCode()) << L"\nerror=" << response.ErrorMessage().c_str() << L'\n';
                return 4;
            }
            owned = response.CreatedLoopbackEntry();
            created = true;
        }
        Print(owned, true, created, false, json);
        return owned || command == L"status" ? 0 : 4;
    } catch (const winrt::hresult_error& error) {
        std::wcerr << L"hresult=0x" << std::hex << static_cast<unsigned>(error.code()) << L"\nerror=" << error.message().c_str() << L'\n'; return 5;
    } catch (const std::exception& error) {
        std::cerr << "error=" << error.what() << '\n'; return 6;
    }
}
