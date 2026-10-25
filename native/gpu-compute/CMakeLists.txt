# gb_gpu_compute (component gpu-compute, INTERFACES section 11): one CMake project for both the Android (NDK) build
# and the glibc host test build. llama.cpp (pinned tree, see LLAMA_PIN) is built as static libraries and linked into
# libgb_gpu_compute.so; its Vulkan backend links against the private loader stub libgb_nvk_loader.so, never against
# libvulkan.so.
#
#   -DGB_LLAMA_DIR=<pinned llama.cpp checkout>   (required)
#   -DGB_VK_INC=<dir with vulkan/ vk_video/ spirv/> (required; the same headers for both builds, scripts/15_headers.sh)
#   -DGB_COMPUTE_TESTING=ON                       host test build: GB_COMPUTE_* env knobs, test-backend-ops, test_loader
#   -DGB_LLAMA_TAG=<tag>                          recorded in --version and the log
cmake_minimum_required(VERSION 3.22)
project(gb_gpu_compute C CXX)

set(GB_LLAMA_DIR "" CACHE PATH "pinned llama.cpp tree")
set(GB_VK_INC "" CACHE PATH "Vulkan-Headers + SPIRV-Headers include dir")
set(GB_LLAMA_TAG "unknown" CACHE STRING "llama.cpp release tag")
option(GB_COMPUTE_TESTING "host test build" OFF)
if (NOT EXISTS "${GB_LLAMA_DIR}/include/llama.h")
    message(FATAL_ERROR "GB_LLAMA_DIR=${GB_LLAMA_DIR} is not a llama.cpp tree")
endif()
if (NOT EXISTS "${GB_VK_INC}/vulkan/vulkan_core.h" OR NOT EXISTS "${GB_VK_INC}/vulkan/vulkan.hpp"
    OR NOT EXISTS "${GB_VK_INC}/spirv/unified1/spirv.hpp")
    message(FATAL_ERROR "GB_VK_INC=${GB_VK_INC} lacks vulkan_core.h / vulkan.hpp / spirv.hpp")
endif()

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Threads REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(GB_SYS_LIBS ${CMAKE_DL_LIBS} Threads::Threads)
if (ANDROID)
    list(APPEND GB_SYS_LIBS log)
endif()

# ---------------------------------------------------------------------------------------------- loader stub
set(GB_TRAMP ${CMAKE_CURRENT_BINARY_DIR}/gb_vk_trampolines.inc)
set(GB_NAMES ${CMAKE_CURRENT_BINARY_DIR}/gb_vk_core_names.txt)
add_custom_command(
    OUTPUT ${GB_TRAMP} ${GB_NAMES}
    COMMAND ${Python3_EXECUTABLE} -I ${CMAKE_CURRENT_SOURCE_DIR}/loader/gen_trampolines.py
            ${GB_VK_INC}/vulkan/vulkan_core.h ${GB_TRAMP} ${GB_NAMES}
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/loader/gen_trampolines.py ${GB_VK_INC}/vulkan/vulkan_core.h
    COMMENT "Generate Vulkan 1.3 core trampolines")
add_library(gb_nvk_loader SHARED loader/libgb_nvk_loader.c ${GB_TRAMP})
set_target_properties(gb_nvk_loader PROPERTIES OUTPUT_NAME gb_nvk_loader C_VISIBILITY_PRESET hidden
                      LINK_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/loader/libgb_nvk_loader.map)
target_include_directories(gb_nvk_loader PRIVATE ${CMAKE_CURRENT_BINARY_DIR} PUBLIC ${GB_VK_INC})
target_compile_options(gb_nvk_loader PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter)
target_link_options(gb_nvk_loader PRIVATE
    -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/loader/libgb_nvk_loader.map -Wl,--no-undefined
    -Wl,-soname,libgb_nvk_loader.so)
target_link_libraries(gb_nvk_loader PRIVATE ${GB_SYS_LIBS})
# ggml-vulkan's find_package(Vulkan) finds this alias instead of creating an imported libvulkan target.
add_library(Vulkan::Vulkan ALIAS gb_nvk_loader)

# ---------------------------------------------------------------------------------------------- llama.cpp (static)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN ON CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)          # libomp.so is not LLNDK
set(GGML_BACKEND_DL OFF CACHE BOOL "" FORCE)
set(GGML_CCACHE OFF CACHE BOOL "" FORCE)
set(GGML_SSE42 ON CACHE BOOL "" FORCE)            # i7-1065G7 (Ice Lake): device and builder CPU
set(GGML_AVX ON CACHE BOOL "" FORCE)
set(GGML_AVX2 ON CACHE BOOL "" FORCE)
set(GGML_BMI2 ON CACHE BOOL "" FORCE)
set(GGML_FMA ON CACHE BOOL "" FORCE)
set(GGML_F16C ON CACHE BOOL "" FORCE)
set(GGML_AVX512 OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
add_subdirectory(${GB_LLAMA_DIR} llama EXCLUDE_FROM_ALL)

# ---------------------------------------------------------------------------------------------- service library
# (target gb_gpu_compute_lib -> libgb_gpu_compute.so; the launcher executable is the file gb_gpu_compute)
add_library(gb_gpu_compute_lib SHARED service/gb_compute.cpp service/gb_llm.cpp)
set_target_properties(gb_gpu_compute_lib PROPERTIES OUTPUT_NAME gb_gpu_compute CXX_VISIBILITY_PRESET hidden
                      VISIBILITY_INLINES_HIDDEN ON INSTALL_RPATH "\$ORIGIN" BUILD_WITH_INSTALL_RPATH ON
                      LINK_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/service/gb_compute.map)
target_include_directories(gb_gpu_compute_lib PRIVATE ${GB_LLAMA_DIR}/vendor)
target_compile_definitions(gb_gpu_compute_lib PRIVATE GB_LLAMA_TAG="${GB_LLAMA_TAG}")
target_compile_options(gb_gpu_compute_lib PRIVATE -Wall -Wextra -Wno-unused-parameter)
target_link_options(gb_gpu_compute_lib PRIVATE
    -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/service/gb_compute.map -Wl,--no-undefined
    -Wl,--exclude-libs,ALL -Wl,-soname,libgb_gpu_compute.so)
target_link_libraries(gb_gpu_compute_lib PRIVATE llama ggml ${GB_SYS_LIBS})
if (ANDROID)
    # CMake's Android platform module emits no rpath at all; the payload needs DT_RUNPATH $ORIGIN (INTERFACES 5.3)
    target_link_options(gb_gpu_compute_lib PRIVATE "LINKER:-rpath,\$ORIGIN" "LINKER:--enable-new-dtags")
endif()

# ---------------------------------------------------------------------------------------------- launcher
add_executable(gb_gpu_compute_launcher launcher/gb_gpu_compute.c)
set_target_properties(gb_gpu_compute_launcher PROPERTIES OUTPUT_NAME gb_gpu_compute)
target_compile_options(gb_gpu_compute_launcher PRIVATE -Wall -Wextra -Werror)
target_link_libraries(gb_gpu_compute_launcher PRIVATE ${GB_SYS_LIBS})

if (GB_COMPUTE_TESTING)
    target_compile_definitions(gb_gpu_compute_lib PRIVATE GB_COMPUTE_TESTING=1)
    target_compile_definitions(gb_gpu_compute_launcher PRIVATE GB_COMPUTE_TESTING=1)
    # llama.cpp's backend test, linked to ggml (and thus to the stub) instead of libvulkan
    add_executable(test-backend-ops ${GB_LLAMA_DIR}/tests/test-backend-ops.cpp)
    target_link_libraries(test-backend-ops PRIVATE ggml Threads::Threads)
    add_executable(test_loader test/test_loader.c)
    target_include_directories(test_loader PRIVATE ${GB_VK_INC})
    target_compile_options(test_loader PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(test_loader PRIVATE gb_nvk_loader ${GB_SYS_LIBS})
endif()

# probe harness for the bionic chroot / drm-shim smoke test (GC-09): dlopen()s a given libgb_gpu_compute.so
add_executable(gc_probe test/gc_probe.c)
target_link_libraries(gc_probe PRIVATE ${GB_SYS_LIBS})
