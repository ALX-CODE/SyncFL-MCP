/*
 * SyncFL native MIDI output transport — versioned C ABI.
 *
 * Scope: enumerate WinMM MIDI output names, open ONE output selected by exact
 * name, send the controller wakeup (channel 0 note-on, note 127, velocity 127),
 * and close the session. Nothing else is ever transmitted. Mailbox files,
 * request IDs, expiry, locking and response validation stay in the Python
 * connection layer.
 *
 * Types and encoding
 *   - Every string crossing this ABI is NUL-terminated UTF-8. Input names must be
 *     valid UTF-8 (no overlong forms, no encoded surrogates) and 1..31 UTF-16 code
 *     units long: WinMM product names are truncated to 31 characters, so a longer
 *     request can never match and is rejected as an invalid argument.
 *   - Status codes are int32_t; SYNCFL_MIDI_OK is 0, every failure is positive.
 *   - syncfl_midi_session is an opaque 64-bit token, not a pointer. 0 is never a
 *     valid session. Tokens are never reused within a process, so a stale or
 *     forged token is reported as SYNCFL_MIDI_E_INVALID_HANDLE instead of crashing.
 *
 * Buffer ownership
 *   - The caller owns every buffer. The library never allocates memory for the
 *     caller and never retains a caller pointer after a call returns.
 *   - Buffer-filling calls take (buffer, capacity, required). *required receives
 *     the byte count needed including terminators. If capacity is too small the
 *     call returns SYNCFL_MIDI_E_BUFFER_TOO_SMALL and leaves the buffer untouched.
 *     (buffer = NULL, capacity = 0) is a valid size query.
 *   - syncfl_midi_status_name returns a pointer to a static string owned by the
 *     library; it stays valid for as long as the library is loaded.
 *
 * Name matching
 *   - Default (SYNCFL_MIDI_OPEN_EXACT): an output matches only when its full WinMM
 *     product name equals the request code unit for code unit. No case folding,
 *     Unicode normalisation, trimming, prefix or fuzzy matching.
 *   - SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX (explicit caller choice): an output
 *     named "<request> <ASCII digits>" also matches, e.g. "ALX FL Bridge (Send) 2".
 *   - Exactly one output must match. Zero is SYNCFL_MIDI_E_NOT_FOUND, more than
 *     one is SYNCFL_MIDI_E_AMBIGUOUS. There is no "first device" fallback.
 *   - Opening rechecks identity: after midiOutOpen the library reads the opened
 *     device's capabilities through the handle and re-enumerates. If the device
 *     changed or the name became ambiguous (hot-plug), the handle is closed and
 *     SYNCFL_MIDI_E_ENDPOINT_CHANGED is returned.
 *
 * Delivery
 *   - syncfl_midi_send_wakeup calls midiOutShortMsg exactly once with 0x007F7F90
 *     and never retries, even on failure. *delivery_attempted reports whether that
 *     call was made. A successful return means WinMM accepted the message; it is
 *     NOT evidence that FL Studio received or executed anything.
 *   - Opening and closing transmit nothing (midiOutReset, which emits note-off
 *     traffic, is never called).
 *
 * Threading and lifetime
 *   - Every function is thread-safe. Operations on one session are serialised;
 *     closing waits for an in-flight send on that session to return. After
 *     syncfl_midi_close returns (with any status) the token is invalid.
 *   - The last error is per thread. Every function except the four accessors
 *     (abi_version, status_name, last_error, last_status) replaces it, and success
 *     clears it, so read it on the same thread directly after the failing call.
 *   - No function calls back into caller code. No exception crosses this ABI.
 *   - Close every session before unloading the library. Sessions still open at
 *     unload are not closed (closing WinMM handles under the loader lock is
 *     unsafe); the operating system releases them when the process exits.
 */
#ifndef SYNCFL_MIDI_TRANSPORT_H
#define SYNCFL_MIDI_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#if defined(SYNCFL_MIDI_STATIC)
#define SYNCFL_MIDI_API
#elif defined(SYNCFL_MIDI_BUILDING_DLL)
#define SYNCFL_MIDI_API __declspec(dllexport)
#else
#define SYNCFL_MIDI_API __declspec(dllimport)
#endif
#define SYNCFL_MIDI_CALL __cdecl

#ifdef __cplusplus
extern "C" {
#endif

/* ABI version: (major << 16) | minor. Major changes are incompatible. */
#define SYNCFL_MIDI_ABI_VERSION_MAJOR 1u
#define SYNCFL_MIDI_ABI_VERSION_MINOR 0u
#define SYNCFL_MIDI_ABI_VERSION \
    ((SYNCFL_MIDI_ABI_VERSION_MAJOR << 16) | SYNCFL_MIDI_ABI_VERSION_MINOR)

typedef uint64_t syncfl_midi_session;

/* Status codes. Values are part of the ABI and never change meaning. */
#define SYNCFL_MIDI_OK 0
#define SYNCFL_MIDI_E_INVALID_ARGUMENT 1 /* NULL pointer, bad flags, invalid or over-long name */
#define SYNCFL_MIDI_E_BUFFER_TOO_SMALL 2 /* *required holds the needed byte count */
#define SYNCFL_MIDI_E_NOT_FOUND 3        /* no output matches the requested name */
#define SYNCFL_MIDI_E_AMBIGUOUS 4        /* more than one output matches */
#define SYNCFL_MIDI_E_ENDPOINT_CHANGED 5 /* identity changed during open; nothing was sent */
#define SYNCFL_MIDI_E_INVALID_HANDLE 6   /* unknown, stale or already-closed session */
#define SYNCFL_MIDI_E_BUSY 7             /* WinMM reports the output is allocated elsewhere */
#define SYNCFL_MIDI_E_SEND_FAILED 8      /* midiOutShortMsg failed; delivery outcome unknown */
#define SYNCFL_MIDI_E_NATIVE 9           /* other WinMM failure; see the native code */
#define SYNCFL_MIDI_E_OUT_OF_MEMORY 10
#define SYNCFL_MIDI_E_DEVICE_SET_CHANGED 11 /* devices kept changing while enumerating */
#define SYNCFL_MIDI_E_INTERNAL 12           /* unexpected internal failure */

/* Open flags. Unknown bits are rejected with SYNCFL_MIDI_E_INVALID_ARGUMENT. */
#define SYNCFL_MIDI_OPEN_EXACT 0u
#define SYNCFL_MIDI_OPEN_ALLOW_DECIMAL_SUFFIX 1u

/* Returns SYNCFL_MIDI_ABI_VERSION of the loaded library. */
SYNCFL_MIDI_API uint32_t SYNCFL_MIDI_CALL syncfl_midi_abi_version(void);

/* Static, never NULL. Unknown codes map to "SYNCFL_MIDI_E_UNKNOWN". */
SYNCFL_MIDI_API const char* SYNCFL_MIDI_CALL syncfl_midi_status_name(int32_t status);

/*
 * Snapshot of WinMM output names in device order: *count UTF-8 strings, each
 * NUL-terminated, followed by one extra NUL. Parse exactly *count strings (a name
 * may be empty). Names that are not valid UTF-16 are reported with U+FFFD and can
 * never be opened. required and count must not be NULL; *count is set even when
 * the buffer is too small. The device set can change between calls.
 */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_list_outputs(
    char* buffer, size_t capacity, size_t* required, uint32_t* count);

/*
 * Applies the open-time matching rules without opening anything. On success the
 * matched WinMM name is copied to (matched, capacity, required) when required is
 * not NULL; pass (NULL, 0, NULL) to receive only the status. A matched buffer
 * without a required pointer is an invalid argument.
 */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_resolve_output(
    const char* name, uint32_t flags, char* matched, size_t capacity, size_t* required);

/* Opens the single matching output. *session is 0 on failure. */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_open_output(
    const char* name, uint32_t flags, syncfl_midi_session* session);

/* The actual WinMM name of the opened output (may carry the decimal suffix). */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_session_name(
    syncfl_midi_session session, char* buffer, size_t capacity, size_t* required);

/*
 * Sends one wakeup. delivery_attempted must not be NULL; it is set to 1 only if
 * midiOutShortMsg was called (success or SYNCFL_MIDI_E_SEND_FAILED), else 0.
 */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_send_wakeup(
    syncfl_midi_session session, uint32_t* delivery_attempted);

/*
 * Closes the session. The token becomes invalid even if WinMM reports a close
 * error (returned as SYNCFL_MIDI_E_NATIVE). A second close returns
 * SYNCFL_MIDI_E_INVALID_HANDLE and touches nothing.
 */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_close(syncfl_midi_session session);

/*
 * Copies this thread's last error: *native_code receives the WinMM MMRESULT (0 if
 * none) and the buffer an English UTF-8 message. Returns SYNCFL_MIDI_OK, or
 * SYNCFL_MIDI_E_BUFFER_TOO_SMALL / SYNCFL_MIDI_E_INVALID_ARGUMENT about the copy
 * itself. Never modifies the stored error. native_code and required may be NULL.
 */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_last_error(
    uint32_t* native_code, char* buffer, size_t capacity, size_t* required);

/* The status that produced this thread's last error (SYNCFL_MIDI_OK if none). */
SYNCFL_MIDI_API int32_t SYNCFL_MIDI_CALL syncfl_midi_last_status(void);

#ifdef __cplusplus
}
#endif

#endif /* SYNCFL_MIDI_TRANSPORT_H */
