cmake_minimum_required(VERSION 3.24)
project(ALXBackgroundBridge LANGUAGES CXX)
if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8 OR NOT MSVC)
  message(FATAL_ERROR "Experimental bridge requires Windows x64 and MSVC")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
add_compile_definitions(UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
add_compile_options(/W4 /WX /permissive- /EHsc /sdl /guard:cf)
add_link_options(/DYNAMICBASE /NXCOMPAT /guard:cf)
add_library(alx-background-hook SHARED hook.cpp)
target_link_libraries(alx-background-hook PRIVATE ole32 shell32 user32)
add_library(background-client STATIC client.cpp)
target_link_libraries(background-client PUBLIC advapi32 bcrypt user32)
add_executable(alx-background-bridge main.cpp)
target_link_libraries(alx-background-bridge PRIVATE background-client)
add_executable(background-selftest selftest.cpp)
target_link_libraries(background-selftest PRIVATE background-client ole32 shell32)
enable_testing()
add_test(NAME background_own_host COMMAND background-selftest)
set_tests_properties(background_own_host PROPERTIES TIMEOUT 30 SKIP_RETURN_CODE 77)
add_test(NAME background_reject_arguments COMMAND alx-background-bridge --drop)
set_tests_properties(background_reject_arguments PROPERTIES WILL_FAIL TRUE)
add_test(NAME background_skipped_over_result
  COMMAND "${CMAKE_COMMAND}"
    "-DBRIDGE=$<TARGET_FILE:alx-background-bridge>"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/check_skipped_over.cmake")
get_filename_component(msvc_bin "${CMAKE_LINKER}" DIRECTORY)
add_test(NAME background_no_input_imports
  COMMAND "${CMAKE_COMMAND}"
    "-DDUMPBIN=${msvc_bin}/dumpbin.exe"
    "-DCLIENT=$<TARGET_FILE:alx-background-bridge>"
    "-DHOOK=$<TARGET_FILE:alx-background-hook>"
    "-DSOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/check_no_input.cmake")
