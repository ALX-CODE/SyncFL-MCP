# An invalid zero target produces structured output without contacting a window.
execute_process(COMMAND "${BRIDGE}" --pid 0 --hwnd 0
  RESULT_VARIABLE result OUTPUT_VARIABLE output ERROR_VARIABLE errors)
if(NOT result EQUAL 3)
  message(FATAL_ERROR "Expected a rejected zero target: ${result}; ${errors}")
endif()
string(JSON skipped GET "${output}" over_skipped)
string(JSON over_hr GET "${output}" over_hr)
string(JSON state GET "${output}" state)
if(NOT skipped OR NOT over_hr EQUAL 1 OR NOT state STREQUAL "rejected")
  message(FATAL_ERROR "Expected explicit skipped DragOver sentinel: ${output}")
endif()
