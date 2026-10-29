"""Read-only: interpreter + DT_NEEDED of every ELF in the VM and native /odm trees (and the dGPU payload), so each
binary can be classified as LLNDK/NDK-only (build-independent ABI) or linked against vendor/system-private libraries.
  python -I -B elf_deps.py BUILD_ESP_PY"""
import contextlib, hashlib, importlib.util, io, os, sys
sys.dont_write_bytecode = True
os.environ["GB_NO_BUILDER"] = "1"
# LLNDK + NDK stable libraries (AOSP llndk + ndk sets as used by vendor code; libc++ NDK static not dynamic)
STABLE = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libbinder_ndk.so", "libnativewindow.so", "libsync.so",
          "libvndksupport.so", "libandroid_net.so", "libmediandk.so", "libvulkan.so", "libEGL.so", "libGLESv1_CM.so",
          "libGLESv2.so", "libGLESv3.so", "libz.so", "libandroid.so", "libcamera2ndk.so", "libaaudio.so",
          "libOpenMAXAL.so", "libOpenSLES.so", "libjnigraphics.so", "libneuralnetworks.so", "libdl_android.so",
          "libselinux.so", "libft2.so", "libRS.so", "libstatssocket.so", "libapexsupport.so"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def cpio_entries(buf):
    out, o = [], 0
    while True:
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode()
        o = (o + 110 + nsz + 3) & ~3
        data = buf[o:o + fsz]
        o = (o + fsz + 3) & ~3
        if name == "TRAILER!!!":
            return out
        out.append((name, mode, data))


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        be = load("build_esp", sys.argv[1])
        nat = cpio_entries(be.native_cpio())
    rows = [(p, d) for p, m, d in be._VM_ENTRIES if d and d[:4] == b"\x7fELF" and (m & 0o170000) == 0o100000]
    rows += [(p, d) for p, m, d in nat if d and d[:4] == b"\x7fELF" and (m & 0o170000) == 0o100000]
    rows += [("payload/" + p, d) for p, d in be.PAYLOAD_FILES if d[:4] == b"\x7fELF"]
    ours = {os.path.basename(p) for p, _ in rows}
    for p, d in rows:
        if p.endswith(".ko"):
            continue
        try:
            interp, needed = be._elf_dynamic(d)
        except AssertionError as e:
            interp, needed = f"(static/odd: {e})", []
        priv = [n for n in needed if n not in STABLE and n not in ours]
        bundled = [n for n in needed if n in ours and n not in STABLE]
        cls = "STABLE-ONLY" if not priv else "PRIVATE-DEPS"
        print(f"{cls}\t{p}\t{interp}\tbundled={','.join(bundled) or '-'}\tprivate={','.join(priv) or '-'}")


if __name__ == "__main__":
    main()
