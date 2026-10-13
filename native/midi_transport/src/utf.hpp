#pragma once
// Strict, platform-independent UTF conversions. Requested names must round-trip
// exactly, so decoding rejects anything that is not well-formed UTF-8 instead of
// relying on MultiByteToWideChar's version-dependent leniency.

#include <optional>
#include <string>
#include <string_view>

namespace syncfl::midi {

// Returns nullopt for malformed UTF-8: invalid lead/continuation bytes, truncated
// sequences, overlong forms, encoded surrogates (U+D800..U+DFFF), values > U+10FFFF
// and embedded NUL.
std::optional<std::wstring> Utf8ToUtf16Strict(std::string_view text);

// Lone surrogates become U+FFFD, so the result is always valid UTF-8.
std::string Utf16ToUtf8Lossy(std::wstring_view text);

// True when every code unit forms a valid UTF-16 sequence.
bool IsValidUtf16(std::wstring_view text) noexcept;

}  // namespace syncfl::midi
