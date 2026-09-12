cmake_minimum_required(VERSION 3.13)
project(pokerfast_equity CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# OMPEval is a git submodule, not vendored source. `build.sh` initialises it
# and applies native/patches/ before configuring; if you ran cmake by hand and
# it is missing, that is what this is telling you.
set(OMP_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../vendor/OMPEval)
if(NOT EXISTS ${OMP_DIR}/omp/HandEvaluator.cpp)
  message(FATAL_ERROR
    "vendor/OMPEval is empty. Run:\n"
    "  git submodule update --init --recursive\n"
    "or use native/build.sh, which does that and applies the patches.")
endif()

add_executable(ompeval_batch
  equity_batch.cpp
  ${OMP_DIR}/omp/CardRange.cpp
  ${OMP_DIR}/omp/CombinedRange.cpp
  ${OMP_DIR}/omp/EquityCalculator.cpp
  ${OMP_DIR}/omp/HandEvaluator.cpp
)

target_include_directories(ompeval_batch PRIVATE ${OMP_DIR})

find_package(Threads REQUIRED)
target_link_libraries(ompeval_batch PRIVATE Threads::Threads)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(ompeval_batch PRIVATE -O2)
  # A static link makes the binary portable to a machine without the toolchain,
  # which matters because callers spawn it as a subprocess.
  if(NOT APPLE)
    target_link_options(ompeval_batch PRIVATE -static)
  endif()
endif()

install(TARGETS ompeval_batch RUNTIME DESTINATION bin)
