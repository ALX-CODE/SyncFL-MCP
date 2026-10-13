#pragma once
// Minimal test harness so the component needs no third-party test framework.

#include <cstdio>
#include <exception>
#include <string>
#include <vector>

namespace mini_test {

struct Case {
    const char* name;
    void (*body)();
};

inline std::vector<Case>& Cases() {
    static std::vector<Case> cases;
    return cases;
}

struct Failure : std::exception {
    std::string text;
    explicit Failure(std::string message) : text(std::move(message)) {}
    const char* what() const noexcept override { return text.c_str(); }
};

struct Registrar {
    Registrar(const char* name, void (*body)()) { Cases().push_back({name, body}); }
};

inline int RunAll(int argc, char** argv) {
    const std::string filter = argc > 1 ? argv[1] : "";
    int failed = 0, ran = 0;
    for (const auto& item : Cases()) {
        if (!filter.empty() && std::string(item.name).find(filter) == std::string::npos) continue;
        ++ran;
        try {
            item.body();
            std::printf("[ ok ] %s\n", item.name);
        } catch (const std::exception& error) {
            ++failed;
            std::printf("[FAIL] %s: %s\n", item.name, error.what());
        }
    }
    std::printf("%d/%d passed\n", ran - failed, ran);
    return failed == 0 && ran > 0 ? 0 : 1;
}

}  // namespace mini_test

#define MT_CONCAT_INNER(a, b) a##b
#define MT_CONCAT(a, b) MT_CONCAT_INNER(a, b)
#define TEST_CASE(name)                                                       \
    static void name();                                                       \
    static const ::mini_test::Registrar MT_CONCAT(name, _registrar){#name, name}; \
    static void name()
#define CHECK(condition)                                                                 \
    do {                                                                                 \
        if (!(condition))                                                                \
            throw ::mini_test::Failure("line " + std::to_string(__LINE__) + ": " #condition); \
    } while (0)
#define CHECK_EQ(actual, expected)                                                    \
    do {                                                                              \
        const auto mt_a = (actual);                                                   \
        const auto mt_e = (expected);                                                 \
        if (!(mt_a == mt_e))                                                          \
            throw ::mini_test::Failure("line " + std::to_string(__LINE__) + ": " #actual \
                                       " == " #expected " (got " + std::to_string(mt_a) + \
                                       ", want " + std::to_string(mt_e) + ")");       \
    } while (0)
