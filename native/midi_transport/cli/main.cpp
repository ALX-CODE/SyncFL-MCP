// syncfl-midi: diagnostic CLI over the transport DLL's exported C ABI.
//
//   syncfl-midi version
//   syncfl-midi list [--json]
//   syncfl-midi resolve <name> [--allow-decimal-suffix] [--json]
//   syncfl-midi wakeup <name> --confirm-send [--allow-decimal-suffix] [--json]
//
// Exit codes: 0 success, 1..12 the SYNCFL_MIDI_* status of the failing call,
// 64 usage error. Only `wakeup --confirm-send` opens an endpoint or sends MIDI.

#include "syncfl_midi_transport.h"

#include <windows.h>

#include <cstdio>
#include <string>
#include <string_view>
#include <vector>

#ifndef SYNCFL_MIDI_VERSION
#define SYNCFL_MIDI_VERSION "0.0.0"
#endif

namespace {

constexpr int kUsageExit = 64;

// Consoles get UTF-16 via WriteConsoleW (the console code page is left alone);
// pipes and files get UTF-8 bytes.
void Write(DWORD stream, std::string_view utf8) {
    const HANDLE handle = GetStdHandle(stream);
    if (handle == nullptr || handle == INVALID_HANDLE_VALUE || utf8.empty()) return;
    DWORD mode = 0;
    DWORD written = 0;
    if (GetConsoleMode(handle, &mode)) {
        const int units = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()),
                                              nullptr, 0);
        std::wstring wide(static_cast<size_t>(units), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), wide.data(),
                            units);
        WriteConsoleW(handle, wide.data(), static_cast<DWORD>(wide.size()), &written, nullptr);
    } else {
        WriteFile(handle, utf8.data(), static_cast<DWORD>(utf8.size()), &written, nullptr);
    }
}
void Out(std::string_view text) { Write(STD_OUTPUT_HANDLE, text); }
void Err(std::string_view text) { Write(STD_ERROR_HANDLE, text); }

bool ToUtf8(const wchar_t* text, std::string& out) {
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text, -1, nullptr, 0,
                                         nullptr, nullptr);
    if (size <= 0) return false;
    out.assign(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text, -1, out.data(), size, nullptr,
                        nullptr);
    out.pop_back();  // drop the terminator
    return true;
}

std::string Json(std::string_view text) {
    std::string out = "\"";
    for (const char ch : text) {
        const auto byte = static_cast<unsigned char>(ch);
        if (ch == '"' || ch == '\\') {
            out.push_back('\\');
            out.push_back(ch);
        } else if (byte < 0x20) {
            char escaped[8];
            std::snprintf(escaped, sizeof(escaped), "\\u%04x", byte);
            out += escaped;
        } else {
            out.push_back(ch);
        }
    }
    return out + "\"";
}

struct Failure {
    int32_t status;
    uint32_t native;
    std::string message;
};

// Must run on the failing call's thread, before any other transport call.
Failure Capture(int32_t status) {
    Failure failure{status, 0, {}};
    size_t required = 0;
    syncfl_midi_last_error(&failure.native, nullptr, 0, &required);
    std::string message(required, '\0');
    if (syncfl_midi_last_error(&failure.native, message.data(), message.size(), &required) ==
        SYNCFL_MIDI_OK)
        failure.message.assign(message.c_str());
    return failure;
}

int Report(const char* operation, const Failure& failure, bool json, bool attempted) {
    if (json) {
        Out(std::string("{\"ok\":false,\"operation\":") + Json(operation) +
            ",\"status\":" + Json(syncfl_midi_status_name(failure.status)) +
            ",\"message\":" + Json(failure.message) +
            ",\"native_code\":" + std::to_string(failure.native) +
            ",\"delivery_attempted\":" + (attempted ? "true" : "false") + "}\n");
    }
    std::string line = std::string("syncfl-midi: ") + operation + " failed: " +
                       syncfl_midi_status_name(failure.status) + ": " + failure.message;
    if (failure.native) line += " (MMRESULT " + std::to_string(failure.native) + ")";
    if (attempted) line += " [the wakeup call was made; do not retry blindly]";
    Err(line + "\n");
    return failure.status;
}

int Usage(const std::string& problem) {
    if (!problem.empty()) Err("syncfl-midi: " + problem + "\n");
    Err("usage:\n"
        "  syncfl-midi version\n"
        "  syncfl-midi list [--json]\n"
        "  syncfl-midi resolve <name> [--allow-decimal-suffix] [--json]\n"
        "  syncfl-midi wakeup <name> --confirm-send [--allow-decimal-suffix] [--json]\n"
        "Names match exactly (case-sensitive, no fuzzy matching). wakeup opens the one\n"
        "matching output and sends a single note-on (channel 0, note 127, velocity 127).\n");
    return kUsageExit;
}

int Version() {
    const uint32_t abi = syncfl_midi_abi_version();
    Out("syncfl-midi " SYNCFL_MIDI_VERSION " (DLL ABI " + std::to_string(abi >> 16) + "." +
        std::to_string(abi & 0xFFFF) + ")\n");
    if ((abi >> 16) != SYNCFL_MIDI_ABI_VERSION_MAJOR) {
        Err("syncfl-midi: incompatible transport DLL ABI\n");
        return SYNCFL_MIDI_E_INTERNAL;
    }
    return 0;
}

int List(bool json) {
    for (int attempt = 0; attempt < 4; ++attempt) {
        size_t required = 0;
        uint32_t count = 0;
        int32_t status = syncfl_midi_list_outputs(nullptr, 0, &required, &count);
        if (status != SYNCFL_MIDI_E_BUFFER_TOO_SMALL) return Report("list", Capture(status), json, false);
        std::vector<char> buffer(required);
        status = syncfl_midi_list_outputs(buffer.data(), buffer.size(), &required, &count);
        if (status == SYNCFL_MIDI_E_BUFFER_TOO_SMALL) continue;  // devices changed in between
        if (status != SYNCFL_MIDI_OK) return Report("list", Capture(status), json, false);
        std::vector<std::string> names;
        for (const char* cursor = buffer.data(); names.size() < count; cursor += names.back().size() + 1)
            names.emplace_back(cursor);
        if (json) {
            std::string text = "{\"ok\":true,\"outputs\":[";
            for (size_t i = 0; i < names.size(); ++i) text += (i ? "," : "") + Json(names[i]);
            Out(text + "],\"count\":" + std::to_string(names.size()) + "}\n");
        } else {
            for (const auto& name : names) Out(name + "\n");
            Out(std::to_string(names.size()) + " WinMM MIDI output(s)\n");
        }
        return 0;
    }
    return Report("list", Failure{SYNCFL_MIDI_E_DEVICE_SET_CHANGED, 0, "the device set kept changing"},
                  json, false);
}

int Resolve(const std::string& name, uint32_t flags, bool json) {
    size_t required = 0;
    int32_t status = syncfl_midi_resolve_output(name.c_str(), flags, nullptr, 0, &required);
    std::string matched;
    if (status == SYNCFL_MIDI_E_BUFFER_TOO_SMALL) {
        matched.assign(required, '\0');
        status = syncfl_midi_resolve_output(name.c_str(), flags, matched.data(), matched.size(),
                                            &required);
        matched.resize(required ? required - 1 : 0);
    }
    if (status != SYNCFL_MIDI_OK) return Report("resolve", Capture(status), json, false);
    if (json)
        Out("{\"ok\":true,\"requested\":" + Json(name) + ",\"matched\":" + Json(matched) + "}\n");
    else
        Out("resolves to exactly one output: " + matched + "\n");
    return 0;
}

int Wakeup(const std::string& name, uint32_t flags, bool json) {
    syncfl_midi_session session = 0;
    int32_t status = syncfl_midi_open_output(name.c_str(), flags, &session);
    if (status != SYNCFL_MIDI_OK) return Report("open", Capture(status), json, false);

    size_t required = 0;
    std::string opened;
    if (syncfl_midi_session_name(session, nullptr, 0, &required) == SYNCFL_MIDI_E_BUFFER_TOO_SMALL) {
        opened.assign(required, '\0');
        if (syncfl_midi_session_name(session, opened.data(), opened.size(), &required) == SYNCFL_MIDI_OK)
            opened.resize(required - 1);
        else
            opened.clear();
    }

    uint32_t attempted = 0;
    status = syncfl_midi_send_wakeup(session, &attempted);
    const Failure send_failure = status == SYNCFL_MIDI_OK ? Failure{} : Capture(status);
    const int32_t closed = syncfl_midi_close(session);
    const Failure close_failure = closed == SYNCFL_MIDI_OK ? Failure{} : Capture(closed);
    if (status != SYNCFL_MIDI_OK) return Report("send", send_failure, json, attempted != 0);

    if (json)
        Out("{\"ok\":true,\"endpoint\":" + Json(opened) +
            ",\"message\":\"0x90 0x7f 0x7f\",\"delivery_attempted\":true" +
            ",\"closed\":" + (closed == SYNCFL_MIDI_OK ? "true" : "false") + "}\n");
    else
        Out("sent one wakeup (90 7F 7F) to " + opened +
            "\nWinMM accepted it; this does not prove FL Studio executed anything.\n");
    if (closed != SYNCFL_MIDI_OK) return Report("close", close_failure, false, true);
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    std::vector<std::string> args;
    for (int i = 1; i < argc; ++i) {
        std::string arg;
        if (!ToUtf8(argv[i], arg)) return Usage("argument " + std::to_string(i) + " is not valid Unicode");
        args.push_back(std::move(arg));
    }
    if (args.empty() || args[0] == "help" || args[0] == "--help" || args[0] == "-h") {
        if (args.empty()) return Usage("missing command");
        Usage("");
        return 0;
    }

    const std::string command = args[0];
    std::string name;
    bool json = false, confirm = false, have_name = false;
    uint32_t flags = SYNCFL_MIDI_OPEN_EXACT;
    for (size_t i = 1; i < args.size(); ++i) {
        const std::string& arg = args[i];
        if (arg == "--json") {
            json = true;
        } else if (arg == "--allow-decimal-suffix") {
            flags |= SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX;
        } else if (arg == "--confirm-send") {
            confirm = true;
        } else if (arg.rfind("--", 0) == 0) {
            return Usage("unknown option " + arg);
        } else if (!have_name) {
            name = arg;
            have_name = true;
        } else {
            return Usage("unexpected argument " + arg);
        }
    }

    const bool takes_name = command == "resolve" || command == "wakeup";
    if (command != "version" && command != "list" && !takes_name)
        return Usage("unknown command " + command);
    if (takes_name != have_name) return Usage(takes_name ? "missing <name>" : "unexpected <name>");
    if ((flags != SYNCFL_MIDI_OPEN_EXACT || confirm) && !takes_name)
        return Usage("option not valid for " + command);
    if (confirm && command != "wakeup") return Usage("--confirm-send is only valid for wakeup");

    if (command == "version") return Version();
    if (command == "list") return List(json);
    if (command == "resolve") return Resolve(name, flags, json);
    if (!confirm)
        return Usage("wakeup sends MIDI to a live endpoint; pass --confirm-send to proceed");
    return Wakeup(name, flags, json);
}
