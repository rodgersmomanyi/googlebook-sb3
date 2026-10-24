r"""Host side of the lapis guest root shell (gb_console on ttyS1 <-> vmx serial1 = \\.\pipe\lapis_con).

  gbsh.py [-t SECS] "cmd; cmd"        run commands, print output, exit with the guest's exit status
  gbsh.py [-t SECS] -f script.sh      run a local script's text
  gbsh.py push LOCAL REMOTE           copy a file into the guest (base64 over the serial line)
  gbsh.py pull REMOTE LOCAL           copy a file out of the guest

Reads are polled with PeekNamedPipe: a blocking ReadFile on a synchronous handle would also block WriteFile.
"""
import base64, ctypes, msvcrt, os, sys, time, uuid

PIPE = os.environ.get('GB_PIPE') or r'\\.\pipe\lapis_con'   # GB_PIPE: another VM's console pipe
k32 = ctypes.windll.kernel32


def _open():
    for _ in range(50):
        try:
            return open(PIPE, 'r+b', buffering=0)
        except OSError:
            time.sleep(0.2)
    sys.exit('gbsh: cannot open ' + PIPE + ' (VM off, or another client connected?)')


def _avail(f):
    n = ctypes.c_ulong(0)
    if not k32.PeekNamedPipe(ctypes.c_void_p(msvcrt.get_osfhandle(f.fileno())), None, 0, None, ctypes.byref(n), None):
        raise OSError('PeekNamedPipe failed (pipe closed?)')
    return n.value


def run(cmd, timeout=60, echo=True):
    """Send cmd, return (status, output bytes). Output before our marker from earlier sessions is dropped."""
    f = _open()
    tag = uuid.uuid4().hex[:10]
    beg, end = ('__GBBEG_%s__' % tag).encode(), ('__GBEND_%s__' % tag).encode()
    # Markers are split with "" so a tty echo of the input line can never match the real marker output.
    payload = 'echo __GBB""EG_%s__\n%s\necho __GBE""ND_%s__ $?\n' % (tag, cmd.rstrip('\n'), tag)
    data = payload.encode()
    for i in range(0, len(data), 2048):          # stay well under the tty's 4 KiB canonical buffer per burst
        f.write(data[i:i + 2048])
    buf, deadline, shown = b'', time.time() + timeout, 0
    while time.time() < deadline:
        n = _avail(f)
        if not n:
            time.sleep(0.03)
            continue
        buf += f.read(n)
        b = buf.find(beg + b'\n')
        if b >= 0 and echo:
            body = buf[b + len(beg) + 1:]
            e = body.find(end)
            out = body[:e] if e >= 0 else body[:max(0, len(body) - len(end) - 8)]
            if len(out) > shown:
                sys.stdout.buffer.write(out[shown:].replace(b'\r', b''))
                sys.stdout.flush()
                shown = len(out)
        e = buf.find(end + b' ')
        if e >= 0 and buf.find(b'\n', e) >= 0:
            st = buf[e + len(end) + 1:buf.find(b'\n', e)].strip()
            f.close()
            body = buf[buf.find(beg + b'\n') + len(beg) + 1:e] if b >= 0 else b''
            return int(st or b'0'), body.replace(b'\r', b'')
    f.close()
    sys.stderr.write('\ngbsh: TIMEOUT after %ss\n' % timeout)
    return 124, buf


def push(local, remote, timeout=900):
    raw = open(local, 'rb').read()
    b64 = base64.b64encode(raw).decode()
    lines = '\n'.join(b64[i:i + 1000] for i in range(0, len(b64), 1000))
    cmd = ("/system/bin/toybox base64 -d > '%s.gbtmp' <<'GBEOF'\n%s\nGBEOF\n"
           "/system/bin/toybox mv -f '%s.gbtmp' '%s' && /system/bin/toybox md5sum '%s'") % (remote, lines, remote, remote, remote)
    t = time.time()
    st, out = run(cmd, timeout, echo=False)
    import hashlib
    want = hashlib.md5(raw).hexdigest()
    ok = want.encode() in out
    print('push %s -> %s: %d bytes in %.1fs, md5 %s' % (local, remote, len(raw), time.time() - t, 'OK' if ok else 'MISMATCH ' + out.decode(errors='replace')))
    return 0 if ok else 1


def pull(remote, local, timeout=900):
    st, out = run("/system/bin/toybox base64 -w 0 '%s'; echo" % remote, timeout, echo=False)
    if st:
        sys.exit('pull failed: ' + out.decode(errors='replace'))
    open(local, 'wb').write(base64.b64decode(out.strip()))
    print('pull %s -> %s: %d bytes' % (remote, local, os.path.getsize(local)))
    return 0


if __name__ == '__main__':
    a = sys.argv[1:]
    t = 60
    if a[:1] == ['-t']:
        t = float(a[1]); a = a[2:]
    if a[:1] == ['--reset']:
        # ^C on the tty SIGINTs the whole gb_console process group (a hung command, sh, cat); init restarts it.
        f = _open(); f.write(b'\x03'); f.close()
        print('sent ^C'); sys.exit(0)
    if a[:1] == ['push']:
        sys.exit(push(a[1], a[2], max(t, 900)))
    if a[:1] == ['pull']:
        sys.exit(pull(a[1], a[2], max(t, 900)))
    if a[:1] == ['-f']:
        cmd = open(a[1], encoding='utf-8').read()
    else:
        cmd = ' '.join(a)
    st, _ = run(cmd, t)
    sys.exit(st)
