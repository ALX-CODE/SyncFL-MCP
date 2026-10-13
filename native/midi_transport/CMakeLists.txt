cmake_minimum_required(VERSION 3.24)
project(SyncFLMidiTransport VERSION 0.1.0 LANGUAGES CXX)
if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "The SyncFL MIDI transport requires Windows x64")
endif()
if(NOT MSVC)
  message(FATAL_ERROR "Build with the MSVC toolset; the warning and hardening flags are MSVC flags")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
# Static CRT, like the other native components: no VC++ redistributable needed.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
enable_testing()

# Warnings are errors; CFG, CET, ASLR, DEP and /GS hardening; deterministic output.
# /Z7 keeps debug info in the objects, and /PDBALTPATH (set per target below)
# records only the PDB file name, so binaries do not embed the builder's directories.
add_library(syncfl_midi_build_flags INTERFACE)
target_compile_definitions(syncfl_midi_build_flags INTERFACE
  UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
target_compile_options(syncfl_midi_build_flags INTERFACE
  /W4 /WX /permissive- /utf-8 /EHsc /Zc:__cplusplus /Zc:inline /sdl /GS /guard:cf /Brepro /Z7)
target_link_options(syncfl_midi_build_flags INTERFACE
  /WX /DYNAMICBASE /HIGHENTROPYVA /NXCOMPAT /guard:cf /CETCOMPAT
  /DEBUG /OPT:REF /OPT:ICF /INCREMENTAL:NO /Brepro)
# Import search for each module's static imports: the DLL needs only System32
# (winmm, kernel32); executables also load the transport DLL beside them.
set(SYNCFL_DLL_IMPORT_SEARCH /DEPENDENTLOADFLAG:0x800)   # SYSTEM32
set(SYNCFL_EXE_IMPORT_SEARCH /DEPENDENTLOADFLAG:0xA00)   # APPLICATION_DIR | SYSTEM32
# %_PDB% is mangled by the Visual Studio generator's escaping, so name the PDB
# explicitly for each linked target.
function(syncfl_pdb_name_only target)
  target_link_options(${target} PRIVATE "/PDBALTPATH:$<TARGET_FILE_BASE_NAME:${target}>.pdb")
endfunction()

# Core implementation, linked unchanged into the DLL and the unit tests.
add_library(syncfl_midi_core STATIC src/utf.cpp src/transport.cpp src/winmm_api.cpp)
target_include_directories(syncfl_midi_core PUBLIC src include)
target_link_libraries(syncfl_midi_core PUBLIC syncfl_midi_build_flags winmm)

add_library(syncfl_midi_transport SHARED src/abi.cpp)
target_compile_definitions(syncfl_midi_transport PRIVATE SYNCFL_MIDI_BUILDING_DLL)
target_link_libraries(syncfl_midi_transport PRIVATE syncfl_midi_core)
target_include_directories(syncfl_midi_transport PUBLIC include)
target_link_options(syncfl_midi_transport PRIVATE ${SYNCFL_DLL_IMPORT_SEARCH})

# Diagnostic CLI; uses the DLL through its exported C ABI only.
add_executable(syncfl-midi cli/main.cpp)
target_link_libraries(syncfl-midi PRIVATE syncfl_midi_transport syncfl_midi_build_flags)
target_link_options(syncfl-midi PRIVATE ${SYNCFL_EXE_IMPORT_SEARCH})
target_compile_definitions(syncfl-midi PRIVATE "SYNCFL_MIDI_VERSION=\"${PROJECT_VERSION}\"")

# Unit tests: the same core plus the ABI shim compiled with the fake-WinMM seam.
add_executable(syncfl_midi_unit_tests tests/unit_tests.cpp src/abi.cpp)
target_compile_definitions(syncfl_midi_unit_tests PRIVATE SYNCFL_MIDI_STATIC SYNCFL_MIDI_TEST_SEAM)
target_link_libraries(syncfl_midi_unit_tests PRIVATE syncfl_midi_core)
target_link_options(syncfl_midi_unit_tests PRIVATE ${SYNCFL_DLL_IMPORT_SEARCH})

# Smoke test of the shipped DLL against real WinMM. It only enumerates and
# resolves names that cannot exist; it never opens an endpoint.
add_executable(syncfl_midi_abi_smoke tests/abi_smoke.cpp)
target_link_libraries(syncfl_midi_abi_smoke PRIVATE syncfl_midi_transport syncfl_midi_build_flags)
target_link_options(syncfl_midi_abi_smoke PRIVATE ${SYNCFL_EXE_IMPORT_SEARCH})

foreach(linked IN ITEMS syncfl_midi_transport syncfl-midi syncfl_midi_unit_tests syncfl_midi_abi_smoke)
  syncfl_pdb_name_only(${linked})
endforeach()

add_test(NAME unit_tests COMMAND syncfl_midi_unit_tests)
add_test(NAME abi_smoke COMMAND syncfl_midi_abi_smoke)

set(ABSENT_NAME "SyncFL CTest absent 7f3c")  # <= 31 characters, the WinMM limit
function(syncfl_cli_test name expected_exit)
  list(JOIN ARGN "|" joined)
  add_test(NAME ${name} COMMAND ${CMAKE_COMMAND}
    "-DEXE=$<TARGET_FILE:syncfl-midi>" "-DEXPECTED=${expected_exit}" "-DARGS=${joined}"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/expect_exit.cmake")
endfunction()
syncfl_cli_test(cli_version 0 version)
syncfl_cli_test(cli_list_is_read_only 0 list --json)
syncfl_cli_test(cli_resolve_absent_is_not_found 3 resolve "${ABSENT_NAME}")
syncfl_cli_test(cli_wakeup_requires_confirmation 64 wakeup "${ABSENT_NAME}")
syncfl_cli_test(cli_wakeup_absent_is_not_found 3 wakeup "${ABSENT_NAME}" --confirm-send)
syncfl_cli_test(cli_rejects_unknown_command 64 frobnicate)
syncfl_cli_test(cli_rejects_unknown_option 64 resolve "${ABSENT_NAME}" --fuzzy)
syncfl_cli_test(cli_rejects_names_over_the_winmm_limit 1 resolve "SyncFL name longer than WinMM allows")

add_test(NAME binaries_embed_no_build_paths COMMAND ${CMAKE_COMMAND}
  "-DFILES=$<TARGET_FILE:syncfl_midi_transport>|$<TARGET_FILE:syncfl-midi>"
  "-DNEEDLES=${CMAKE_SOURCE_DIR}|${CMAKE_BINARY_DIR}|$ENV{USERPROFILE}|$ENV{USERNAME}"
  -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_no_build_paths.cmake")
