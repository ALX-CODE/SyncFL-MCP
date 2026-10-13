#include "protocol.hpp"
#include <filesystem>
#include <cstdio>
#include <stdexcept>

unsigned long long number(const wchar_t* text) {
    if (!text || !*text || *text == L'-' || *text == L'+') throw std::runtime_error("invalid number");
    wchar_t* end = nullptr; errno = 0;
    auto value = std::wcstoull(text, &end, 0);
    if (errno || !end || *end) throw std::runtime_error("invalid number");
    return value;
}
int wmain(int argc, wchar_t** argv) {
    try {
        background::Config config;
        config.expected_image = L"C:\\Program Files\\Image-Line\\FL Studio 2026\\FL64.exe";
        bool have_x = false, have_y = false, have_pid = false, have_hwnd = false;
        for (int index = 1; index < argc; ++index) {
            std::wstring key = argv[index];
            if (key == L"--drop") { if (config.drop) throw std::runtime_error("duplicate drop"); config.drop = true; continue; }
            if (++index >= argc) throw std::runtime_error("missing value");
            if (key == L"--pid") {
                auto value = number(argv[index]); if (have_pid || value > MAXDWORD) throw std::runtime_error("invalid pid");
                config.pid = static_cast<DWORD>(value); have_pid = true;
            } else if (key == L"--hwnd") {
                if (have_hwnd) throw std::runtime_error("duplicate hwnd");
                config.hwnd = reinterpret_cast<HWND>(number(argv[index])); have_hwnd = true;
            } else if (key == L"--x" || key == L"--y") {
                wchar_t* end = nullptr; errno = 0; auto value = std::wcstoll(argv[index], &end, 10);
                if (errno || !end || *end || value < LONG_MIN || value > LONG_MAX) throw std::runtime_error("invalid point");
                bool& present = key == L"--x" ? have_x : have_y;
                if (present) throw std::runtime_error("duplicate point");
                present = true;
                (key == L"--x" ? config.point.x : config.point.y) = static_cast<LONG>(value);
            } else if (key == L"--file") {
                if (!config.file.empty()) throw std::runtime_error("duplicate file"); config.file = argv[index];
            } else if (key == L"--expected-exe") { config.expected_image = argv[index];
            } else if (key == L"--timeout-ms") {
                auto value = number(argv[index]); if (value > 30000) throw std::runtime_error("invalid timeout");
                config.timeout_ms = static_cast<DWORD>(value);
            } else throw std::runtime_error("unknown option");
        }
        if (!have_pid || !have_hwnd || (config.drop && (!have_x || !have_y || config.file.empty())) ||
            (!config.drop && (have_x || have_y || !config.file.empty())) ||
            !std::filesystem::path(config.expected_image).is_absolute() ||
            _wcsicmp(std::filesystem::path(config.expected_image).filename().c_str(), L"FL64.exe"))
            throw std::runtime_error("invalid target or options");
        auto result = background::run(config); background::print_result(result);
        return result.state == background::State::probed || result.state == background::State::dropped ? 0 : 3;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\nUsage: --pid N --hwnd N [--expected-exe absolute-FL64.exe] "
            "[--timeout-ms 100..30000] [--drop --x N --y N --file absolute-path]\n", error.what());
        return 2;
    }
}
