cmake_minimum_required(VERSION 3.15)
project(pokerfast_omp CXX)

# Builds `pokerfast_omp`, a shared library with a plain C ABI over OMPEval,
# loaded by pokerfast.equity through ctypes. Driven by scikit-build-core when
# building a wheel (pip / python -m build / cibuildwheel), or by hand:
#
#   cmake -S native -B native/build && cmake --build native/build --config Release
#
# then point POKERFAST_OMPEVAL_LIB at the library, or copy it into
# src/pokerfast/_native/.

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

# ---------------------------------------------------------------- OMPEval
# A git submodule. The sdist carries its files, so a build from the sdist does
# not need git.
set(OMP_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../vendor/OMPEval)
if(NOT EXISTS ${OMP_SRC}/omp/HandEvaluator.cpp)
  message(FATAL_ERROR
    "vendor/OMPEval is empty. Run:\n"
    "  git submodule update --init --recursive")
endif()

# The sources are COPIED into the build tree and patched there, so the
# submodule working tree is never modified and a build from an sdist (no git)
# patches the same way. omp/ includes its siblings by relative path, so the
# patched Random.h must sit next to the other headers: hence the whole copy.
set(OMP_DIR ${CMAKE_CURRENT_BINARY_DIR}/ompeval)
file(COPY ${OMP_SRC}/omp ${OMP_SRC}/libdivide DESTINATION ${OMP_DIR})

# Patch: Random.h's min()/max() must be constexpr, which newer C++ standard
# libraries require of a UniformRandomBitGenerator (MSVC and libc++ reject it
# otherwise). Every anchor is asserted: an upstream change that moves them
# fails the build here instead of compiling the unpatched text.
function(pf_patch file)
  file(READ ${file} text)
  math(EXPR last "${ARGC} - 1")
  foreach(i RANGE 1 ${last} 2)
    math(EXPR j "${i} + 1")
    set(old "${ARGV${i}}")
    set(new "${ARGV${j}}")
    string(FIND "${text}" "${old}" pos)
    if(pos EQUAL -1)
      message(FATAL_ERROR "patch anchor not found in ${file}: ${old}")
    endif()
    string(REPLACE "${old}" "${new}" text "${text}")
  endforeach()
  file(WRITE ${file} "${text}")
endfunction()

pf_patch(${OMP_DIR}/omp/Random.h
  "    static uint64_t min()" "    constexpr static uint64_t min()"
  "    static uint64_t max()" "    constexpr static uint64_t max()")

# Patch: libdivide takes `_WIN64` to mean x86-64, but Windows on ARM64 defines
# it too, and the x86 inline assembly that follows fails to compile there.
# Detect x86-64 by the architecture macros alone; ARM64 then takes libdivide's
# portable C path.
pf_patch(${OMP_DIR}/libdivide/libdivide.h
  "#if defined(__x86_64__) || defined(_WIN64) || defined(_M_X64)"
  "#if defined(__x86_64__) || defined(_M_X64)")

# ---------------------------------------------------------------- library
add_library(pokerfast_omp SHARED
  pokerfast_omp.cpp
  ${OMP_DIR}/omp/CardRange.cpp
  ${OMP_DIR}/omp/CombinedRange.cpp
  ${OMP_DIR}/omp/EquityCalculator.cpp
  ${OMP_DIR}/omp/HandEvaluator.cpp
)
target_include_directories(pokerfast_omp PRIVATE ${OMP_DIR})

find_package(Threads REQUIRED)
target_link_libraries(pokerfast_omp PRIVATE Threads::Threads)

# Load it by one fixed name on every platform: no "lib" prefix, so ctypes
# looks for pokerfast_omp.{dll,so,dylib}.
set_target_properties(pokerfast_omp PROPERTIES PREFIX "")

if(MSVC)
  target_compile_options(pokerfast_omp PRIVATE /O2 /EHsc)
elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(pokerfast_omp PRIVATE -O2)
  if(MINGW)
    # MinGW's libstdc++/libc++ and winpthread are not on a user's PATH; link
    # them in so the DLL depends only on system DLLs.
    target_link_options(pokerfast_omp PRIVATE -static)
  endif()
endif()

# Baseline ISA only: x86-64 builds get SSE2 (OMPEval's fast path) because
# every x86-64 CPU has it; SSE4.1/AVX are deliberately NOT enabled, since a
# wheel must run on any CPU of its architecture. ARM builds use the portable
# scalar path.

# scikit-build-core installs relative to the wheel's platlib root.
install(TARGETS pokerfast_omp
  LIBRARY DESTINATION pokerfast/_native
  RUNTIME DESTINATION pokerfast/_native)
