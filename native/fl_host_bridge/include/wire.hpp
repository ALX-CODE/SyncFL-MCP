#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <type_traits>

namespace alx {
constexpr std::uint32_t magic = 0x31484c41; // ALH1, little-endian Windows wire format.
constexpr std::uint32_t version = 1;
constexpr std::uint32_t max_notes = 128;
enum class Operation : std::uint32_t { ping = 1, project_info = 2, notes_preview = 3 };
enum class Status : std::uint32_t {
    ok = 0, invalid = 1, expired = 2, stale = 3, unsupported_abi = 4,
    unavailable = 5, wrong_thread = 6
};

#pragma pack(push, 1)
struct Note {
    std::int32_t position_ticks{};
    std::int32_t length_ticks{};
    std::int32_t pitch{};
    std::int32_t velocity_permille{};
};
struct Request {
    std::uint32_t signature{magic};
    std::uint32_t protocol{version};
    std::uint32_t bytes{sizeof(Request)};
    Operation operation{Operation::ping};
    std::uint64_t id{};
    std::uint64_t expires_unix_ms{};
    std::uint64_t expected_instance{};
    std::uint64_t expected_generation{};
    std::int32_t pattern{-1};
    std::int32_t channel{-1};
    std::uint32_t replace{};
    std::uint32_t count{};
    Note notes[max_notes]{};
};
struct Response {
    std::uint32_t signature{magic};
    std::uint32_t protocol{version};
    std::uint32_t bytes{sizeof(Response)};
    Status status{Status::invalid};
    std::uint64_t id{};
    std::uint64_t instance{};
    std::uint64_t generation{};
    std::int32_t host_version{};
    std::int32_t ppq{}; // Zero means not supplied by the host yet.
    std::int32_t playing{-1}; // -1 means unknown.
    std::uint32_t gui_thread{};
    std::uint32_t notes_enabled{}; // Always zero in this unqualified prototype.
    char detail[256]{};
};
#pragma pack(pop)

static_assert(sizeof(Note) == 16 && sizeof(Request) == 2112 && sizeof(Response) == 316);
static_assert(std::is_trivially_copyable_v<Request> && std::is_trivially_copyable_v<Response>);
std::uint64_t now_ms();
std::uint64_t random_id();
std::wstring pipe_name(std::uint32_t pid);
const char* status_name(Status status);
void detail(Response& response, const char* text);
bool validate(const Request& request, Response& response);
} // namespace alx
