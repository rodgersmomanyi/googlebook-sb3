#!/usr/bin/env python3
"""One request as the current uid (the tests start this as other uids to exercise SO_PEERCRED).

usage: python3 -I -B gc_client.py <socket> <method> <url> [<json body>]
prints {"status": <int or null>, "body": <str>, "error": <str>} on one line
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gc_http  # noqa: E402


def main():
    sock, method, url = sys.argv[1:4]
    body = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
    try:
        r = gc_http.request(sock, method, url, body, timeout=300)
        print(json.dumps({"status": r.status, "body": r.body.decode("utf-8", "replace"), "error": ""}))
    except Exception as e:  # noqa: BLE001 - reported to the caller
        print(json.dumps({"status": None, "body": "", "error": "%s: %s" % (type(e).__name__, e)}))


if __name__ == "__main__":
    main()
