"""One polite fetch helper for verification runs. Stdlib only.

- One request in flight, a 5 s gap per host, 10 s for web.archive.org.
- Redirects are not followed automatically; each hop is a new gated
  request (at most 3 hops), so a cross-host redirect waits for its own
  host's gap.
- Accept-Encoding: gzip only. No cookies.
- 429, 503, 403, or a Cloudflare challenge body stops all further
  requests to that host for this run and is printed loudly.
- Writes <name>.hdr, <name>.body, <name>.meta into out_dir.

Usage:
  polite_fetch.py OUT_DIR URL [URL ...]
  polite_fetch.py OUT_DIR --cond NAME URL     conditional GET with validators from NAME.hdr
"""
import gzip, pathlib, sys, time, urllib.request, urllib.error, urllib.parse

UA = "swingset/0.0 (+https://github.com/skeswa/swingset)"
GAPS = {"web.archive.org": 10.0}
DEFAULT_GAP = 5.0
last_at: dict[str, float] = {}
stopped: set[str] = set()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

opener = urllib.request.build_opener(NoRedirect)

def wait(host):
    gap = GAPS.get(host, DEFAULT_GAP)
    dt = time.monotonic() - last_at.get(host, -1e9)
    if dt < gap:
        time.sleep(gap - dt)

def one(url, headers):
    host = urllib.parse.urlsplit(url).hostname
    if host in stopped:
        return None, f"host {host} stopped earlier in this run"
    wait(host)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **headers})
    t0 = time.monotonic()
    try:
        resp = opener.open(req, timeout=30)
        status, hdrs, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, e.headers, e.read()
    finally:
        last_at[host] = time.monotonic()
    body = gzip.decompress(raw) if hdrs.get("Content-Encoding") == "gzip" and raw else raw
    if status in (429, 503, 403) or b"cf-chl" in body[:20000] or b"Just a moment" in body[:20000]:
        stopped.add(host)
        print(f"!! {host}: status {status} or challenge; no more requests to this host", file=sys.stderr)
    return (status, hdrs, body, time.monotonic() - t0), None

def fetch(out, name, url, headers=None, hops=0):
    r, err = one(url, headers or {})
    if err:
        print(f"== {name}: {err}"); return
    status, hdrs, body, dt = r
    (out / f"{name}.hdr").write_text("".join(f"{k}: {v}\n" for k, v in hdrs.items()))
    (out / f"{name}.body").write_bytes(body)
    (out / f"{name}.meta").write_text(f"{status} {len(body)} {dt:.2f}s {url}\n")
    print(f"== {name}: {status} {len(body)} bytes {dt:.2f}s {url}")
    loc = hdrs.get("Location")
    if status in (301, 302, 303, 307, 308) and loc and hops < 3:
        fetch(out, f"{name}.hop{hops+1}", urllib.parse.urljoin(url, loc), headers, hops + 1)

def validators(out, name):
    h = {}
    for line in (out / f"{name}.hdr").read_text().splitlines():
        k, _, v = line.partition(": ")
        if k.lower() == "etag": h["If-None-Match"] = v
        if k.lower() == "last-modified": h["If-Modified-Since"] = v
    return h

def main(argv):
    out = pathlib.Path(argv[0]); out.mkdir(parents=True, exist_ok=True)
    args = argv[1:]
    if args and args[0] == "--cond":
        name, url = args[1], args[2]
        fetch(out, f"{name}_cond", url, validators(out, name)); return
    for i, url in enumerate(args):
        fetch(out, f"r{i:02d}_" + urllib.parse.urlsplit(url).hostname.replace(".", "_"), url)

if __name__ == "__main__":
    main(sys.argv[1:])
