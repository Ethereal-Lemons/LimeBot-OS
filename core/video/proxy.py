"""Per-job loopback proxy with public-address validation and DNS pinning."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from .constants import MAX_DOWNLOAD_BYTES


class ProxySecurityError(RuntimeError):
    pass


def is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global) and not any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def resolve_public_host(host: str, port: int) -> str:
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ProxySecurityError("remote hostname could not be resolved") from exc
    addresses = list(dict.fromkeys(item[4][0] for item in results))
    if not addresses or any(not is_public_address(address) for address in addresses):
        raise ProxySecurityError("remote hostname resolves to a non-public address")
    return addresses[0]


class GuardedProxy:
    def __init__(self, byte_limit: int = MAX_DOWNLOAD_BYTES, idle_timeout: float = 45.0):
        self.byte_limit = byte_limit
        self.idle_timeout = idle_timeout
        self.bytes_received = 0
        self.error: Exception | None = None
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def url(self) -> str:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("proxy is not running")
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_exc):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _read_headers(self, reader: asyncio.StreamReader) -> bytes:
        try:
            data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.idle_timeout)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError) as exc:
            raise ProxySecurityError("invalid or stalled proxy request") from exc
        if len(data) > 64 * 1024:
            raise ProxySecurityError("proxy request headers are too large")
        return data

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        try:
            raw = await self._read_headers(client_reader)
            lines = raw.decode("iso-8859-1").split("\r\n")
            method, target, version = lines[0].split(" ", 2)
            if method.upper() == "CONNECT":
                host, port = self._parse_authority(target, 443)
                pinned = await resolve_public_host(host, port)
                upstream_reader, upstream_writer = await asyncio.open_connection(pinned, port)
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            else:
                parsed = urlsplit(target)
                if parsed.scheme.lower() != "http" or not parsed.hostname:
                    raise ProxySecurityError("proxy accepts only absolute public HTTP targets")
                if parsed.username or parsed.password:
                    raise ProxySecurityError("embedded URL credentials are not allowed")
                port = parsed.port or 80
                pinned = await resolve_public_host(parsed.hostname, port)
                upstream_reader, upstream_writer = await asyncio.open_connection(pinned, port)
                origin = parsed.path or "/"
                if parsed.query:
                    origin += "?" + parsed.query
                filtered = [line for line in lines[1:] if not line.lower().startswith(("proxy-connection:", "proxy-authorization:"))]
                rewritten = "\r\n".join([f"{method} {origin} {version}", *filtered]).encode("iso-8859-1")
                upstream_writer.write(rewritten)
                await upstream_writer.drain()

            await self._relay(client_reader, client_writer, upstream_reader, upstream_writer)
        except Exception as exc:
            self.error = exc
            try:
                client_writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                await client_writer.drain()
            except Exception:
                pass
        finally:
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            if task:
                self._tasks.discard(task)

    @staticmethod
    def _parse_authority(authority: str, default_port: int) -> tuple[str, int]:
        if authority.startswith("["):
            end = authority.find("]")
            if end < 0:
                raise ProxySecurityError("invalid CONNECT destination")
            host = authority[1:end]
            port = int(authority[end + 2 :]) if authority[end + 1 :].startswith(":") else default_port
            return host, port
        host, separator, raw_port = authority.rpartition(":")
        return (host, int(raw_port)) if separator else (authority, default_port)

    async def _pump(self, reader, writer, count: bool):
        while True:
            data = await asyncio.wait_for(reader.read(64 * 1024), self.idle_timeout)
            if not data:
                break
            if count:
                self.bytes_received += len(data)
                if self.bytes_received > self.byte_limit:
                    raise ProxySecurityError("remote media exceeded the 500 MiB job limit")
            writer.write(data)
            await writer.drain()

    async def _relay(self, client_reader, client_writer, upstream_reader, upstream_writer):
        outbound = asyncio.create_task(self._pump(client_reader, upstream_writer, False))
        inbound = asyncio.create_task(self._pump(upstream_reader, client_writer, True))
        done, pending = await asyncio.wait({outbound, inbound}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*done, *pending, return_exceptions=True)
        upstream_writer.close()
        try:
            await upstream_writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        for result in results:
            if isinstance(result, ProxySecurityError):
                raise result
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                raise result
