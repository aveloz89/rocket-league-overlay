import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

log = logging.getLogger(__name__)

EventHandler = Callable[[dict], Awaitable[None]]

# A single Stats API event fits comfortably under ~10 KB. Cap at 1 MB to bound
# memory if a misbehaving (or malicious) source on the local TCP port sends
# data without newlines.
MAX_LINE_BYTES = 1_000_000


def split_json_lines(buffer: bytes) -> tuple[list[dict], bytes]:
    """Split a TCP buffer into JSON objects, returning leftover bytes.

    The Stats API emits one JSON object per line, but a TCP read can land
    in the middle of a line — leftover is kept for the next read.
    """
    events: list[dict] = []
    while True:
        nl = buffer.find(b"\n")
        if nl == -1:
            if len(buffer) > MAX_LINE_BYTES:
                log.warning("line buffer exceeded %d bytes without newline — dropping", MAX_LINE_BYTES)
                return events, b""
            return events, buffer
        line = buffer[:nl].strip()
        buffer = buffer[nl + 1 :]
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            log.warning("invalid JSON line dropped: %s", exc)


async def stream_events(
    host: str = "127.0.0.1",
    port: int = 49123,
    reconnect_delay: float = 2.0,
) -> AsyncIterator[dict]:
    """Yield events from the Rocket League Stats API, reconnecting on drop.

    The game closes the socket between matches, so we loop forever.
    """
    while True:
        try:
            log.info("connecting to %s:%d", host, port)
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            log.warning("connect failed (%s) — retry in %.1fs", exc, reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            continue

        log.info("connected")
        buffer = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    log.info("socket closed by game")
                    break
                buffer += chunk
                events, buffer = split_json_lines(buffer)
                for event in events:
                    yield event
        except (ConnectionError, asyncio.IncompleteReadError) as exc:
            log.warning("read error: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        await asyncio.sleep(reconnect_delay)
