p = "/build/swiftshader/src/Vulkan/CMakeLists.txt"
s = open(p).read()
old = """if(ANDROID)
    target_link_options(vk_swiftshader PRIVATE "-L/mnt/laps/system/lib64" "-Wl,--allow-shlib-undefined")
    target_link_libraries(vk_swiftshader
        PRIVATE
            log
            nativewindow
            sync
            /build/linklibs/libcutils.so
            /build/linklibs/libutils.so
            /build/linklibs/libbase.so
            /build/linklibs/libhidlbase.so
    )
endif()"""
new = """if(ANDROID)
    # Only liblog/libnativewindow/libsync are actually referenced (libc++ is
    # static; no HAVE_GRALLOC so no mapper/hidl/cutils/utils deps). Set the HAL
    # soname to match the Cuttlefish artifact name.
    target_link_options(vk_swiftshader PRIVATE "-Wl,--allow-shlib-undefined" "-Wl,-soname,vulkan.pastel.so")
    target_link_libraries(vk_swiftshader
        PRIVATE
            log
            nativewindow
            sync
    )
endif()"""
assert s.count(old) == 1, s.count(old)
open(p,"w").write(s.replace(old,new))
print("patched link block")
