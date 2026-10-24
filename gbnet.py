"""Fast file transfer to/from the lapis guest over the VM network (guest eth0, default 192.168.115.133), using toybox nc in
the guest driven through the serial console (gbsh.py). The guest always listens; the host connects (no host firewall rule).
  gbnet.py push LOCAL REMOTE      copy host file into the guest (verifies md5)
  gbnet.py pull REMOTE LOCAL      copy a guest file to the host
"""
import hashlib, os, socket, subprocess, sys, time

PY = sys.executable
GBSH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gbsh.py')
PORT = 8766


def gbsh(cmd, t=60):
    env = dict(os.environ, MSYS_NO_PATHCONV='1')
    r = subprocess.run([PY, '-I', GBSH, '-t', str(t), cmd], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


def guest_ip():
    _, out = gbsh("ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1")
    ip = out.strip().splitlines()[-1] if out.strip() else ''
    if not ip:
        sys.exit('gbnet: guest has no eth0 address')
    return ip


def connect(ip):
    for _ in range(40):
        try:
            return socket.create_connection((ip, PORT), timeout=10)
        except OSError:
            time.sleep(0.25)
    sys.exit('gbnet: cannot connect to guest listener')


def push(local, remote):
    data = open(local, 'rb').read()
    ip = guest_ip()
    gbsh("pkill -f 'nc -l -p %d'; (toybox nc -l -p %d > '%s.gbtmp' &) ; sleep 0.3; echo listening" % (PORT, PORT, remote))
    s = connect(ip)
    s.sendall(data)
    s.shutdown(socket.SHUT_WR)
    s.close()
    time.sleep(0.5)
    _, out = gbsh("sleep 0.5; mv -f '%s.gbtmp' '%s'; md5sum '%s'" % (remote, remote, remote))
    ok = hashlib.md5(data).hexdigest() in out
    print('push %s -> %s: %d bytes md5 %s' % (local, remote, len(data), 'OK' if ok else 'MISMATCH ' + out))
    return 0 if ok else 1


def pull(remote, local):
    ip = guest_ip()
    gbsh("pkill -f 'nc -l -p %d'; (toybox nc -l -p %d < '%s' &) ; sleep 0.3; echo serving" % (PORT, PORT, remote))
    s = connect(ip)
    buf = b''
    while True:
        d = s.recv(1 << 16)
        if not d:
            break
        buf += d
    s.close()
    open(local, 'wb').write(buf)
    print('pull %s -> %s: %d bytes' % (remote, local, len(buf)))
    return 0


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == 'push':
        sys.exit(push(a[1], a[2]))
    if len(a) == 3 and a[0] == 'pull':
        sys.exit(pull(a[1], a[2]))
    sys.exit(__doc__)
