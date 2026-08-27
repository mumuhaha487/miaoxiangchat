from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit


LISTEN_HOST = os.getenv("PROXY_BRIDGE_BIND_HOST", "172.17.0.1").strip()
LISTEN_PORT = int(os.getenv("PROXY_BRIDGE_BIND_PORT", "10809"))
UPSTREAM = urlsplit(os.getenv("OUTBOUND_PROXY_URL", "http://127.0.0.1:10808").strip())

if UPSTREAM.scheme not in {"http", "https"} or not UPSTREAM.hostname or not UPSTREAM.port:
    raise RuntimeError("OUTBOUND_PROXY_URL must be an http(s) URL with an explicit port")


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    peer = client_writer.get_extra_info("peername")
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(UPSTREAM.hostname, UPSTREAM.port)
    except OSError as exc:
        print(f"[proxy-bridge] upstream unavailable for {peer}: {exc}", flush=True)
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        relay(client_reader, upstream_writer),
        relay(upstream_reader, client_writer),
    )


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"[proxy-bridge] {sockets} -> {UPSTREAM.hostname}:{UPSTREAM.port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
