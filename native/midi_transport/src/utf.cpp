#include "utf.hpp"

#include <cstdint>

namespace syncfl::midi {
namespace {

constexpr char32_t kReplacement = 0xFFFD;

bool IsHighSurrogate(char32_t unit) noexcept { return unit >= 0xD800 && unit <= 0xDBFF; }
bool IsLowSurrogate(char32_t unit) noexcept { return unit >= 0xDC00 && unit <= 0xDFFF; }

void AppendUtf16(std::wstring& out, char32_t code_point) {
    if (code_point < 0x10000) {
        out.push_back(static_cast<wchar_t>(code_point));
    } else {
        const char32_t offset = code_point - 0x10000;
        out.push_back(static_cast<wchar_t>(0xD800 + (offset >> 10)));
        out.push_back(static_cast<wchar_t>(0xDC00 + (offset & 0x3FF)));
    }
}

void AppendUtf8(std::string& out, char32_t code_point) {
    if (code_point < 0x80) {
        out.push_back(static_cast<char>(code_point));
    } else if (code_point < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (code_point >> 6)));
        out.push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
    } else if (code_point < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (code_point >> 12)));
        out.push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (code_point >> 18)));
        out.push_back(static_cast<char>(0x80 | ((code_point >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
    }
}

}  // namespace

std::optional<std::wstring> Utf8ToUtf16Strict(std::string_view text) {
    std::wstring out;
    out.reserve(text.size());
    std::size_t i = 0;
    while (i < text.size()) {
        const auto lead = static_cast<std::uint8_t>(text[i]);
        if (lead == 0) return std::nullopt;
        std::size_t length;
        char32_t code_point;
        char32_t minimum;
        if (lead < 0x80) {
            length = 1, code_point = lead, minimum = 0;
        } else if ((lead & 0xE0) == 0xC0) {
            length = 2, code_point = lead & 0x1F, minimum = 0x80;
        } else if ((lead & 0xF0) == 0xE0) {
            length = 3, code_point = lead & 0x0F, minimum = 0x800;
        } else if ((lead & 0xF8) == 0xF0) {
            length = 4, code_point = lead & 0x07, minimum = 0x10000;
        } else {
            return std::nullopt;  // continuation byte or 5/6-byte lead
        }
        if (length > text.size() - i) return std::nullopt;
        for (std::size_t k = 1; k < length; ++k) {
            const auto next = static_cast<std::uint8_t>(text[i + k]);
            if ((next & 0xC0) != 0x80) return std::nullopt;
            code_point = (code_point << 6) | (next & 0x3F);
        }
        if (code_point < minimum || code_point > 0x10FFFF || IsHighSurrogate(code_point) ||
            IsLowSurrogate(code_point))
            return std::nullopt;
        AppendUtf16(out, code_point);
        i += length;
    }
    return out;
}

std::string Utf16ToUtf8Lossy(std::wstring_view text) {
    std::string out;
    out.reserve(text.size() * 3);
    for (std::size_t i = 0; i < text.size(); ++i) {
        const char32_t unit = static_cast<char16_t>(text[i]);
        if (IsHighSurrogate(unit) && i + 1 < text.size() &&
            IsLowSurrogate(static_cast<char16_t>(text[i + 1]))) {
            const char32_t low = static_cast<char16_t>(text[++i]);
            AppendUtf8(out, 0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00));
        } else if (IsHighSurrogate(unit) || IsLowSurrogate(unit)) {
            AppendUtf8(out, kReplacement);
        } else {
            AppendUtf8(out, unit);
        }
    }
    return out;
}

bool IsValidUtf16(std::wstring_view text) noexcept {
    for (std::size_t i = 0; i < text.size(); ++i) {
        const char32_t unit = static_cast<char16_t>(text[i]);
        if (IsHighSurrogate(unit)) {
            if (i + 1 >= text.size() || !IsLowSurrogate(static_cast<char16_t>(text[i + 1])))
                return false;
            ++i;
        } else if (IsLowSurrogate(unit)) {
            return false;
        }
    }
    return true;
}

}  // namespace syncfl::midi
