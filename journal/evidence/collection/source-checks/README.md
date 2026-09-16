# Verification fetches

Each dated folder holds the response headers (`*.hdr`) and the script
that produced them for one round of checks against the sites. Bodies are
not kept.

`2026-09-08/verify.sh` and `verify2.sh` are the record of what ran on
that day, kept unchanged. They are not a model for the fetch layer:
they used `curl -L`, which follows redirects without passing the hop
through the per-host gap (the DCN `robots.txt` redirect and its target
were fetched in the same second), and they did not stop on a 429 or
503. Nothing was throttled that day, but the scripts would not have
noticed.

Future checks use `polite_fetch.py` in this folder: one helper, no
automatic redirects (each hop is its own gated request), a per-host
gap of 5 s, `Accept-Encoding: gzip` only, and it stops the host on
429, 503, 403, or a Cloudflare challenge. Usage:

```
python3 research/verification/polite_fetch.py <out_dir> <url> [<url> ...]
python3 research/verification/polite_fetch.py <out_dir> --cond <name> <url>   # conditional GET using <name>.hdr
```
