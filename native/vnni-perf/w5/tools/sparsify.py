r"""Give the all-zero 64 KiB blocks of a throwaway test disk back to NTFS (FSCTL_SET_ZERO_DATA); content unchanged.

  python -I -B sparsify.py D:\gb-w5test\<dir>\disk.img

Only below D:\gb-w5test, only a sparse file, only when no VM holds it (no .lck folder next to it). The whole-file
sha256 is computed while scanning and again afterwards; both must be equal (else exit 1)."""
import ctypes, hashlib, msvcrt, os, sys, time
from ctypes import wintypes

P = os.path.abspath(sys.argv[1])
assert os.path.normcase(P).startswith(os.path.normcase("D:\\gb-w5test") + os.sep), P
d = os.path.dirname(P)
assert not [n for n in os.listdir(d) if n.lower().endswith(".lck") and os.listdir(os.path.join(d, n))], "VM lock present"
k = ctypes.windll.kernel32
assert k.GetFileAttributesW(ctypes.c_wchar_p(P)) & 0x200, "not a sparse file"
k.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k.DeviceIoControl.restype = wintypes.BOOL
k.GetCompressedFileSizeW.restype = ctypes.c_ulong


class FZDI(ctypes.Structure):
    _fields_ = [("FileOffset", ctypes.c_longlong), ("BeyondFinalZero", ctypes.c_longlong)]


def alloc():
    hi = ctypes.c_ulong(0)
    lo = k.GetCompressedFileSizeW(ctypes.c_wchar_p(P), ctypes.byref(hi))
    return (hi.value << 32) | lo


def sha():
    h = hashlib.sha256()
    with open(P, "rb") as f:
        while True:
            b = f.read(16 << 20)
            if not b:
                return h.hexdigest()
            h.update(b)


B = 64 << 10
CH = 16 << 20
ZB = bytes(B)
size = os.path.getsize(P)
a0 = alloc()
t0 = time.time()
h = hashlib.sha256()
runs = []
with open(P, "rb") as f:
    pos = 0
    while pos < size:
        buf = f.read(min(CH, size - pos))
        h.update(buf)
        for i in range(0, len(buf), B):
            if buf[i:i + B] == ZB[:len(buf[i:i + B])]:
                s = pos + i
                e = min(s + B, size)
                if runs and runs[-1][1] == s:
                    runs[-1][1] = e
                else:
                    runs.append([s, e])
        pos += len(buf)
before = h.hexdigest()
with open(P, "r+b") as f:
    hd = ctypes.c_void_p(msvcrt.get_osfhandle(f.fileno()))
    br = wintypes.DWORD(0)
    for s, e in runs:
        info = FZDI(s, e)
        if not k.DeviceIoControl(hd, 0x000980C8, ctypes.byref(info), ctypes.sizeof(info), None, 0, ctypes.byref(br), None):
            raise OSError(f"FSCTL_SET_ZERO_DATA failed at {s}: {ctypes.GetLastError()}")
    f.flush(); os.fsync(f.fileno())
after = sha()
print(f"{P}: {len(runs)} zero runs; allocated {a0 / 2**30:.2f} -> {alloc() / 2**30:.2f} GiB; sha256 before {before} "
      f"after {after}: {'UNCHANGED' if before == after else 'CHANGED'} ({time.time() - t0:.0f} s)")
sys.exit(0 if before == after else 1)
