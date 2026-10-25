"""Minimal HTTP/1.1-over-AF_UNIX client for the gb_gpu_compute tests (python3 -I; no third-party modules)."""
import json
import socket
import time


class Resp:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def events(self):
        """Server-sent events: list of decoded 'data:' payloads (JSON objects, or the string '[DONE]')."""
        out = []
        for block in self.body.decode("utf-8", "replace").split("\n\n"):
            for line in block.split("\n"):
                if line.startswith("data: "):
                    d = line[6:]
                    out.append(d if d == "[DONE]" else json.loads(d))
        return out

    def __repr__(self):
        return "Resp(%d, %r)" % (self.status, self.body[:200])


def connect(path, timeout=120):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    return s


def send(s, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    h = {"Host": "localhost"}
    if data or method == "POST":
        h["Content-Type"] = "application/json"
        h["Content-Length"] = str(len(data))
    h.update(headers or {})
    head = "%s %s HTTP/1.1\r\n" % (method, path) + "".join("%s: %s\r\n" % kv for kv in h.items()) + "\r\n"
    s.sendall(head.encode() + data)


def read_head(s, timeout=120):
    deadline = time.monotonic() + timeout
    buf = b""
    while True:
        while b"\r\n\r\n" not in buf:
            if time.monotonic() > deadline:
                raise TimeoutError("no response header")
            d = s.recv(65536)
            if not d:
                raise ConnectionError("closed before the response header (got %r)" % buf[:100])
            buf += d
        head, rest = buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1])
        if status == 100:
            buf = rest
            continue
        hdr = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            hdr[k.strip().lower()] = v.strip()
        return status, hdr, rest


def read_response(s, timeout=120):
    status, hdr, rest = read_head(s, timeout)
    deadline = time.monotonic() + timeout
    if "content-length" in hdr:
        n = int(hdr["content-length"])
        while len(rest) < n:
            if time.monotonic() > deadline:
                raise TimeoutError("body")
            d = s.recv(65536)
            if not d:
                raise ConnectionError("closed in the body")
            rest += d
        return Resp(status, hdr, rest[:n])
    while True:  # event stream / Connection: close: read to EOF
        if time.monotonic() > deadline:
            raise TimeoutError("stream")
        d = s.recv(65536)
        if not d:
            break
        rest += d
    return Resp(status, hdr, rest)


def request(path, method, url, body=None, headers=None, raw=None, timeout=120):
    s = connect(path, timeout)
    try:
        send(s, method, url, body, headers, raw)
        return read_response(s, timeout)
    finally:
        s.close()
