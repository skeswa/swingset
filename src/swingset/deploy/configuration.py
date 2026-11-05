"""Render exact endpoint ACLs and a resolver that never forwards unknown names."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def runtime_artifact(value: str) -> str:
    """Validate backup metadata's OCI identity, independently of daemon CONFIG IDs."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", value) or "//" in value:
        raise ValueError("runtime artifact must name a qualified OCI image with a 64-hex digest")
    return value


def endpoints(values: list[str]) -> list[tuple[str, int]]:
    result = []
    for value in values:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("proxy destinations must be exact HTTP origins")
        host = url.hostname or ""
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or ".." in host:
            raise ValueError("proxy destinations must name exact DNS hosts")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP literal proxy destinations are forbidden")
        port = url.port or (443 if url.scheme == "https" else 80)
        if url.scheme == "https" and port != 443:
            raise ValueError("HTTPS proxy destinations use port 443")
        result.append((host, port))
    if not result:
        raise ValueError("an explicit proxy endpoint list is required")
    return sorted(set(result))


def squid(origins: list[str]) -> str:
    routes = endpoints(origins)
    result = [
        "http_port 3128",
        "visible_hostname swingset-rehearsal",
        "pid_filename none",
        "cache_log /dev/stderr",
        "cache_store_log none",
        "cache deny all",
        "cache_mem 0 MB",
        "logformat bounded %ts.%03tu %>a %Ss/%03>Hs %<st %rm",
        "access_log stdio:/dev/stdout bounded",
        "forwarded_for delete",
        "via off",
        "host_verify_strict on",
        "shutdown_lifetime 10 seconds",
        "acl CONNECT method CONNECT",
        "acl tls_port port 443",
        "http_access deny CONNECT !tls_port",
    ]
    for position, (host, port) in enumerate(routes):
        result += [
            f"acl route_{position}_host dstdomain -n {host}",
            f"acl route_{position}_port port {port}",
            f"http_access allow route_{position}_host route_{position}_port",
        ]
    result += ["http_access deny all", ""]
    return "\n".join(result)


def coredns(hosts: dict[str, str]) -> str:
    result = [".:53 {", "  errors", "  hosts {", "    ttl 30", "    no_reverse"]
    for hostname, address in sorted(hosts.items()):
        if (
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", hostname)
            or ".." in hostname
        ):
            raise ValueError("invalid internal resolver host")
        if not ipaddress.ip_address(address).is_private:
            raise ValueError("internal resolver records must have private addresses")
        result.append(f"    {address} {hostname}")
    result += ["  }", "}", ""]  # No fallthrough or forward: unknown names return NXDOMAIN.
    return "\n".join(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = json.loads(args.configuration.read_text())
    proxy, resolver = squid(document["proxy_origins"]), coredns(document["internal_hosts"])
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "squid.conf").write_text(proxy)
    (args.output / "Corefile").write_text(resolver)


if __name__ == "__main__":
    main()
