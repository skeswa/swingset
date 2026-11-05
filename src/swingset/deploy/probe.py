"""Exercise allowed proxy traffic and blocked DNS/direct routes to owned endpoints."""

from __future__ import annotations

import argparse
import json
import socket

import httpx


def check(
    allowed: str, denied_name: str, denied_addresses: list[str], port: int, proxy: str
) -> dict[str, object]:
    if not denied_addresses:
        raise ValueError("owned negative IP endpoints are required")
    with httpx.Client(proxy=proxy, trust_env=False, timeout=5, follow_redirects=True) as client:
        response = client.get(allowed)
        if response.status_code != 200:
            raise RuntimeError("the owned allowed endpoint did not succeed through the proxy")
        denied = client.get(f"http://{denied_name}:{port}/ok")
        if denied.status_code != 403:
            raise RuntimeError("the proxy did not deny the owned forbidden name")
    try:
        socket.getaddrinfo(denied_name, port)
    except socket.gaierror:
        pass
    else:
        raise RuntimeError("the worker resolver accepted a forbidden name")
    for address in denied_addresses:
        try:
            connection = socket.create_connection((address, port), timeout=3)
        except OSError:
            continue
        connection.close()
        raise RuntimeError("the worker reached an owned endpoint directly")
    return {
        "allowed_proxy": True,
        "forbidden_proxy": True,
        "forbidden_dns": True,
        "direct_addresses_blocked": denied_addresses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowed", required=True)
    parser.add_argument("--denied-name", required=True)
    parser.add_argument("--denied-address", action="append", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--proxy", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            check(args.allowed, args.denied_name, args.denied_address, args.port, args.proxy),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
