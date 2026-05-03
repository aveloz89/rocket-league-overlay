import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tcp_client import _decode_data_field, parse_json_objects, stream_events  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _decoder() -> json.JSONDecoder:
    return json.JSONDecoder()


def test_parse_objects_back_to_back_no_separator():
    """Two minified objects glued together — what RL actually sends in practice."""
    a = json.dumps({"Event": "UpdateState", "Data": {"MatchGuid": "A"}})
    b = json.dumps({"Event": "BallHit", "Data": {"MatchGuid": "A"}})

    events, leftover = parse_json_objects(a + b, _decoder())

    assert leftover == ""
    assert [e["Event"] for e in events] == ["UpdateState", "BallHit"]


def test_parse_objects_with_arbitrary_whitespace():
    a = json.dumps({"Event": "UpdateState"})
    b = json.dumps({"Event": "BallHit"})

    events, leftover = parse_json_objects(a + " \n\t " + b + "\n", _decoder())

    assert leftover == ""
    assert [e["Event"] for e in events] == ["UpdateState", "BallHit"]


def test_parse_pretty_printed_object_with_internal_newlines():
    """The pre-fix code split on \\n, which tore pretty-printed objects apart."""
    payload = (FIXTURES / "update_state.json").read_text()  # multi-line indented JSON

    events, leftover = parse_json_objects(payload, _decoder())

    assert leftover == ""
    assert len(events) == 1
    assert events[0]["Event"] == "UpdateState"
    assert events[0]["Data"]["Game"]["Teams"][0]["Name"] == "Blue"


def test_parse_keeps_partial_tail_for_next_read():
    full = json.dumps({"Event": "UpdateState", "Data": {}})
    partial_head = '{"Event": "BallH'

    events, leftover = parse_json_objects(full + partial_head, _decoder())

    assert len(events) == 1
    assert leftover == partial_head


def test_parse_top_level_array_is_flattened_into_objects():
    """The fixtures include a BallHit array; some Stats API entries arrive batched."""
    payload = (FIXTURES / "ball_hit_array.json").read_text()

    events, leftover = parse_json_objects(payload, _decoder())

    assert leftover == ""
    assert len(events) == 2
    assert all(e["Event"] == "BallHit" for e in events)


def test_parse_returns_no_events_on_pure_whitespace():
    events, leftover = parse_json_objects("   \n\n  \t  ", _decoder())

    assert events == []
    assert leftover == ""


def test_parse_decodes_string_encoded_data_field():
    """RL wraps the Data payload as a JSON-encoded string, not a nested object."""
    inner = '{"MatchGuid":"M1","Players":[{"Name":"alas"}]}'
    raw = json.dumps({"Event": "BallHit", "Data": inner})

    events, leftover = parse_json_objects(raw, _decoder())

    assert leftover == ""
    assert len(events) == 1
    assert events[0]["Event"] == "BallHit"
    # Data must arrive as a dict for the handler chain (data.get(...))
    assert isinstance(events[0]["Data"], dict)
    assert events[0]["Data"]["MatchGuid"] == "M1"
    assert events[0]["Data"]["Players"][0]["Name"] == "alas"


def test_decode_data_field_leaves_dict_data_untouched():
    """Defensive: if a fixture or future RL build sends Data as a real dict,
    don't try to re-decode it."""
    event = {"Event": "BallHit", "Data": {"MatchGuid": "M1"}}

    out = _decode_data_field(event)

    assert out["Data"] == {"MatchGuid": "M1"}


def test_decode_data_field_leaves_string_in_place_when_inner_invalid():
    event = {"Event": "BallHit", "Data": "{not json"}

    out = _decode_data_field(event)

    assert out["Data"] == "{not json"


def test_stream_events_yields_minified_back_to_back():
    sample = {"Event": "UpdateState", "Data": {"MatchGuid": "abc"}}

    async def runner():
        async def serve(_reader, writer):
            # Three objects with no separator at all — the worst case the old
            # newline parser failed on silently in production.
            writer.write((json.dumps(sample) * 3).encode())
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


def test_stream_events_yields_pretty_printed():
    """Real RL traffic is pretty-printed; verify the end-to-end pump handles it."""

    async def runner():
        async def serve(_reader, writer):
            payload = (FIXTURES / "update_state.json").read_text()
            writer.write(payload.encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async with server:
            received: list[dict] = []
            agen = stream_events(host="127.0.0.1", port=port, reconnect_delay=0.01)
            async for event in agen:
                received.append(event)
                await agen.aclose()
                break
            return received

    result = asyncio.run(runner())
    assert len(result) == 1
    assert result[0]["Event"] == "UpdateState"
