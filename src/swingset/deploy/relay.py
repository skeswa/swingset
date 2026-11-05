"""A credential-free TCP relay; Postgres still authenticates each end user."""

from __future__ import annotations

import argparse
import contextlib
import socket
import socketserver
import threading


def serve(listen: str, port: int, target: str, target_port: int) -> None:
    class Relay(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            try:
                remote = socket.create_connection((target, target_port), timeout=10)
            except OSError:
                return
            with remote:
                self.request.settimeout(4200)
                remote.settimeout(4200)

                def copy(source: socket.socket, destination: socket.socket) -> None:
                    try:
                        while data := source.recv(64 * 1024):
                            destination.sendall(data)
                    except OSError:
                        pass
                    finally:
                        with contextlib.suppress(OSError):
                            destination.shutdown(socket.SHUT_WR)

                outbound = threading.Thread(target=copy, args=(self.request, remote), daemon=True)
                outbound.start()
                copy(remote, self.request)
                outbound.join(timeout=10)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server((listen, port), Relay) as server:
        server.serve_forever(poll_interval=0.2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--target", required=True)
    parser.add_argument("--target-port", type=int, default=5432)
    args = parser.parse_args()
    serve(args.listen, args.port, args.target, args.target_port)


if __name__ == "__main__":
    main()
