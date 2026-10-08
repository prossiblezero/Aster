"""Connect the isolated Docker network to the host model gateway through one Unix socket."""
import select
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer


class Relay(BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.connect("/relay/model.sock")
            peers = {self.request: upstream, upstream: self.request}
            while True:
                ready, _, _ = select.select(list(peers), [], [], 180)
                if not ready:
                    return
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    peers[source].sendall(chunk)


if __name__ == "__main__":
    ThreadingTCPServer.allow_reuse_address = True
    ThreadingTCPServer.daemon_threads = True
    ThreadingTCPServer(("0.0.0.0", 18080), Relay).serve_forever()
