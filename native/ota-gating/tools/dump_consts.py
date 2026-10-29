"""Print selected literal/computed constants of a build_esp.py copy (imported with GB_NO_BUILDER=1)."""
import contextlib, importlib.util, io, os, sys
sys.dont_write_bytecode = True
os.environ["GB_NO_BUILDER"] = "1"
spec = importlib.util.spec_from_file_location("be_dump", sys.argv[1])
be = importlib.util.module_from_spec(spec)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    spec.loader.exec_module(be)
print(buf.getvalue()[-1500:])
for n in sys.argv[2:]:
    v = getattr(be, n)
    print(f"===== {n} =====")
    print(v.decode() if isinstance(v, bytes) else v)
