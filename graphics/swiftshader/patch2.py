p = "/build/swiftshader/CMakeLists.txt"
s = open(p).read()
anchor = "            \"-Wno-unknown-warning-option\""
ins = "            \"-Wno-missing-field-initializers\"  # Android HAL struct designated-initializers\n"
if "-Wno-missing-field-initializers" in s:
    print("already present")
else:
    assert s.count(anchor) == 1, s.count(anchor)
    s = s.replace(anchor, ins + anchor)
    open(p, "w").write(s)
    print("patched")
