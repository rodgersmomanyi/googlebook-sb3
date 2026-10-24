import sys, os

def patch(path, repls):
    with open(path) as f:
        s = f.read()
    orig = s
    for i,(old,new) in enumerate(repls):
        if new in s and old not in s:
            print(f"  [{i}] already applied, skipping")
            continue
        c = s.count(old)
        if c != 1:
            print(f"  [{i}] ERROR: anchor count={c} (need 1). Anchor starts: {old[:60]!r}")
            sys.exit(1)
        s = s.replace(old, new)
        print(f"  [{i}] applied")
    if s != orig:
        if not os.path.exists(path + ".orig"):
            with open(path + ".orig","w") as f: f.write(orig)
        with open(path,"w") as f: f.write(s)
        print("  written")
    else:
        print("  no change")

C = "/build/swiftshader/CMakeLists.txt"
V = "/build/swiftshader/src/Vulkan/CMakeLists.txt"

print("== CMakeLists.txt ==")
patch(C, [
 # Edit1: extend android cxx flags + include dir
 (
"    set(ANDROID TRUE)\n    set(CMAKE_CXX_FLAGS \"-DANDROID_NDK_BUILD\")",
"    set(ANDROID TRUE)\n    set(CMAKE_CXX_FLAGS \"-DANDROID_NDK_BUILD -DVK_USE_PLATFORM_ANDROID_KHR -DSWIFTSHADER_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER -DANDROID_PLATFORM_SDK_VERSION=10000\")\n    include_directories(SYSTEM \"/build/inc\")"
 ),
 # Edit2: WSI vk_base elseif(ANDROID)
 (
"elseif(FUCHSIA)\n    target_compile_definitions(vk_base INTERFACE \"VK_USE_PLATFORM_FUCHSIA\")\nelse()\n    message(FATAL_ERROR \"Platform does not support Vulkan yet\")\nendif()",
"elseif(FUCHSIA)\n    target_compile_definitions(vk_base INTERFACE \"VK_USE_PLATFORM_FUCHSIA\")\nelseif(ANDROID)\n    target_compile_definitions(vk_base INTERFACE \"VK_USE_PLATFORM_ANDROID_KHR\")\nelse()\n    message(FATAL_ERROR \"Platform does not support Vulkan yet\")\nendif()"
 ),
 # Edit3: export-map macro branch + android lds
 (
"    elseif(LINUX OR FUCHSIA)\n        # NOTE: The Fuchsia linker script is needed to export the vk_icdInitializeConnectToServiceCallback\n        # entry point (a private implementation detail betwen the Fuchsia Vulkan loader and the ICD).\n        if ((FUCHSIA) AND (\"${TARGET}\" STREQUAL \"vk_swiftshader\"))\n          set(LINKER_VERSION_SCRIPT \"fuchsia_vk_swiftshader.lds\")\n        else()\n          set(LINKER_VERSION_SCRIPT \"${TARGET}.lds\")\n        endif()",
"    elseif(LINUX OR FUCHSIA OR ANDROID)\n        # NOTE: The Fuchsia linker script is needed to export the vk_icdInitializeConnectToServiceCallback\n        # entry point (a private implementation detail betwen the Fuchsia Vulkan loader and the ICD).\n        if ((FUCHSIA) AND (\"${TARGET}\" STREQUAL \"vk_swiftshader\"))\n          set(LINKER_VERSION_SCRIPT \"fuchsia_vk_swiftshader.lds\")\n        elseif ((ANDROID) AND (\"${TARGET}\" STREQUAL \"vk_swiftshader\"))\n          set(LINKER_VERSION_SCRIPT \"android_vk_swiftshader.lds\")\n        else()\n          set(LINKER_VERSION_SCRIPT \"${TARGET}.lds\")\n        endif()"
 ),
 # Edit4: relax --no-undefined for android
 (
"        if(NOT SWIFTSHADER_MSAN AND NOT SWIFTSHADER_ASAN AND NOT SWIFTSHADER_TSAN AND NOT SWIFTSHADER_UBSAN)\n            set_property(TARGET ${TARGET} APPEND_STRING PROPERTY LINK_FLAGS \" -Wl,--no-undefined\")",
"        if(NOT SWIFTSHADER_MSAN AND NOT SWIFTSHADER_ASAN AND NOT SWIFTSHADER_TSAN AND NOT SWIFTSHADER_UBSAN AND NOT ANDROID)\n            set_property(TARGET ${TARGET} APPEND_STRING PROPERTY LINK_FLAGS \" -Wl,--no-undefined\")"
 ),
])

print("== src/Vulkan/CMakeLists.txt ==")
patch(V, [
 # Edit5: add android src before add_library
 (
"add_library(vk_swiftshader SHARED\n    ${VULKAN_SRC_FILES}\n)",
"if(ANDROID)\n    list(APPEND VULKAN_SRC_FILES\n        VkDeviceMemoryExternalAndroid.cpp\n        VkDeviceMemoryExternalAndroid.hpp\n    )\nendif()\n\nadd_library(vk_swiftshader SHARED\n    ${VULKAN_SRC_FILES}\n)"
 ),
 # Edit6: API lib name for android
 (
"elseif(FUCHSIA)\n    set(VULKAN_API_LIBRARY_NAME \"libvulkan.so\")\nelse()\n    message(FATAL_ERROR \"Platform does not support Vulkan yet\")\nendif()",
"elseif(FUCHSIA)\n    set(VULKAN_API_LIBRARY_NAME \"libvulkan.so\")\nelseif(ANDROID)\n    set(VULKAN_API_LIBRARY_NAME \"libvulkan.so\")\nelse()\n    message(FATAL_ERROR \"Platform does not support Vulkan yet\")\nendif()"
 ),
 # Edit7: android link libs
 (
"set_shared_library_export_map(vk_swiftshader ${CMAKE_CURRENT_SOURCE_DIR})",
"set_shared_library_export_map(vk_swiftshader ${CMAKE_CURRENT_SOURCE_DIR})\n\nif(ANDROID)\n    target_link_options(vk_swiftshader PRIVATE \"-L/mnt/laps/system/lib64\" \"-Wl,--allow-shlib-undefined\")\n    target_link_libraries(vk_swiftshader\n        PRIVATE\n            log\n            nativewindow\n            sync\n            /build/linklibs/libcutils.so\n            /build/linklibs/libutils.so\n            /build/linklibs/libbase.so\n            /build/linklibs/libhidlbase.so\n    )\nendif()"
 ),
])
print("DONE")
