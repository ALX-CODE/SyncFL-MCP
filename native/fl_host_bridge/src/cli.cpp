#include "ipc.hpp"
#include <charconv>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace {
std::uint64_t number(std::string_view text) {
    std::uint64_t value{};
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size())
        throw std::invalid_argument("Arguments must be complete unsigned decimal integers");
    return value;
}
std::int32_t integer(std::string_view text) {
    const auto value = number(text);
    if (value > INT32_MAX) throw std::invalid_argument("Integer exceeds signed 32-bit range");
    return static_cast<std::int32_t>(value);
}
alx::Note note(std::string_view text) {
    alx::Note result;
    std::int32_t* fields[]{&result.position_ticks, &result.length_ticks,
                          &result.pitch, &result.velocity_permille};
    for (int index = 0; index < 4; ++index) {
        const auto separator = text.find(':');
        if ((index < 3 && separator == text.npos) || (index == 3 && separator != text.npos))
            throw std::invalid_argument("Note requires position:length:pitch:velocity_permille");
        *fields[index] = integer(text.substr(0, separator));
        if (separator != text.npos) text.remove_prefix(separator + 1);
    }
    return result;
}
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 4 || std::string_view(argv[1]) != "--pid")
            throw std::invalid_argument("Use --pid PID ping|project-info|notes-preview");
        const auto pid64 = number(argv[2]);
        if (!pid64 || pid64 > UINT32_MAX) throw std::invalid_argument("Invalid PID");
        alx::Request request;
        request.id = alx::random_id();
        request.expires_unix_ms = alx::now_ms() + 5000;
        const std::string_view command(argv[3]);
        if (command == "ping" || command == "project-info") {
            if (argc != 4) throw std::invalid_argument("Read command takes no target arguments");
            request.operation = command == "ping" ? alx::Operation::ping : alx::Operation::project_info;
        } else if (command == "notes-preview") {
            if (argc < 10 || argc > 9 + static_cast<int>(alx::max_notes))
                throw std::invalid_argument(
                    "notes-preview INSTANCE GENERATION PATTERN CHANNEL add|replace NOTE [NOTE...]");
            request.operation = alx::Operation::notes_preview;
            request.expected_instance = number(argv[4]);
            request.expected_generation = number(argv[5]);
            request.pattern = integer(argv[6]);
            request.channel = integer(argv[7]);
            const std::string_view mode(argv[8]);
            if (mode != "add" && mode != "replace") throw std::invalid_argument("Invalid notes mode");
            request.replace = mode == "replace" ? 1u : 0u;
            request.count = static_cast<std::uint32_t>(argc - 9);
            for (std::uint32_t index = 0; index < request.count; ++index)
                request.notes[index] = note(argv[index + 9]);
        } else {
            throw std::invalid_argument("Unknown command");
        }
        alx::Response preview;
        if (!alx::validate(request, preview)) throw std::invalid_argument(preview.detail);
        const auto response = alx::call(static_cast<std::uint32_t>(pid64), request);
        // Detail strings originate in this implementation, never from host metadata.
        std::cout << "{\"status\":\"" << alx::status_name(response.status)
                  << "\",\"instance\":\"" << response.instance
                  << "\",\"generation\":\"" << response.generation
                  << "\",\"host_version\":" << response.host_version
                  << ",\"ppq\":" << response.ppq << ",\"playing\":" << response.playing
                  << ",\"gui_thread\":" << response.gui_thread
                  << ",\"notes_enabled\":false,\"project_text_available\":false,\"detail\":\""
                  << response.detail << "\"}\n";
        return response.status == alx::Status::ok ? 0 : 2;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
