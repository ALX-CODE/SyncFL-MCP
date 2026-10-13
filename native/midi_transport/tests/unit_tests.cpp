// Unit tests for the transport core and the C ABI, driven through FakeWinMm so
// no real MIDI endpoint is ever enumerated, opened or written.

#include "abi_internal.hpp"
#include "fake_winmm.hpp"
#include "mini_test.hpp"
#include "syncfl_midi_transport.h"
#include "transport.hpp"
#include "utf.hpp"

#include <atomic>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <thread>

using namespace syncfl::midi;
using Kind = FakeWinMm::Kind;

namespace {

int S(Status status) { return static_cast<int>(status); }
std::string U8(const char8_t* text) { return reinterpret_cast<const char*>(text); }

constexpr wchar_t kManaged[] = L"ALX FL Bridge (Send)";
const std::string kManagedUtf8 = "ALX FL Bridge (Send)";

// Installs a fake behind the C ABI for one test and restores WinMM afterwards.
struct AbiFake {
    FakeWinMm fake;
    explicit AbiFake(std::vector<std::wstring> names = {}) : fake(std::move(names)) {
        abi::UseApiForTesting(&fake);
    }
    ~AbiFake() { abi::UseApiForTesting(nullptr); }
};

std::string LastMessage() {
    size_t required = 0;
    char buffer[512] = {};
    syncfl_midi_last_error(nullptr, buffer, sizeof(buffer), &required);
    return buffer;
}

}  // namespace

// ---------------------------------------------------------------- UTF ------

TEST_CASE(utf8_decoder_accepts_multilingual_text) {
    CHECK(Utf8ToUtf16Strict(U8(u8"Ünïcödé")) ==
          std::wstring(L"Ünïcödé"));
    CHECK(Utf8ToUtf16Strict(U8(u8"ミディ出力")) ==
          std::wstring(L"ミディ出力"));
    CHECK(Utf8ToUtf16Strict(U8(u8"\U0001F3B9 Bridge")) == std::wstring(L"\U0001F3B9 Bridge"));
}

TEST_CASE(utf8_decoder_rejects_malformed_sequences) {
    const std::string bad[] = {
        "\xC0\xAF",              // overlong '/'
        "\xE0\x80\xAF",          // overlong
        "\xED\xA0\x80",          // encoded high surrogate
        "\xED\xBF\xBF",          // encoded low surrogate
        "\xF4\x90\x80\x80",      // > U+10FFFF
        "\xF8\x88\x80\x80\x80",  // 5-byte form
        "\xFF",
        "\x80",                  // stray continuation
        "\xE2\x82",              // truncated
        "A\xC3",                 // truncated at end
        std::string("a\0b", 3),  // embedded NUL
    };
    for (const auto& text : bad) CHECK(!Utf8ToUtf16Strict(text).has_value());
}

TEST_CASE(utf16_encoder_replaces_lone_surrogates) {
    CHECK(Utf16ToUtf8Lossy(std::wstring(L"A\xD800" L"B")) == "A\xEF\xBF\xBD" "B");
    CHECK(Utf16ToUtf8Lossy(std::wstring(L"A\xDC00")) == "A\xEF\xBF\xBD");
    CHECK(Utf16ToUtf8Lossy(std::wstring(L"\xD83C")) == "\xEF\xBF\xBD");
    CHECK(Utf16ToUtf8Lossy(L"\U0001F3B9") == "\xF0\x9F\x8E\xB9");
    CHECK(!IsValidUtf16(std::wstring(L"x\xDFFF")));
    CHECK(IsValidUtf16(L"\U0001F3B9"));
}

// ----------------------------------------------------------- Matching -----

TEST_CASE(matching_is_exact_by_default) {
    CHECK(NameMatches(kManaged, kManaged, 0));
    CHECK(!NameMatches(L"ALX FL Bridge (Send) 2", kManaged, 0));
    CHECK(!NameMatches(L"alx fl bridge (send)", kManaged, 0));
    CHECK(!NameMatches(L"ALX FL Bridge (Send) ", kManaged, 0));
    CHECK(!NameMatches(L" ALX FL Bridge (Send)", kManaged, 0));
    CHECK(!NameMatches(L"ALX FL Bridge (Sen", kManaged, 0));
}

TEST_CASE(decimal_suffix_needs_the_flag_and_a_strict_shape) {
    for (const wchar_t* suffix : {L" 0", L" 1", L" 37", L" 1000"})
        CHECK(NameMatches(std::wstring(kManaged) + suffix, kManaged, kAllowDecimalSuffix));
    for (const wchar_t* suffix :
         {L"  1", L" 1a", L" -1", L" 1.0", L" ١", L" ", L"1", L" extra", L" 1 extra"})
        CHECK(!NameMatches(std::wstring(kManaged) + suffix, kManaged, kAllowDecimalSuffix));
    CHECK(!NameMatches(L"Other ALX FL Bridge (Send) 1", kManaged, kAllowDecimalSuffix));
}

// --------------------------------------------------------- Resolution -----

TEST_CASE(resolve_exact_unique_name) {
    FakeWinMm fake({L"Microsoft GS Wavetable Synth", kManaged});
    Transport transport(fake);
    Endpoint endpoint;
    CHECK_EQ(S(transport.Resolve(kManagedUtf8, 0, endpoint).status), S(Status::Ok));
    CHECK_EQ(endpoint.device, 1u);
    CHECK(endpoint.name == kManaged);
    CHECK_EQ(fake.Calls().size(), 0u);  // resolving never opens
}

TEST_CASE(missing_name_is_not_found_and_never_opens) {
    FakeWinMm fake({L"Microsoft GS Wavetable Synth"});
    Transport transport(fake);
    std::uint64_t session = 99;
    const auto result = transport.Open(kManagedUtf8, 0, session);
    CHECK_EQ(S(result.status), S(Status::NotFound));
    CHECK_EQ(session, 0u);
    CHECK_EQ(fake.Calls().size(), 0u);
}

TEST_CASE(duplicate_names_are_ambiguous_and_never_open) {
    FakeWinMm fake({kManaged, L"Other", kManaged});
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK_EQ(S(transport.Open(kManagedUtf8, 0, session).status), S(Status::Ambiguous));
    CHECK_EQ(fake.Calls().size(), 0u);
}

TEST_CASE(suffixed_names_only_match_with_the_explicit_flag) {
    {
        FakeWinMm fake({L"ALX FL Bridge (Send) 2"});
        Transport transport(fake);
        std::uint64_t session = 0;
        CHECK_EQ(S(transport.Open(kManagedUtf8, 0, session).status), S(Status::NotFound));
        CHECK_EQ(S(transport.Open(kManagedUtf8, kAllowDecimalSuffix, session).status),
                 S(Status::Ok));
        std::string name;
        CHECK_EQ(S(transport.SessionName(session, name).status), S(Status::Ok));
        CHECK(name == "ALX FL Bridge (Send) 2");
        CHECK_EQ(S(transport.Close(session).status), S(Status::Ok));
    }
    {
        FakeWinMm fake({kManaged, L"ALX FL Bridge (Send) 2"});
        Transport transport(fake);
        Endpoint endpoint;
        CHECK_EQ(S(transport.Resolve(kManagedUtf8, kAllowDecimalSuffix, endpoint).status),
                 S(Status::Ambiguous));
        CHECK_EQ(S(transport.Resolve(kManagedUtf8, 0, endpoint).status), S(Status::Ok));
        CHECK(endpoint.name == kManaged);
    }
}

TEST_CASE(never_falls_back_to_first_or_similar_device) {
    FakeWinMm fake({L"Microsoft GS Wavetable Synth", L"ALX FL Bridge (Send) extra",
                    L"alx fl bridge (send)", L"ALX FL Bridge (Send)1"});
    Transport transport(fake);
    Endpoint endpoint;
    CHECK_EQ(S(transport.Resolve(kManagedUtf8, kAllowDecimalSuffix, endpoint).status),
             S(Status::NotFound));
}

TEST_CASE(non_ascii_names_enumerate_and_open_exactly) {
    FakeWinMm fake({L"Ünïcödé MIDI", L"ミディ出力",
                    L"\U0001F3B9 Bridge"});
    Transport transport(fake);
    std::vector<std::string> names;
    CHECK_EQ(S(transport.ListOutputs(names).status), S(Status::Ok));
    CHECK_EQ(names.size(), 3u);
    CHECK(names[0] == U8(u8"Ünïcödé MIDI"));
    CHECK(names[1] == U8(u8"ミディ出力"));
    CHECK(names[2] == U8(u8"\U0001F3B9 Bridge"));
    for (const auto& name : names) {
        std::uint64_t session = 0;
        CHECK_EQ(S(transport.Open(name, 0, session).status), S(Status::Ok));
        std::string opened;
        CHECK_EQ(S(transport.SessionName(session, opened).status), S(Status::Ok));
        CHECK(opened == name);
        CHECK_EQ(S(transport.Close(session).status), S(Status::Ok));
    }
    // Canonically equivalent but differently encoded (NFD) names do not match.
    Endpoint endpoint;
    CHECK_EQ(S(transport.Resolve(U8(u8"Ünïcödé MIDI"), 0, endpoint).status),
             S(Status::NotFound));
}

TEST_CASE(invalid_utf16_device_name_is_listed_lossily_and_cannot_be_opened) {
    FakeWinMm fake;
    fake.devices.push_back({std::wstring(L"Bad\xD800")});
    Transport transport(fake);
    std::vector<std::string> names;
    CHECK_EQ(S(transport.ListOutputs(names).status), S(Status::Ok));
    CHECK(names.at(0) == "Bad\xEF\xBF\xBD");
    Endpoint endpoint;
    CHECK_EQ(S(transport.Resolve(names[0], 0, endpoint).status), S(Status::NotFound));
}

TEST_CASE(unterminated_product_name_is_bounded) {
    FakeWinMm fake;
    FakeWinMm::Device device{L"0123456789"};
    device.unterminated = true;
    fake.devices.push_back(device);
    Transport transport(fake);
    std::vector<std::string> names;
    CHECK_EQ(S(transport.ListOutputs(names).status), S(Status::Ok));
    CHECK_EQ(names.at(0).size(), static_cast<size_t>(MAXPNAMELEN));
}

TEST_CASE(invalid_requests_are_rejected_before_any_winmm_call) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    Endpoint endpoint;
    const std::string too_long(kMaxNameUnits + 1, 'x');
    for (const std::string& name : {std::string(), std::string("\xC0\xAF"), too_long,
                                    std::string("a\0b", 3)})
        CHECK_EQ(S(transport.Resolve(name, 0, endpoint).status), S(Status::InvalidArgument));
    CHECK_EQ(S(transport.Resolve(kManagedUtf8, 2, endpoint).status), S(Status::InvalidArgument));
    CHECK_EQ(S(transport.Resolve(kManagedUtf8, 0x80000000u, endpoint).status),
             S(Status::InvalidArgument));
    CHECK_EQ(fake.CountCalls(), 0u);
}

TEST_CASE(maximum_length_name_is_accepted) {
    const std::wstring longest(kMaxNameUnits, L'n');
    FakeWinMm fake({longest});
    Transport transport(fake);
    Endpoint endpoint;
    CHECK_EQ(S(transport.Resolve(std::string(kMaxNameUnits, 'n'), 0, endpoint).status),
             S(Status::Ok));
}

// -------------------------------------------------------- Enumeration -----

TEST_CASE(enumeration_retries_after_one_device_change) {
    FakeWinMm fake({L"A", L"B"});
    fake.on_count = [](FakeWinMm& self, unsigned index) {
        if (index == 1) self.AddDevice({L"C"});  // changes between the two counts
    };
    Transport transport(fake);
    std::vector<std::string> names;
    CHECK_EQ(S(transport.ListOutputs(names).status), S(Status::Ok));
    CHECK_EQ(names.size(), 3u);
}

TEST_CASE(enumeration_gives_up_when_devices_keep_changing) {
    FakeWinMm fake({L"A"});
    fake.on_count = [](FakeWinMm& self, unsigned) { self.AddDevice({L"churn"}); };
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK_EQ(S(transport.Open("A", 0, session).status), S(Status::DeviceSetChanged));
    CHECK_EQ(fake.Count(Kind::Open), 0u);
}

TEST_CASE(vanishing_device_during_enumeration_is_unstable) {
    FakeWinMm fake({L"A"});
    fake.devices[0].caps_error = MMSYSERR_BADDEVICEID;
    Transport transport(fake);
    std::vector<std::string> names;
    CHECK_EQ(S(transport.ListOutputs(names).status), S(Status::DeviceSetChanged));
}

TEST_CASE(unreadable_device_fails_closed) {
    FakeWinMm fake({kManaged, L"Broken"});
    fake.devices[1].caps_error = MMSYSERR_NODRIVER;
    Transport transport(fake);
    Endpoint endpoint;
    const auto result = transport.Resolve(kManagedUtf8, 0, endpoint);
    CHECK_EQ(S(result.status), S(Status::Native));  // a hidden duplicate cannot be ruled out
    CHECK_EQ(result.native, static_cast<std::uint32_t>(MMSYSERR_NODRIVER));
}

// ------------------------------------------------------ Open identity -----

TEST_CASE(index_shift_during_open_is_detected_and_cleaned_up) {
    FakeWinMm fake({kManaged});
    fake.before_open = [](FakeWinMm& self) { self.ReplaceDevice(0, {L"Hardware Synth"}); };
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK_EQ(S(transport.Open(kManagedUtf8, 0, session).status), S(Status::EndpointChanged));
    CHECK_EQ(session, 0u);
    CHECK_EQ(fake.LiveHandles(), 0u);
    CHECK_EQ(fake.Count(Kind::Send), 0u);
}

TEST_CASE(same_name_on_a_different_device_is_rejected) {
    FakeWinMm fake({kManaged});
    fake.before_open = [](FakeWinMm& self) {
        FakeWinMm::Device impostor{kManaged};
        impostor.pid = 77;
        self.ReplaceDevice(0, impostor);
    };
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK_EQ(S(transport.Open(kManagedUtf8, 0, session).status), S(Status::EndpointChanged));
    CHECK_EQ(fake.LiveHandles(), 0u);
}

TEST_CASE(duplicate_appearing_during_open_is_rejected) {
    FakeWinMm fake({kManaged});
    fake.after_open = [](FakeWinMm& self) { self.AddDevice({kManaged}); };
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK_EQ(S(transport.Open(kManagedUtf8, 0, session).status), S(Status::EndpointChanged));
    CHECK_EQ(fake.LiveHandles(), 0u);
    CHECK_EQ(transport.OpenSessionCount(), 0u);
}

TEST_CASE(open_errors_map_to_specific_statuses) {
    const std::pair<MMRESULT, Status> cases[] = {
        {MMSYSERR_BADDEVICEID, Status::EndpointChanged},
        {MMSYSERR_NODRIVER, Status::EndpointChanged},
        {MMSYSERR_ALLOCATED, Status::Busy},
        {MMSYSERR_NOMEM, Status::OutOfMemory},
        {MMSYSERR_ERROR, Status::Native},
    };
    for (const auto& [code, expected] : cases) {
        FakeWinMm fake({kManaged});
        fake.open_result = code;
        Transport transport(fake);
        std::uint64_t session = 0;
        const auto result = transport.Open(kManagedUtf8, 0, session);
        CHECK_EQ(S(result.status), S(expected));
        CHECK_EQ(result.native, static_cast<std::uint32_t>(code));
        CHECK_EQ(session, 0u);
    }
}

TEST_CASE(identity_read_failure_closes_the_handle) {
    FakeWinMm fake({kManaged});
    fake.handle_caps_result = MMSYSERR_ERROR;
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK(!transport.Open(kManagedUtf8, 0, session).ok());
    CHECK_EQ(fake.LiveHandles(), 0u);
    CHECK_EQ(fake.Count(Kind::Close), 1u);
}

// -------------------------------------------------------------- Send ------

TEST_CASE(wakeup_emits_exactly_one_three_byte_note_on) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK_EQ(S(transport.Open(kManagedUtf8, 0, session).status), S(Status::Ok));
    bool attempted = false;
    CHECK_EQ(S(transport.SendWakeup(session, attempted).status), S(Status::Ok));
    CHECK(attempted);
    CHECK_EQ(S(transport.Close(session).status), S(Status::Ok));

    const auto calls = fake.Calls();
    CHECK_EQ(calls.size(), 3u);
    CHECK(calls[0].kind == Kind::Open);
    CHECK(calls[1].kind == Kind::Send);
    CHECK(calls[2].kind == Kind::Close);
    CHECK_EQ(calls[1].message, 0x007F7F90ul);
    CHECK_EQ(calls[1].message & 0xFF, 0x90ul);           // note-on, channel 0
    CHECK_EQ((calls[1].message >> 8) & 0xFF, 127ul);     // note 127
    CHECK_EQ((calls[1].message >> 16) & 0xFF, 127ul);    // velocity 127
    CHECK_EQ(calls[1].message >> 24, 0ul);               // nothing beyond three bytes
}

TEST_CASE(every_successful_call_emits_once) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK(transport.Open(kManagedUtf8, 0, session).ok());
    for (int i = 0; i < 100; ++i) {
        bool attempted = false;
        CHECK(transport.SendWakeup(session, attempted).ok());
        CHECK(attempted);
    }
    CHECK_EQ(fake.Count(Kind::Send), 100u);
    for (const auto& call : fake.Calls())
        if (call.kind == Kind::Send) CHECK_EQ(call.message, kWakeupMessage);
    CHECK(transport.Close(session).ok());
}

TEST_CASE(failed_send_is_reported_as_attempted_and_never_retried) {
    FakeWinMm fake({kManaged});
    fake.send_result = MIDIERR_NOTREADY;
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK(transport.Open(kManagedUtf8, 0, session).ok());
    bool attempted = false;
    const auto result = transport.SendWakeup(session, attempted);
    CHECK_EQ(S(result.status), S(Status::SendFailed));
    CHECK(attempted);
    CHECK_EQ(result.native, static_cast<std::uint32_t>(MIDIERR_NOTREADY));
    CHECK_EQ(fake.Count(Kind::Send), 1u);
    CHECK(transport.Close(session).ok());
}

TEST_CASE(open_and_close_transmit_nothing) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    for (int i = 0; i < 10; ++i) {
        std::uint64_t session = 0;
        CHECK(transport.Open(kManagedUtf8, 0, session).ok());
        CHECK(transport.Close(session).ok());
    }
    CHECK_EQ(fake.Count(Kind::Send), 0u);
}

// ----------------------------------------------------------- Handles ------

TEST_CASE(invalid_handles_never_reach_winmm) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    for (std::uint64_t token : {0ull, 1ull, 12345ull, ~0ull}) {
        bool attempted = true;
        CHECK_EQ(S(transport.SendWakeup(token, attempted).status), S(Status::InvalidHandle));
        CHECK(!attempted);
        CHECK_EQ(S(transport.Close(token).status), S(Status::InvalidHandle));
        std::string name;
        CHECK_EQ(S(transport.SessionName(token, name).status), S(Status::InvalidHandle));
    }
    CHECK_EQ(fake.Calls().size(), 0u);
}

TEST_CASE(double_close_closes_once_and_invalidates_the_token) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK(transport.Open(kManagedUtf8, 0, session).ok());
    CHECK_EQ(S(transport.Close(session).status), S(Status::Ok));
    CHECK_EQ(S(transport.Close(session).status), S(Status::InvalidHandle));
    bool attempted = true;
    CHECK_EQ(S(transport.SendWakeup(session, attempted).status), S(Status::InvalidHandle));
    CHECK(!attempted);
    CHECK_EQ(fake.Count(Kind::Close), 1u);
    CHECK_EQ(fake.Count(Kind::Send), 0u);
}

TEST_CASE(repeated_opens_use_fresh_tokens_and_leak_nothing) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    std::uint64_t previous = 0;
    for (int i = 0; i < 1000; ++i) {
        std::uint64_t session = 0;
        CHECK(transport.Open(kManagedUtf8, 0, session).ok());
        CHECK(session > previous);
        previous = session;
        CHECK(transport.Close(session).ok());
    }
    CHECK_EQ(fake.LiveHandles(), 0u);
    CHECK_EQ(transport.OpenSessionCount(), 0u);
}

TEST_CASE(close_error_still_invalidates_the_session) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    std::uint64_t session = 0;
    CHECK(transport.Open(kManagedUtf8, 0, session).ok());
    fake.close_result = MMSYSERR_ERROR;
    const auto result = transport.Close(session);
    CHECK_EQ(S(result.status), S(Status::Native));
    CHECK_EQ(result.native, static_cast<std::uint32_t>(MMSYSERR_ERROR));
    CHECK_EQ(S(transport.Close(session).status), S(Status::InvalidHandle));
    bool attempted = true;
    CHECK_EQ(S(transport.SendWakeup(session, attempted).status), S(Status::InvalidHandle));
    CHECK_EQ(fake.Count(Kind::Close), 1u);
}

// ------------------------------------------------------- Concurrency ------

TEST_CASE(concurrent_send_and_close_never_sends_on_a_closed_handle) {
    for (int round = 0; round < 25; ++round) {
        FakeWinMm fake({kManaged});
        Transport transport(fake);
        std::uint64_t session = 0;
        CHECK(transport.Open(kManagedUtf8, 0, session).ok());
        std::atomic<int> ok{0}, rejected{0}, other{0}, attempted_after_close{0};
        std::atomic<bool> go{false};
        std::vector<std::thread> senders;
        for (int t = 0; t < 8; ++t) {
            senders.emplace_back([&] {
                while (!go) std::this_thread::yield();
                for (int i = 0; i < 2000; ++i) {
                    bool attempted = false;
                    const auto status = transport.SendWakeup(session, attempted).status;
                    if (status == Status::Ok) {
                        ++ok;
                    } else if (status == Status::InvalidHandle) {
                        ++rejected;
                        if (attempted) ++attempted_after_close;
                        break;
                    } else {
                        ++other;
                    }
                }
            });
        }
        go = true;
        std::this_thread::sleep_for(std::chrono::microseconds(200 + round * 40));
        CHECK_EQ(S(transport.Close(session).status), S(Status::Ok));
        for (auto& thread : senders) thread.join();

        CHECK_EQ(other.load(), 0);
        CHECK_EQ(attempted_after_close.load(), 0);
        CHECK_EQ(fake.SendsOnDeadHandles(), 0u);
        CHECK_EQ(fake.Count(Kind::Send), static_cast<size_t>(ok.load()));  // one emission per OK
        std::uint64_t close_seq = 0;
        for (const auto& call : fake.Calls())
            if (call.kind == Kind::Close) close_seq = call.seq;
        for (const auto& call : fake.Calls())
            if (call.kind == Kind::Send) CHECK(call.seq < close_seq);
    }
}

TEST_CASE(concurrent_sessions_open_send_close_independently) {
    FakeWinMm fake({kManaged});
    Transport transport(fake);
    std::atomic<int> failures{0};
    std::vector<std::thread> workers;
    for (int t = 0; t < 8; ++t) {
        workers.emplace_back([&] {
            for (int i = 0; i < 200; ++i) {
                std::uint64_t session = 0;
                bool attempted = false;
                if (!transport.Open(kManagedUtf8, 0, session).ok() ||
                    !transport.SendWakeup(session, attempted).ok() || !attempted ||
                    !transport.Close(session).ok())
                    ++failures;
            }
        });
    }
    for (auto& worker : workers) worker.join();
    CHECK_EQ(failures.load(), 0);
    CHECK_EQ(fake.Count(Kind::Send), 1600u);
    CHECK_EQ(fake.LiveHandles(), 0u);
    CHECK_EQ(fake.ClosesOfDeadHandles(), 0u);
}

// --------------------------------------------------------------- ABI ------

TEST_CASE(abi_reports_version_and_status_names) {
    CHECK_EQ(syncfl_midi_abi_version(), (1u << 16) | 0u);
    CHECK(std::strcmp(syncfl_midi_status_name(SYNCFL_MIDI_OK), "SYNCFL_MIDI_OK") == 0);
    CHECK(std::strcmp(syncfl_midi_status_name(SYNCFL_MIDI_E_ENDPOINT_CHANGED),
                      "SYNCFL_MIDI_E_ENDPOINT_CHANGED") == 0);
    CHECK(std::strcmp(syncfl_midi_status_name(SYNCFL_MIDI_E_INTERNAL),
                      "SYNCFL_MIDI_E_INTERNAL") == 0);
    CHECK(std::strcmp(syncfl_midi_status_name(-1), "SYNCFL_MIDI_E_UNKNOWN") == 0);
    CHECK(std::strcmp(syncfl_midi_status_name(13), "SYNCFL_MIDI_E_UNKNOWN") == 0);
}

TEST_CASE(abi_list_honours_the_buffer_contract) {
    AbiFake abi({L"A", L"Ü"});
    size_t required = 0;
    uint32_t count = 0;
    CHECK_EQ(syncfl_midi_list_outputs(nullptr, 0, &required, &count),
             SYNCFL_MIDI_E_BUFFER_TOO_SMALL);
    const size_t expected = 2 + 3 + 1;  // "A\0" + "\xC3\x9C\0" + final NUL
    CHECK_EQ(required, expected);
    CHECK_EQ(count, 2u);

    char small[16];
    std::memset(small, '#', sizeof(small));
    CHECK_EQ(syncfl_midi_list_outputs(small, expected - 1, &required, &count),
             SYNCFL_MIDI_E_BUFFER_TOO_SMALL);
    for (char ch : small) CHECK(ch == '#');  // untouched

    char exact[expected];
    CHECK_EQ(syncfl_midi_list_outputs(exact, expected, &required, &count), SYNCFL_MIDI_OK);
    CHECK(std::memcmp(exact, "A\0\xC3\x9C\0\0", expected) == 0);

    CHECK_EQ(syncfl_midi_list_outputs(exact, expected, nullptr, &count),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_list_outputs(exact, expected, &required, nullptr),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_list_outputs(nullptr, 8, &required, &count),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);
}

TEST_CASE(abi_list_of_no_devices_is_a_single_terminator) {
    AbiFake abi;
    size_t required = 0;
    uint32_t count = 7;
    char buffer[4] = {'#', '#', '#', '#'};
    CHECK_EQ(syncfl_midi_list_outputs(buffer, sizeof(buffer), &required, &count), SYNCFL_MIDI_OK);
    CHECK_EQ(required, 1u);
    CHECK_EQ(count, 0u);
    CHECK(buffer[0] == '\0');
}

TEST_CASE(abi_open_validates_arguments) {
    AbiFake abi({kManaged});
    syncfl_midi_session session = 42;
    CHECK_EQ(syncfl_midi_open_output(nullptr, 0, &session), SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(session, 0u);
    CHECK_EQ(syncfl_midi_open_output(kManagedUtf8.c_str(), 0, nullptr),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_resolve_output(nullptr, 0, nullptr, 0, nullptr), SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(syncfl_midi_resolve_output(kManagedUtf8.c_str(), 4, nullptr, 0, nullptr), SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(abi.fake.Calls().size(), 0u);
}

TEST_CASE(abi_resolve_reports_the_matched_name) {
    AbiFake abi({L"ALX FL Bridge (Send) 2"});
    size_t required = 0;
    CHECK_EQ(syncfl_midi_resolve_output(kManagedUtf8.c_str(), SYNCFL_MIDI_OPEN_EXACT, nullptr, 0,
                                        &required),
             SYNCFL_MIDI_E_NOT_FOUND);
    CHECK_EQ(required, 0u);
    CHECK_EQ(syncfl_midi_resolve_output(kManagedUtf8.c_str(), SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX,
                                        nullptr, 0, &required),
             SYNCFL_MIDI_E_BUFFER_TOO_SMALL);
    const std::string expected = "ALX FL Bridge (Send) 2";
    CHECK_EQ(required, expected.size() + 1);
    std::string matched(required, '#');
    CHECK_EQ(syncfl_midi_resolve_output(kManagedUtf8.c_str(), SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX,
                                        matched.data(), matched.size(), &required),
             SYNCFL_MIDI_OK);
    CHECK(std::strcmp(matched.c_str(), expected.c_str()) == 0);
    CHECK_EQ(syncfl_midi_resolve_output(kManagedUtf8.c_str(), SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX,
                                        matched.data(), matched.size(), nullptr),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(abi.fake.Calls().size(), 0u);  // resolving never opens
}

TEST_CASE(abi_full_session_round_trip) {
    AbiFake abi({kManaged});
    syncfl_midi_session session = 0;
    CHECK_EQ(syncfl_midi_open_output(kManagedUtf8.c_str(), SYNCFL_MIDI_OPEN_EXACT, &session),
             SYNCFL_MIDI_OK);
    CHECK(session != 0);

    size_t required = 0;
    CHECK_EQ(syncfl_midi_session_name(session, nullptr, 0, &required),
             SYNCFL_MIDI_E_BUFFER_TOO_SMALL);
    CHECK_EQ(required, kManagedUtf8.size() + 1);
    std::string name(required, '#');
    CHECK_EQ(syncfl_midi_session_name(session, name.data(), required - 1, &required),
             SYNCFL_MIDI_E_BUFFER_TOO_SMALL);
    CHECK(name == std::string(required, '#'));
    CHECK_EQ(syncfl_midi_session_name(session, name.data(), name.size(), &required),
             SYNCFL_MIDI_OK);
    CHECK(std::strcmp(name.c_str(), kManagedUtf8.c_str()) == 0);

    CHECK_EQ(syncfl_midi_send_wakeup(session, nullptr), SYNCFL_MIDI_E_INVALID_ARGUMENT);
    CHECK_EQ(abi.fake.Count(Kind::Send), 0u);
    uint32_t attempted = 9;
    CHECK_EQ(syncfl_midi_send_wakeup(session, &attempted), SYNCFL_MIDI_OK);
    CHECK_EQ(attempted, 1u);
    CHECK_EQ(syncfl_midi_close(session), SYNCFL_MIDI_OK);
    CHECK_EQ(syncfl_midi_close(session), SYNCFL_MIDI_E_INVALID_HANDLE);
    CHECK_EQ(syncfl_midi_send_wakeup(session, &attempted), SYNCFL_MIDI_E_INVALID_HANDLE);
    CHECK_EQ(attempted, 0u);
    CHECK_EQ(abi.fake.Count(Kind::Send), 1u);
}

TEST_CASE(abi_last_error_is_stable_until_the_next_call) {
    AbiFake abi({L"Other"});
    syncfl_midi_session session = 0;
    CHECK_EQ(syncfl_midi_open_output(kManagedUtf8.c_str(), 0, &session), SYNCFL_MIDI_E_NOT_FOUND);
    CHECK_EQ(syncfl_midi_last_status(), SYNCFL_MIDI_E_NOT_FOUND);

    size_t required = 0;
    char tiny[2] = {'#', '#'};
    CHECK_EQ(syncfl_midi_last_error(nullptr, tiny, sizeof(tiny), &required),
             SYNCFL_MIDI_E_BUFFER_TOO_SMALL);
    CHECK(tiny[0] == '#' && tiny[1] == '#');
    CHECK(required > 2);
    CHECK_EQ(syncfl_midi_last_status(), SYNCFL_MIDI_E_NOT_FOUND);  // copying did not clobber it
    CHECK(LastMessage().find("ALX FL Bridge (Send)") != std::string::npos);
    CHECK_EQ(syncfl_midi_last_error(nullptr, nullptr, 8, &required),
             SYNCFL_MIDI_E_INVALID_ARGUMENT);

    CHECK_EQ(syncfl_midi_resolve_output("Other", 0, nullptr, 0, nullptr), SYNCFL_MIDI_OK);  // success clears it
    CHECK_EQ(syncfl_midi_last_status(), SYNCFL_MIDI_OK);
    uint32_t native = 5;
    char buffer[8] = {'#'};
    CHECK_EQ(syncfl_midi_last_error(&native, buffer, sizeof(buffer), &required), SYNCFL_MIDI_OK);
    CHECK_EQ(native, 0u);
    CHECK_EQ(required, 1u);
    CHECK(buffer[0] == '\0');
}

TEST_CASE(abi_last_error_is_per_thread_and_carries_native_codes) {
    AbiFake abi({kManaged});
    abi.fake.send_result = MIDIERR_NOTREADY;
    syncfl_midi_session session = 0;
    CHECK_EQ(syncfl_midi_open_output(kManagedUtf8.c_str(), 0, &session), SYNCFL_MIDI_OK);
    uint32_t attempted = 0;
    CHECK_EQ(syncfl_midi_send_wakeup(session, &attempted), SYNCFL_MIDI_E_SEND_FAILED);
    CHECK_EQ(attempted, 1u);
    uint32_t native = 0;
    size_t required = 0;
    char buffer[256];
    CHECK_EQ(syncfl_midi_last_error(&native, buffer, sizeof(buffer), &required), SYNCFL_MIDI_OK);
    CHECK_EQ(native, static_cast<uint32_t>(MIDIERR_NOTREADY));

    int32_t other_thread_status = -1;
    std::thread([&] { other_thread_status = syncfl_midi_last_status(); }).join();
    CHECK_EQ(other_thread_status, SYNCFL_MIDI_OK);
    CHECK_EQ(syncfl_midi_close(session), SYNCFL_MIDI_OK);
}

TEST_CASE(exception_firewall_maps_every_exception) {
    CHECK_EQ(abi::Guarded([]() -> Result { throw std::bad_alloc(); }),
             SYNCFL_MIDI_E_OUT_OF_MEMORY);
    CHECK_EQ(syncfl_midi_last_status(), SYNCFL_MIDI_E_OUT_OF_MEMORY);
    CHECK_EQ(abi::Guarded([]() -> Result { throw std::runtime_error("boom"); }),
             SYNCFL_MIDI_E_INTERNAL);
    CHECK_EQ(abi::Guarded([]() -> Result { throw 7; }), SYNCFL_MIDI_E_INTERNAL);
    CHECK_EQ(syncfl_midi_last_status(), SYNCFL_MIDI_E_INTERNAL);
}

int main(int argc, char** argv) { return mini_test::RunAll(argc, argv); }
