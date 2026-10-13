# Runs EXE with ARGS ("|"-separated) and requires the exact exit code EXPECTED.
string(REPLACE "|" ";" arguments "${ARGS}")
execute_process(COMMAND "${EXE}" ${arguments}
  RESULT_VARIABLE code OUTPUT_VARIABLE out ERROR_VARIABLE err)
if(NOT code STREQUAL "${EXPECTED}")
  message(FATAL_ERROR "exit ${code}, expected ${EXPECTED}\nstdout: ${out}\nstderr: ${err}")
endif()
message(STATUS "exit ${code} as expected\n${out}${err}")
