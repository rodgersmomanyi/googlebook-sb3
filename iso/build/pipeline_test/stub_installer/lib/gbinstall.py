"""Googlebook ISO PIPELINE-TEST STUB (NOT the installer). --selftest imports modules that must survive the
initramfs Python pruning and prints GB-SELFTEST OK; anything else refuses. Touches no device, starts no process."""
import os
import sys


def main(argv):
    if argv == ["--selftest"]:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I does not add the script directory
        import base64, binascii, collections, dataclasses, datetime, enum, errno, fcntl, functools  # noqa: F401
        import glob, hashlib, io, itertools, json, math, mmap, pathlib, re, select, shutil, signal  # noqa: F401
        import stat, string, struct, tempfile, termios, textwrap, threading, time, traceback, typing  # noqa: F401
        import uuid, zlib, resource, queue, contextvars  # noqa: F401
        import gb.run  # noqa: F401
        assert hashlib.sha256(b"abc").hexdigest().startswith("ba7816bf")
        assert json.loads(json.dumps({"a": 1})) == {"a": 1}
        print("GB-SELFTEST OK")
        return 0
    print("gbinstall: pipeline-test stub, not the Googlebook installer - refusing")
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
