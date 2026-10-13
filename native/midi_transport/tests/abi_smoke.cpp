// Smoke test of the built DLL against the real WinMM stack. Safe on a machine
// with a live music session: it enumerates (read-only) and resolves or opens only
// a name that cannot exist, so no endpoint is ever opened and nothing is sent.

#include "mini_test.hpp"
#include "syncfl_midi_transport.h"

#include <windows.h>

#include <cstring>
#include <string>
#include <vector>

namespace {

std::string AbsentName() {
    // <= 31 characters, unique per run, never a real product name.
    return "SyncFL absent " + std::to_string(GetCurrentProcessId()) + "-" +
           std::to_string(GetTickCount64() % 1000000000ull);
}

std::vector<std::string> ListOutputs() {
    for (int attempt = 0; attempt < 4; ++attempt) {
        size_t required = 0;
        uint32_t count = 0;
        const int32_t probe = syncfl_midi_list_outputs(nullptr, 0, &required, &count);
        if (probe != SYNCFL_MIDI_E_BUFFER_TOO_SMALL) throw mini_test::Failure("size query failed");
        std::vector<char> buffer(required);
        const int32_t status = syncfl_midi_list_outputs(buffer.data(), buffer.size(), &required, &count);
        if (status == SYNCFL_MIDI_E_BUFFER_TOO_SMALL) continue;  // devices changed in between
        if (status != SYNCFL_MIDI_OK) throw mini_test::Failure("list failed");
        std::vector<std::string> names;
        const char* cursor = buffer.data();
        for (uint32_t i = 0; i < count; ++i) {
            names.emplace_back(cursor);
            cursor += names.back().size() + 1;
        }
        if (*cursor != '\0' || static_cast<size_t>(cursor + 1 - buffer.data()) != required)
            throw mini_test::Failure("list layout mismatch");
        return names;
    }
    throw mini_test::Failure("device set kept changing");
}

}  // namespace

TEST_CASE(dll_reports_the_header_abi_version) {
    CHECK_EQ(syncfl_midi_abi_version(), SYNCFL_MIDI_ABI_VERSION);
}

TEST_CASE(real_enumeration_returns_a_consistent_snapshot) {
    const auto names = ListOutputs();
    std::printf("       %zu WinMM output(s) visible\n", names.size());
    CHECK(syncfl_midi_last_status() == SYNCFL_MIDI_OK);
}

TEST_CASE(absent_name_is_not_found_without_opening) {
    const std::string name = AbsentName();
    CHECK_EQ(syncfl_midi_resolve_output(name.c_str(), SYNCFL_MIDI_OPEN_EXACT, nullptr, 0, nullptr), SYNCFL_MIDI_E_NOT_FOUND);
    CHECK_EQ(syncfl_midi_resolve_output(name.c_str(), SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX, nullptr,
                                        0, nullptr),
             SYNCFL_MIDI_E_NOT_FOUND);
    syncfl_midi_session session = 7;
    CHECK_EQ(syncfl_midi_open_output(name.c_str(), SYNCFL_MIDI_OPEN_EXACT, &session),
             SYNCFL_MIDI_E_NOT_FOUND);
    CHECK_EQ(session, 0u);
    size_t required = 0;
    char message[256] = {};
    CHECK_EQ(syncfl_midi_last_error(nullptr, message, sizeof(message), &required), SYNCFL_MIDI_OK);
    CHECK(std::strstr(message, name.c_str()) != nullptr);
}

TEST_CASE(invalid_input_is_rejected_by_the_dll) {
    syncfl_midi_session session = 0;
    CHECK_EQ(syncfl_midi_open_output("\xC0\xAF", 0, &session), SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_open_output("", 0, &session), SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_resolve_output("0123456789012345678901234567890123", 0, nullptr, 0,
                                        nullptr),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_resolve_output("x", 0x10u, nullptr, 0, nullptr), SYNCFL_MIDI_E_INVALID_ARGUMENT);
}

TEST_CASE(forged_and_stale_tokens_are_rejected_by_the_dll) {
    for (syncfl_midi_session token : {0ull, 1ull, 0xDEADBEEFull, ~0ull}) {
        uint32_t attempted = 1;
        CHECK_EQ(syncfl_midi_send_wakeup(token, &attempted), SYNCFL_MIDI_E_INVALID_HANDLE);
        CHECK_EQ(attempted, 0u);
        CHECK_EQ(syncfl_midi_close(token), SYNCFL_MIDI_E_INVALID_HANDLE);
        size_t required = 0;
        CHECK_EQ(syncfl_midi_session_name(token, nullptr, 0, &required),
                 SYNCFL_MIDI_E_INVALID_HANDLE);
    }
}

int main(int argc, char** argv) { return mini_test::RunAll(argc, argv); }
