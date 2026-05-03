import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

log = logging.getLogger(__name__)

EventHandler = Callable[[dict], Awaitable[None]]

# Runaway-buffer guard. The Stats API does not document a max event size, but
# in practice they fit in a few KB. If we accumulate more than this without
# producing a parsable object, the stream is corrupt — drop and resync.
MAX_BUFFER_CHARS = 1_000_000


def _decode_data_field(event: dict) -> dict:
    """RL ships the inner Data payload as a JSON-encoded string, not a nested
    object — so handlers see e.g. {"Event": "BallHit", "Data": "{...}"}.
    Decode it once here so the rest of the app can rely on dict semantics.
    """
    data = event.get("Data")
    if isinstance(data, str):
        try:
            event["Data"] = json.loads(data)
        except json.JSONDecodeError:
            # Leave the string in place; handle_event's exception guard will
            # drop the malformed event without taking down the pump.
            pass
    return event


def parse_json_objects(
    buffer: str, decoder: json.JSONDecoder
) -> tuple[list[dict], str]:
    """Extract zero or more JSON objects from a string buffer.

    The Stats API streams JSON values separated by arbitrary whitespace
    (often pretty-printed with embedded newlines), not by a single newline
    terminator. Splitting on '\\n' would either tear individual objects
    apart or — with no newlines at all — let the buffer grow until our
    runaway guard drops it. We use streaming raw_decode instead, matching
    what the reference clients (manucabral/RocketLeagueStatsAPI in Python,
    xentrick/rlstatsapi in Rust) do.

    Returns parsed objects and the leftover unparsed tail (a partial
    object waiting for more bytes from the socket).
    """
    events: list[dict] = []
    while True:
        stripped = buffer.lstrip()
        if not stripped:
            return events, ""
        try:
            obj, end = decoder.raw_decode(stripped)
        except json.JSONDecodeError:
            # Incomplete object — keep what we have and wait for more bytes.
            return events, stripped
        if isinstance(obj, dict):
            events.append(_decode_data_field(obj))
        elif isinstance(obj, list):
            events.extend(
                _decode_data_field(item) for item in obj if isinstance(item, dict)
            )
        buffer = stripped[end:]


async def stream_events(
    host: str = "127.0.0.1",
    port: int = 49123,
    reconnect_delay: float = 2.0,
) -> AsyncIterator[dict]:
    """Yield events from the Rocket League Stats API, reconnecting on drop.

    The game closes the socket between matches, so we loop forever.
    """
    decoder = json.JSONDecoder()
    while True:
        try:
            log.info("connecting to %s:%d", host, port)
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            log.warning("connect failed (%s) — retry in %.1fs", exc, reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            continue

        log.info("connected")
        buffer = ""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    log.info("socket closed by game")
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                events, buffer = parse_json_objects(buffer, decoder)
                for event in events:
                    yield event
                if len(buffer) > MAX_BUFFER_CHARS:
                    log.warning(
                        "buffer exceeded %d chars without parsing an object — dropping",
                        MAX_BUFFER_CHARS,
                    )
                    buffer = ""
        except (ConnectionError, asyncio.IncompleteReadError) as exc:
            log.warning("read error: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        await asyncio.sleep(reconnect_delay)
