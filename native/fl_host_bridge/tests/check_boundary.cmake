# Check both direct imports and the original code for desktop-input/injection paths.
execute_process(COMMAND "${DUMPBIN}" /nologo /imports "${BINARY}"
  RESULT_VARIABLE result OUTPUT_VARIABLE imports ERROR_VARIABLE failure)
if(NOT result EQUAL 0)
  message(FATAL_ERROR "Cannot inspect plugin imports: ${failure}")
endif()
set(forbidden
  "SendInput|mouse_event|keybd_event|SetForegroundWindow|SetFocus|SetCursorPos|AttachThreadInput|SendMessage|PostMessage|SetWindowsHookEx|WriteProcessMemory|VirtualAllocEx|CreateRemoteThread")
if(imports MATCHES "${forbidden}")
  message(FATAL_ERROR "Unexpected desktop-input or injected-call dependency")
endif()
foreach(source IN ITEMS src/plugin.cpp src/ipc.cpp src/cli.cpp)
  file(READ "${SOURCE_ROOT}/${source}" content)
  # MSVC's static CRT itself imports LoadLibraryExW/GetProcAddress for Windows
  # compatibility. Our original code may not use them to bypass the API list.
  if(content MATCHES "${forbidden}|LoadLibrary|GetProcAddress")
    message(FATAL_ERROR "Unexpected desktop-input or injected-call path: ${source}")
  endif()
endforeach()
message(STATUS "No desktop-input/injection imports or dynamic calls in original source")
