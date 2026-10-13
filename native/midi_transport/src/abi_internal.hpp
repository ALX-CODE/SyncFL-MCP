#pragma once
// Internals shared by abi.cpp and the unit tests: the exception firewall and,
// in test builds only, the hook that swaps the WinMM binding.

#include "transport.hpp"

#include <cstdint>
#include <new>

namespace syncfl::midi::abi {

// Records the result as this thread's last error and returns its status code.
std::int32_t Store(const Result& result) noexcept;

// Runs body (returning Result) so that no exception can escape: bad_alloc maps to
// OutOfMemory and anything else to Internal.
template <class Body>
std::int32_t Guarded(Body&& body) noexcept {
    try {
        return Store(body());
    } catch (const std::bad_alloc&) {
        return Store(Result{Status::OutOfMemory, 0, "out of memory"});
    } catch (...) {
        try {
            return Store(Result{Status::Internal, 0, "unexpected internal failure"});
        } catch (...) {
            return static_cast<std::int32_t>(Status::Internal);
        }
    }
}

#if defined(SYNCFL_MIDI_TEST_SEAM)
// Replaces the process transport with a fresh one bound to api (nullptr restores
// the real WinMM binding). Test builds only; never compiled into the DLL.
void UseApiForTesting(MidiOutputApi* api);
#endif

}  // namespace syncfl::midi::abi
