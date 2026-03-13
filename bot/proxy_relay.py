"""
Local Proxy Relay — solves proxy authentication in Chrome headless mode.

Problem: Chrome extensions (used for proxy auth) do NOT load in headless mode.
Solution: Spin up a tiny local HTTP proxy on 127.0.0.1:PORT that requires no auth,
          and forwards all traffic to the real authenticated upstream proxy.

Chrome → localhost:PORT (no auth) → upstream proxy (user:pass@host:port) → internet

Each bot worker gets its own relay instance on a unique port.
"""
import threading
import socket
import select
import base64
import logging
import time

logger = logging.getLogger(__name__)

# Track allocated ports to avoid collisions
_port_lock = threading.Lock()
_used_ports = set()
_PORT_RANGE_START = 18100
_PORT_RANGE_END = 18999


def _allocate_port() -> int:
    """Find a free port in our range."""
    with _port_lock:
        for port in range(_PORT_RANGE_START, _PORT_RANGE_END):
            if port not in _used_ports:
                # Quick check if port is actually free
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.bind(("127.0.0.1", port))
                    s.close()
                    _used_ports.add(port)
                    return port
                except OSError:
                    continue
    raise RuntimeError("No free ports available for proxy relay")


def _release_port(port: int):
    with _port_lock:
        _used_ports.discard(port)


class ProxyRelay:
    """
    A lightweight local proxy that forwards traffic to an authenticated upstream proxy.
    
    Usage:
        relay = ProxyRelay("user:pass@1.2.3.4:8080")
        relay.start()      # Starts background thread
        # Chrome uses: --proxy-server=127.0.0.1:{relay.local_port}
        relay.stop()        # When done
    """

    def __init__(self, upstream_proxy: str):
        """
        Args:
            upstream_proxy: Format "user:pass@host:port"
        """
        self._parse_upstream(upstream_proxy)
        self.local_port = _allocate_port()
        self._server_socket = None
        self._running = False
        self._thread = None

    def _parse_upstream(self, proxy_str: str):
        if "@" not in proxy_str:
            raise ValueError(f"Proxy must be in user:pass@host:port format, got: {proxy_str}")
        creds, endpoint = proxy_str.split("@", 1)
        self.upstream_user, self.upstream_pass = creds.split(":", 1)
        self.upstream_host, port_str = endpoint.split(":", 1)
        self.upstream_port = int(port_str)
        # Pre-compute the Proxy-Authorization header
        cred_bytes = f"{self.upstream_user}:{self.upstream_pass}".encode()
        self._auth_header = b"Proxy-Authorization: Basic " + base64.b64encode(cred_bytes) + b"\r\n"

    @property
    def local_address(self) -> str:
        return f"127.0.0.1:{self.local_port}"

    def start(self):
        """Start the relay in a background daemon thread."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("127.0.0.1", self.local_port))
        self._server_socket.listen(32)
        self._server_socket.settimeout(1.0)
        self._running = True

        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info(f"ProxyRelay started: 127.0.0.1:{self.local_port} → {self.upstream_host}:{self.upstream_port}")

    def stop(self):
        """Stop the relay and release the port."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        _release_port(self.local_port)
        logger.info(f"ProxyRelay stopped (port {self.local_port})")

    def _accept_loop(self):
        while self._running:
            try:
                client_sock, _ = self._server_socket.accept()
                handler = threading.Thread(
                    target=self._handle_client, args=(client_sock,), daemon=True
                )
                handler.start()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                if self._running:
                    logger.debug(f"ProxyRelay accept error: {e}")

    def _handle_client(self, client_sock: socket.socket):
        """Handle a single client connection — relay to upstream with auth."""
        upstream_sock = None
        try:
            client_sock.settimeout(30)

            # Read the initial request from Chrome
            data = b""
            while b"\r\n" not in data:
                chunk = client_sock.recv(8192)
                if not chunk:
                    return
                data += chunk

            first_line = data.split(b"\r\n")[0]
            method = first_line.split(b" ")[0].upper()

            # Connect to upstream proxy
            upstream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            upstream_sock.settimeout(30)
            upstream_sock.connect((self.upstream_host, self.upstream_port))

            if method == b"CONNECT":
                # HTTPS tunnel — send CONNECT to upstream WITH auth
                # Inject our auth header into the request
                header_end = data.find(b"\r\n\r\n")
                if header_end == -1:
                    # Read more until we get full headers
                    while b"\r\n\r\n" not in data:
                        chunk = client_sock.recv(8192)
                        if not chunk:
                            return
                        data += chunk
                    header_end = data.find(b"\r\n\r\n")

                # Rebuild request with auth header
                request_line = data[:data.find(b"\r\n") + 2]
                remaining_headers = data[data.find(b"\r\n") + 2:header_end + 4]
                auth_request = request_line + self._auth_header + remaining_headers

                upstream_sock.sendall(auth_request)

                # Read upstream response
                response = b""
                while b"\r\n\r\n" not in response:
                    chunk = upstream_sock.recv(8192)
                    if not chunk:
                        return
                    response += chunk

                first_resp_line = response.split(b"\r\n")[0]
                if b"200" in first_resp_line:
                    # Tunnel established — tell Chrome 200 OK
                    client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    # Bidirectional relay
                    self._tunnel(client_sock, upstream_sock)
                else:
                    # Forward upstream error to client
                    client_sock.sendall(response)

            else:
                # HTTP (non-CONNECT) — inject auth header and forward
                header_end = data.find(b"\r\n\r\n")
                if header_end == -1:
                    while b"\r\n\r\n" not in data:
                        chunk = client_sock.recv(8192)
                        if not chunk:
                            return
                        data += chunk
                    header_end = data.find(b"\r\n\r\n")

                request_line = data[:data.find(b"\r\n") + 2]
                remaining = data[data.find(b"\r\n") + 2:]
                auth_request = request_line + self._auth_header + remaining

                upstream_sock.sendall(auth_request)

                # Relay response back
                self._tunnel(client_sock, upstream_sock)

        except Exception:
            pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            if upstream_sock:
                try:
                    upstream_sock.close()
                except Exception:
                    pass

    def _tunnel(self, sock1: socket.socket, sock2: socket.socket):
        """Bidirectional data relay between two sockets."""
        sockets = [sock1, sock2]
        try:
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 30)
                if errored:
                    break
                if not readable:
                    break  # Timeout
                for s in readable:
                    data = s.recv(65536)
                    if not data:
                        return
                    target = sock2 if s is sock1 else sock1
                    target.sendall(data)
        except Exception:
            pass
