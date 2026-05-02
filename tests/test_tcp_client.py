import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tcp_client import split_json_lines, stream_events  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def test_split_complete_lines():
    payload = (FIXTURES / "update_state.json").read_text()
    minified = json.dumps(json.loads(payload))
    buffer = (minified + "\n" + minified + "\n").encode()

    events, leftover = split_json_lines(buffer)

    assert len(events) == 2
    assert leftover == b""
    assert events[0]["Event"] == "UpdateState"
    assert events[0]["Data"]["Game"]["Teams"][0]["Name"] == "Blue"


def test_split_keeps_partial_tail():
    full = json.dumps({"Event": "UpdateState", "Data": {}})
    partial = json.dumps({"Event": "BallHit"})[:20]
    buffer = (full + "\n" + partial).encode()

    events, leftover = split_json_lines(buffer)

    assert len(events) == 1
    assert leftover.decode() == partial


def test_split_drops_invalid_line_but_continues():
    valid = json.dumps({"Event": "UpdateState", "Data": {}})
    buffer = (valid + "\n{not json}\n" + valid + "\n").encode()

    events, leftover = split_json_lines(buffer)

    assert leftover == b""
    assert len(events) == 2


def test_split_skips_empty_lines():
    valid = json.dumps({"Event": "UpdateState"})
    buffer = (valid + "\n\n\n" + valid + "\n").encode()

    events, _ = split_json_lines(buffer)

    assert len(events) == 2


def test_split_drops_buffer_when_exceeds_max_line_size():
    """If the source sends a huge blob without a newline, we drop it instead of
    growing the buffer unbounded."""
    from tcp_client import MAX_LINE_BYTES

    huge = b"x" * (MAX_LINE_BYTES + 100)
    events, leftover = split_json_lines(huge)

    assert events == []
    assert leftover == b""


def test_stream_events_yields_from_fake_server():
    sample = {"Event": "UpdateState", "Data": {"MatchGuid": "abc"}}

    async def runner():
        # Spin up a dummy TCP server that emits 3 lines and closes
        async def serve(_reader, writer):
            for _ in range(3):
                writer.write((json.dumps(sample) + "\n").encode())
                await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async with server:
            received: list[dict] = []
            agen = stream_events(host="127.0.0.1", port=port, reconnect_delay=0.01)
            async for event in agen:
                received.append(event)
                if len(received) == 3:
                    await agen.aclose()
                    break
            return received

    result = asyncio.run(runner())
    assert len(result) == 3
    assert all(e["Event"] == "UpdateState" for e in result)
    assert result[0]["Data"]["MatchGuid"] == "abc"
