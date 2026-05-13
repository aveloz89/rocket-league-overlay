"""Coverage for the orchestration layer in app.py — Hub, handle_event, origin check."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
from state import MatchAggregator  # noqa: E402
from storage import open_db  # noqa: E402


@pytest.fixture
def fresh_state(tmp_path, monkeypatch):
    """Reset the module-level globals so each test gets a clean slate."""
    monkeypatch.setattr(app, "agg", MatchAggregator())
    monkeypatch.setattr(app, "hub", app.Hub())
    test_db = open_db(tmp_path / "stats.db")
    monkeypatch.setattr(app, "db", test_db)
    yield
    test_db.close()


# ── Hub ──────────────────────────────────────────────────────────────────────


def test_hub_caches_latest_match_and_today_for_late_subscribers():
    hub = app.Hub()
    hub.publish({"type": "match", "data": {"score": 100}})
    hub.publish({"type": "today", "data": {"matches": 3}})

    queue = hub.subscribe()
    received = []
    while not queue.empty():
        received.append(json.loads(queue.get_nowait()))

    # Subscriber receives both cached values; today first then match (stable order).
    assert len(received) == 2
    assert received[0]["type"] == "today"
    assert received[1]["type"] == "match"


def test_hub_publish_replaces_cache_per_type():
    hub = app.Hub()
    hub.publish({"type": "match", "data": {"score": 100}})
    hub.publish({"type": "match", "data": {"score": 200}})

    queue = hub.subscribe()
    received = json.loads(queue.get_nowait())
    assert received["data"]["score"] == 200
    assert queue.empty()  # only the latest cached match, no leftover


def test_hub_drops_oldest_when_queue_full():
    hub = app.Hub()
    queue = hub.subscribe()
    # Fill queue beyond maxsize via direct publish
    for i in range(70):
        hub.publish({"type": "match", "data": {"score": i}})
    # Queue size capped at 64; oldest dropped to make room
    assert queue.qsize() <= 64


def test_hub_unsubscribe_stops_delivery():
    hub = app.Hub()
    queue = hub.subscribe()
    hub.unsubscribe(queue)
    hub.publish({"type": "match", "data": {"score": 100}})
    assert queue.empty()


# ── Origin allowlist ─────────────────────────────────────────────────────────


def test_origin_allowed_loopback_and_obs():
    assert app._origin_allowed("http://127.0.0.1:8080") is True
    assert app._origin_allowed("http://localhost:8080") is True
    # OBS Browser Source omits the header entirely
    assert app._origin_allowed(None) is True


def test_origin_null_rejected():
    """Sandboxed iframes (`<iframe sandbox>`) and data:/blob: contexts send
    `Origin: null`. Any external site can mint such a context, so the WS must
    NOT trust it — only the absence of an Origin header (OBS) is allowed."""
    assert app._origin_allowed("null") is False


def test_origin_allowed_any_port_on_loopback():
    """The user picks --port at runtime; the WS check must follow."""
    assert app._origin_allowed("http://127.0.0.1:8765") is True
    assert app._origin_allowed("http://127.0.0.1:3000") is True
    assert app._origin_allowed("http://localhost:9999") is True
    assert app._origin_allowed("http://[::1]:8080") is True


def test_origin_allowed_rejects_external():
    assert app._origin_allowed("https://evil.example.com") is False
    assert app._origin_allowed("http://192.168.1.10:8080") is False
    # LAN addresses must stay rejected even on the default port
    assert app._origin_allowed("http://10.0.0.5:8080") is False


def test_origin_allowed_rejects_lookalike_subdomains():
    """Hostname tail-attacks: `127.0.0.1.evil.com` must NOT match `127.0.0.1`."""
    assert app._origin_allowed("http://127.0.0.1.evil.com") is False
    assert app._origin_allowed("http://localhost.evil.com") is False


def test_origin_allowed_rejects_malformed_or_unknown_scheme():
    assert app._origin_allowed("not-a-url") is False
    assert app._origin_allowed("file:///etc/passwd") is False
    assert app._origin_allowed("javascript:alert(1)") is False


# ── handle_event ─────────────────────────────────────────────────────────────


def test_handle_initialized_resets_aggregator(fresh_state):
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})
    assert app.agg.match_guid == "M1"


def test_handle_update_state_broadcasts_match(fresh_state):
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})
    app.handle_event({
        "Event": "UpdateState",
        "Data": {
            "MatchGuid": "M1",
            "Players": [{"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                         "Score": 100, "Goals": 0, "Shots": 0, "Saves": 0,
                         "Assists": 0, "Demos": 0, "Touches": 0, "Boost": 50,
                         "Speed": 0, "bOnGround": True, "bHasCar": True}],
            "Game": {"Teams": [{"TeamNum": 0, "Score": 0}, {"TeamNum": 1, "Score": 0}],
                     "TimeSeconds": 290, "bOvertime": False, "bReplay": False,
                     "Arena": "stadium", "bHasTarget": False},
        },
    })

    cached = json.loads(app.hub._latest["match"])
    assert cached["data"]["match"]["score"] == 100


def test_match_guid_change_persists_prior_match(fresh_state):
    """If a new MatchGuid arrives without a MatchEnded event, the prior match
    should still be saved before the aggregator resets."""
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"

    def state(guid: str, score: int):
        return {
            "Event": "UpdateState",
            "Data": {
                "MatchGuid": guid,
                "Players": [{"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                             "Score": score, "Goals": 1, "Shots": 1, "Saves": 0,
                             "Assists": 0, "Demos": 0, "Touches": 1, "Boost": 50,
                             "Speed": 0, "bOnGround": True, "bHasCar": True}],
                "Game": {"Teams": [{"TeamNum": 0, "Score": 1}, {"TeamNum": 1, "Score": 0}],
                         "TimeSeconds": 290, "bOvertime": False, "bReplay": False,
                         "Arena": "stadium", "bHasTarget": False},
            },
        }

    app.handle_event(state("MATCH_A", 100))
    app.handle_event(state("MATCH_B", 200))  # rotation without MatchEnded

    rows = app.db.execute("SELECT match_guid, score FROM matches").fetchall()
    assert ("MATCH_A", 100) in rows


def test_match_ended_persists_match(fresh_state):
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})
    app.handle_event({
        "Event": "UpdateState",
        "Data": {
            "MatchGuid": "M1",
            "Players": [{"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                         "Score": 250, "Goals": 1, "Shots": 2, "Saves": 1,
                         "Assists": 0, "Demos": 0, "Touches": 5, "Boost": 50,
                         "Speed": 0, "bOnGround": True, "bHasCar": True}],
            "Game": {"Teams": [{"TeamNum": 0, "Score": 2}, {"TeamNum": 1, "Score": 0}],
                     "TimeSeconds": 0, "bOvertime": False, "bReplay": False,
                     "Arena": "stadium", "bHasTarget": False},
        },
    })
    app.handle_event({"Event": "MatchEnded", "Data": {"MatchGuid": "M1"}})

    row = app.db.execute("SELECT score, won FROM matches WHERE match_guid='M1'").fetchone()
    assert row == (250, 1)


def test_unknown_event_is_ignored(fresh_state):
    """Forward-compat: events the aggregator doesn't recognize must not raise."""
    app.handle_event({"Event": "ReplayStart", "Data": {}})
    app.handle_event({"Event": "Goal", "Data": {}})
    # No assertion needed — absence of exception is the contract


# ── api/today shape ──────────────────────────────────────────────────────────


def test_api_today_returns_uniform_shape_when_no_identity(fresh_state):
    app.agg.me_id = None
    result = asyncio.run(app.api_today())
    # All keys present, all zero
    assert set(result.keys()) == set(app.EMPTY_TODAY.keys())
    assert result == app.EMPTY_TODAY


def test_api_today_returns_real_data_when_identity_known(fresh_state):
    """With an identity configured and a match saved, /api/today aggregates it."""
    from datetime import datetime
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    today_iso = datetime.now().astimezone().isoformat()
    app.db.execute(
        "INSERT INTO matches (match_guid, player_id, started_at, ended_at, won, score, goals, shots) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("M1", "Steam|1|0", today_iso, today_iso, 1, 300, 1, 2),
    )
    app.db.commit()

    result = asyncio.run(app.api_today())
    assert result["matches"] == 1
    assert result["wins"] == 1
    assert result["goals"] == 1


# ── Identity persistence ─────────────────────────────────────────────────────


def test_apply_and_persist_identity_round_trip(fresh_state, tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_PATH", tmp_path / "config.json")

    app.agg.me_id = "Steam|999|0"
    app.agg.me_name = "newuser"
    app._persist_identity()

    # Reset and reload
    app.agg.me_id = None
    app.agg.me_name = None
    app._apply_config_identity()

    assert app.agg.me_id == "Steam|999|0"
    assert app.agg.me_name == "newuser"


# ── _persist_current_match ───────────────────────────────────────────────────


def test_persist_current_match_returns_false_without_match(fresh_state):
    """Without any match data the helper should bail cleanly, not raise."""
    assert app._persist_current_match() is False


def test_persist_current_match_skips_when_db_missing(fresh_state, monkeypatch):
    monkeypatch.setattr(app, "db", None)
    assert app._persist_current_match() is False


# ── tcp_pump resilience ──────────────────────────────────────────────────────


def test_tcp_pump_continues_after_handler_error(fresh_state, monkeypatch):
    """A single bad event must not kill the pump task."""
    events = [
        {"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}},
        {"Event": "UpdateState", "Data": "this is not a dict"},  # malformed
        {"Event": "MatchEnded", "Data": {"MatchGuid": "M1"}},
    ]

    async def fake_stream(**_kwargs):
        for e in events:
            yield e

    monkeypatch.setattr(app, "stream_events", fake_stream)

    # Should complete without raising even though the middle event is bad
    asyncio.run(app.tcp_pump("127.0.0.1", 0))


# ── Windows documents path / .ini install ────────────────────────────────────


def test_install_rl_stats_ini_skips_when_target_exists(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    target_dir = docs / "My Games" / "Rocket League" / "TAGame" / "Config"
    target_dir.mkdir(parents=True)
    (target_dir / "DefaultStatsAPI.ini").write_text("existing")

    monkeypatch.setattr(app, "_windows_documents_path", lambda: docs)
    monkeypatch.setattr(app, "BUNDLED_INI", tmp_path / "DefaultStatsAPI.ini")
    (tmp_path / "DefaultStatsAPI.ini").write_text("new")

    assert app._install_rl_stats_ini() is False
    # Existing file untouched
    assert (target_dir / "DefaultStatsAPI.ini").read_text() == "existing"


def test_install_rl_stats_ini_copies_when_missing(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    target = docs / "My Games" / "Rocket League" / "TAGame" / "Config" / "DefaultStatsAPI.ini"

    bundled = tmp_path / "DefaultStatsAPI.ini"
    bundled.write_text("[TAGame.MatchStatsExporter_TA]\nPort=49123\n")

    monkeypatch.setattr(app, "_windows_documents_path", lambda: docs)
    monkeypatch.setattr(app, "BUNDLED_INI", bundled)

    assert app._install_rl_stats_ini() is True
    assert target.exists()
    assert "Port=49123" in target.read_text()


def test_install_rl_stats_ini_returns_false_if_bundled_missing(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    monkeypatch.setattr(app, "_windows_documents_path", lambda: docs)
    monkeypatch.setattr(app, "BUNDLED_INI", tmp_path / "does-not-exist.ini")

    assert app._install_rl_stats_ini() is False


def test_windows_documents_path_falls_back_on_non_windows(monkeypatch):
    monkeypatch.setattr(app.sys, "platform", "darwin")
    result = app._windows_documents_path()
    assert result == Path.home() / "Documents"


# ── _broadcast_today via handle_event MatchEnded ─────────────────────────────


def test_match_ended_broadcasts_today_even_without_identity(fresh_state):
    """Even with no identity set, MatchEnded broadcasts an empty TODAY snapshot
    rather than silently doing nothing."""
    app.handle_event({"Event": "MatchEnded", "Data": {"MatchGuid": "M1"}})
    cached = json.loads(app.hub._latest["today"])
    assert cached["type"] == "today"
    assert cached["data"] == app.EMPTY_TODAY


# ── Coach broadcast and endpoint ────────────────────────────────────────────


def test_match_ended_broadcasts_coach(fresh_state):
    """MatchEnded should also publish a 'coach' payload so /coach refreshes live."""
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})
    app.handle_event({
        "Event": "UpdateState",
        "Data": {
            "MatchGuid": "M1",
            "Players": [{"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                         "Score": 250, "Goals": 1, "Shots": 2, "Saves": 0,
                         "Assists": 0, "Demos": 0, "Touches": 5, "Boost": 50,
                         "Speed": 0, "bOnGround": True, "bHasCar": True}],
            "Game": {"Teams": [{"TeamNum": 0, "Score": 1}, {"TeamNum": 1, "Score": 0}],
                     "TimeSeconds": 0, "bOvertime": False, "bReplay": False,
                     "Arena": "stadium", "bHasTarget": False},
        },
    })
    app.handle_event({"Event": "MatchEnded", "Data": {"MatchGuid": "M1"}})

    cached = json.loads(app.hub._latest["coach"])
    assert cached["type"] == "coach"
    # The just-saved match should be the last_match in the coach payload
    assert cached["data"]["last_match"]["match_guid"] == "M1"


def test_match_guid_change_broadcasts_coach(fresh_state):
    """Mid-stream match rotation also refreshes the coach payload."""
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"

    def state(guid):
        return {
            "Event": "UpdateState",
            "Data": {
                "MatchGuid": guid,
                "Players": [{"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                             "Score": 100, "Goals": 0, "Shots": 0, "Saves": 0,
                             "Assists": 0, "Demos": 0, "Touches": 0, "Boost": 50,
                             "Speed": 0, "bOnGround": True, "bHasCar": True}],
                "Game": {"Teams": [{"TeamNum": 0, "Score": 0}, {"TeamNum": 1, "Score": 0}],
                         "TimeSeconds": 290, "bOvertime": False, "bReplay": False,
                         "Arena": "stadium", "bHasTarget": False},
            },
        }

    app.handle_event(state("MATCH_A"))
    app.handle_event(state("MATCH_B"))  # rotation triggers coach broadcast

    assert "coach" in app.hub._latest


def test_api_coach_returns_empty_when_no_identity(fresh_state):
    app.agg.me_id = None
    result = asyncio.run(app.api_coach())
    assert result == app.EMPTY_COACH


def test_api_coach_returns_data_when_identity_known(fresh_state):
    """With an identity and saved matches, /api/coach returns the coach view."""
    from datetime import datetime
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    today_iso = datetime.now().astimezone().isoformat()
    app.db.execute(
        "INSERT INTO matches (match_guid, player_id, started_at, ended_at, won, "
        "score, goals, shots) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("M1", "Steam|1|0", today_iso, today_iso, 1, 300, 1, 2),
    )
    app.db.commit()

    result = asyncio.run(app.api_coach())
    assert result["last_match"]["match_guid"] == "M1"
    assert result["today"]["matches"] == 1


def test_hub_caches_coach_payload_for_late_subscribers():
    """A client connecting after a coach payload was published must receive it."""
    # Earlier asyncio.run() calls in this file close the default loop. On 3.9
    # asyncio.Queue() needs a current loop at construction time — install a
    # fresh one before subscribing.
    asyncio.set_event_loop(asyncio.new_event_loop())
    hub = app.Hub()
    hub.publish({"type": "coach", "data": {"last_match": None, "insights": []}})

    queue = hub.subscribe()
    received = []
    while not queue.empty():
        received.append(json.loads(queue.get_nowait()))

    assert any(m["type"] == "coach" for m in received)


# ── StatfeedEvent dispatch ───────────────────────────────────────────────────


# T19: handle_event with StatfeedEvent calls agg.on_statfeed_event
def test_handle_statfeed_event_increments_counter(fresh_state):
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})

    app.handle_event({
        "Event": "StatfeedEvent",
        "Data": {
            "MatchGuid": "M1",
            "Event": "EpicSave",
            "Causer": {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0},
            "Victim": {"Name": "jstn", "PrimaryId": "Epic|2|0", "TeamNum": 1},
        },
    })

    assert app.agg.epic_saves == 1


def test_handle_statfeed_event_no_broadcast(fresh_state):
    """StatfeedEvent must NOT trigger a match broadcast (no WS noise mid-game)."""
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})

    # Capture hub state before and after
    before_latest = dict(app.hub._latest)

    app.handle_event({
        "Event": "StatfeedEvent",
        "Data": {
            "MatchGuid": "M1",
            "Event": "EpicSave",
            "Causer": {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0},
            "Victim": {"Name": "jstn", "PrimaryId": "Epic|2|0", "TeamNum": 1},
        },
    })

    # "match" key must be unchanged — no broadcast was made by statfeed handler
    assert app.hub._latest.get("match") == before_latest.get("match")


# T20: integration — full match with statfeed events → coach_stats exposes counters
def test_integration_statfeed_persisted_in_coach_stats(fresh_state):
    """A complete match flow with StatfeedEvents results in highlights in coach_stats."""
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    app.handle_event({"Event": "MatchInitialized", "Data": {"MatchGuid": "M1"}})

    # One UpdateState so started_at is set and player identity confirmed
    app.handle_event({
        "Event": "UpdateState",
        "Data": {
            "MatchGuid": "M1",
            "Players": [{"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                         "Score": 100, "Goals": 1, "Shots": 2, "Saves": 1,
                         "Assists": 0, "Demos": 0, "Touches": 5, "Boost": 50,
                         "Speed": 0, "bOnGround": True, "bHasCar": True}],
            "Game": {"Teams": [{"TeamNum": 0, "Score": 1}, {"TeamNum": 1, "Score": 0}],
                     "TimeSeconds": 290, "bOvertime": False, "bReplay": False,
                     "Arena": "stadium", "bHasTarget": False},
        },
    })

    # Statfeed events arrive during the match
    for _ in range(2):
        app.handle_event({
            "Event": "StatfeedEvent",
            "Data": {
                "MatchGuid": "M1",
                "Event": "EpicSave",
                "Causer": {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0},
                "Victim": {"Name": "jstn", "PrimaryId": "Epic|2|0", "TeamNum": 1},
            },
        })
    app.handle_event({
        "Event": "StatfeedEvent",
        "Data": {
            "MatchGuid": "M1",
            "Event": "HatTrick",
            "Causer": {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0},
            "Victim": None,
        },
    })

    app.handle_event({"Event": "MatchEnded", "Data": {"MatchGuid": "M1"}})

    from storage import coach_stats
    result = coach_stats(app.db, "Steam|1|0")
    last = result["last_match"]
    assert last["epic_saves"] == 2
    assert last["hat_tricks"] == 1


# ── POST /api/config/rank ────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with a clean DB and isolated config path."""
    import storage
    from fastapi.testclient import TestClient
    from state import MatchAggregator

    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(app, "agg", MatchAggregator())
    monkeypatch.setattr(app, "hub", app.Hub())
    test_db = open_db(tmp_path / "stats.db")
    monkeypatch.setattr(app, "db", test_db)
    with TestClient(app.app) as c:
        yield c
    test_db.close()


def test_set_rank_valid_returns_200(client, tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_PATH", tmp_path / "config.json")

    resp = client.post("/api/config/rank", json={"rank": "diamond"})
    assert resp.status_code == 200
    assert resp.json() == {"rank": "diamond"}


def test_set_rank_persists_to_config(client, tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_PATH", tmp_path / "config.json")

    client.post("/api/config/rank", json={"rank": "champion"})
    cfg = storage.load_config()
    assert cfg.get("target_rank") == "champion"


def test_set_rank_null_clears_target(client, tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_PATH", tmp_path / "config.json")

    client.post("/api/config/rank", json={"rank": "diamond"})
    resp = client.post("/api/config/rank", json={"rank": None})
    assert resp.status_code == 200
    assert resp.json() == {"rank": None}
    cfg = storage.load_config()
    assert cfg.get("target_rank") is None


def test_set_rank_invalid_tier_returns_422(client):
    resp = client.post("/api/config/rank", json={"rank": "mythical_legend"})
    assert resp.status_code == 422


def test_set_rank_missing_body_returns_422(client):
    resp = client.post("/api/config/rank", json={})
    # Pydantic v2 requires the field — missing body key raises 422
    # rank field has no default so it's required
    assert resp.status_code == 422


def test_api_coach_includes_rank_benchmark_when_configured(fresh_state, tmp_path, monkeypatch):
    """When target_rank is in config, /api/coach should include rank_benchmark."""
    import storage
    monkeypatch.setattr(storage, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(storage, "CONFIG_PATH", tmp_path / "config.json")

    storage.save_config({"target_rank": "diamond"})
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"

    result = asyncio.run(app.api_coach())
    # rank_benchmark may be None if JSON not present, but key must exist
    assert "rank_benchmark" in result


def test_api_coach_filters_by_mode_query_param(fresh_state):
    """Matches with different team_size are partitioned by ?mode=N."""
    from datetime import datetime

    today_iso = datetime.now().astimezone().isoformat()
    app.agg.me_id, app.agg.me_name = "Steam|1|0", "alas"
    for guid, ts in (("ONES", 1), ("TWOS", 2), ("THREES", 3)):
        app.db.execute(
            "INSERT INTO matches (match_guid, player_id, started_at, ended_at, won, "
            "score, goals, shots, team_size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guid, "Steam|1|0", today_iso, today_iso, 1, 300, 1, 2, ts),
        )
    app.db.commit()

    assert asyncio.run(app.api_coach(mode=2))["last_match"]["match_guid"] == "TWOS"
    assert asyncio.run(app.api_coach(mode=3))["last_match"]["match_guid"] == "THREES"
    assert asyncio.run(app.api_today())["matches"] == 3
    assert asyncio.run(app.api_today(mode=2))["matches"] == 1


# ── /coach legacy redirect ───────────────────────────────────────────────────


def test_coach_route_redirects_to_root(client):
    """The /coach URL was unified into /; existing bookmarks must keep working."""
    resp = client.get("/coach", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/"


# ── Cache-Control on shell + assets ──────────────────────────────────────────
# Stale JS/CSS mixed with a fresh index.html can wedge the dashboard on
# "connecting…" without any visible error, so we force browsers to revalidate.


def test_index_sends_no_cache_header(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_static_asset_sends_no_cache_header(client):
    resp = client.get("/static/overlay.js")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")
