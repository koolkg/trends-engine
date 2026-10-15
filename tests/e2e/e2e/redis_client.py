"""A minimal Redis client on the standard library (socket + RESP2), covering
just the commands the e2e suite needs (te-aggregator's client plus SCAN). The project prefers stdlib
over pip packages, and the protocol is small: a command is an array of bulk
strings, and replies are one of five types.
"""
import socket
from typing import Optional


class RedisError(Exception):
    pass


class Redis:
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, timeout: float = 5.0):
        self.host, self.port, self.db, self.timeout = host, port, db, timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""

    # -- connection -------------------------------------------------------
    def _connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._buf = b""
        if self.db:
            self._roundtrip("SELECT", self.db)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def execute(self, *args):
        """Runs one command, reconnecting once if the connection had dropped."""
        try:
            if self._sock is None:
                self._connect()
            return self._roundtrip(*args)
        except (OSError, ConnectionError):
            self.close()
            self._connect()
            return self._roundtrip(*args)

    # -- protocol -----------------------------------------------------------
    @staticmethod
    def _encode(args) -> bytes:
        out = [b"*%d\r\n" % len(args)]
        for a in args:
            b = a if isinstance(a, bytes) else str(a).encode("utf-8")
            out.append(b"$%d\r\n%s\r\n" % (len(b), b))
        return b"".join(out)

    def _roundtrip(self, *args):
        self._sock.sendall(self._encode(args))
        return self._read_reply()

    def _read_line(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("redis closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n + 2:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("redis closed the connection")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n + 2:]
        return data

    def _read_reply(self):
        line = self._read_line()
        kind, rest = line[:1], line[1:]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RedisError(rest.decode())
        if kind == b":":
            return int(rest)
        if kind == b"$":
            n = int(rest)
            return None if n < 0 else self._read_exact(n)
        if kind == b"*":
            n = int(rest)
            return None if n < 0 else [self._read_reply() for _ in range(n)]
        raise RedisError(f"unexpected reply {line!r}")

    # -- commands -----------------------------------------------------------
    def ping(self) -> bool:
        return self.execute("PING") == "PONG"

    def get(self, key: str) -> Optional[bytes]:
        return self.execute("GET", key)

    def set(self, key: str, value, ex: Optional[int] = None, px: Optional[int] = None, nx: bool = False) -> bool:
        args = ["SET", key, value]
        if ex is not None:
            args += ["EX", ex]
        if px is not None:
            args += ["PX", px]
        if nx:
            args.append("NX")
        return self.execute(*args) == "OK"

    def delete(self, *keys: str) -> int:
        return self.execute("DEL", *keys) if keys else 0

    def ttl(self, key: str) -> int:
        return self.execute("TTL", key)

    def eval(self, script: str, keys=(), args=()):
        return self.execute("EVAL", script, len(keys), *keys, *args)

    def keys(self, pattern: str):
        """SCAN-based (never blocks Redis the way KEYS does)."""
        cursor, out = "0", []
        while True:
            cursor, batch = self.execute("SCAN", cursor, "MATCH", pattern, "COUNT", 500)
            cursor = cursor.decode()
            out += [k.decode() for k in batch]
            if cursor == "0":
                return out
