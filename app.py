from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from typing import Optional

from state import MatchAggregator
from storage import (
    RANK_TIERS,
    coach_stats,
    load_config,
    open_db,
    save_config,
    save_match,
    today_stats,
)
from tcp_client import stream_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rl-overlay")


def _resource_path(rel: str) -> Path:
    """Resolve a bundled resource path. PyInstaller --onefile sets sys._MEIPASS."""
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).parent
    return Path(base) / rel


STATIC_DIR = _resource_path("static")
BUNDLED_INI = _resource_path("DefaultStatsAPI.ini")

# UpdateState comes at PacketSendRate (default 60Hz) — throttle to 15 fps for the UI
UPDATE_STATE_MIN_INTERVAL = 1 / 15
MATCH_END_EVENTS = {"MatchEnded", "PodiumStart"}


class Hub:
    """Fan-out broadcaster.

    Caches the latest payload per message type (`match` / `today`) so a fresh
    subscriber gets the full picture on connect. Replays in a stable order
    (today first, then match) so the today panel never gets clobbered by an
    old live match snapshot.
    """

    CACHED_TYPES = ("today", "coach", "match")

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
        self._latest: dict[str, str] = {}

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._clients.add(q)
        for key in self.CACHED_TYPES:
            cached = self._latest.get(key)
            if cached is not None:
                try:
                    q.put_nowait(cached)
                except asyncio.QueueFull:
                    pass
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._clients.discard(q)

    def publish(self, payload: dict) -> None:
        msg_type = payload.get("type")
        body = json.dumps(payload)
        if msg_type in self.CACHED_TYPES:
            self._latest[msg_type] = body
        for q in list(self._clients):
            try:
                q.put_nowait(body)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(body)
                except asyncio.QueueEmpty:
                    pass


# OBS Browser Source omits Origin; browsers always set it. Restrict to loopback
# so a malicious page in the user's browser can't open a WS to read PII.
# We accept any port — the user controls --port via CLI, and same-machine origin
# is the threat model we care about, not a specific port.
# Note: "null" Origin is intentionally rejected. It's set by sandboxed iframes
# (`<iframe sandbox>` without `allow-same-origin`) and `data:`/`blob:` contexts —
# any external site can mint such an iframe and would otherwise reach this WS.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _origin_allowed(origin: str | None) -> bool:
    if origin is None:  # OBS Browser Source omits the header
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https", "ws", "wss"):
        return False
    return (parsed.hostname or "") in LOOPBACK_HOSTS


hub = Hub()
agg = MatchAggregator()
db = None  # set in lifespan


def _apply_config_identity() -> None:
    cfg = load_config()
    if cfg.get("player_id"):
        agg.me_id = cfg["player_id"]
        agg.me_name = cfg.get("player_name")
        log.info("loaded saved identity: %s (%s)", agg.me_name, agg.me_id)


def _persist_identity() -> None:
    cfg = load_config()
    cfg.update({"player_id": agg.me_id, "player_name": agg.me_name})
    save_config(cfg)


EMPTY_TODAY = {
    "matches": 0, "wins": 0, "losses": 0, "win_rate": 0,
    "goals": 0, "shots": 0, "shot_accuracy": 0, "saves": 0, "assists": 0,
    "demos": 0, "demos_taken": 0, "touches": 0,
    "avg_score": 0, "best_score": 0, "avg_boost": 0, "avg_supersonic_pct": 0,
    "total_ball_hits": 0, "best_hit": 0, "win_streak": 0,
}

EMPTY_COACH = {
    "last_match": None,
    "rolling_avg": {"count": 0},
    "trend": [],
    "insights": [],
    "today": dict(EMPTY_TODAY),
    "rank_benchmark": None,
}


class SetRankRequest(BaseModel):
    rank: Optional[str]

    @field_validator("rank")
    @classmethod
    def _valid_tier(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in RANK_TIERS:
            raise ValueError(f"rank must be one of {RANK_TIERS} or null")
        return v


def _resolve_target_rank() -> Optional[str]:
    """Read target_rank from config; return None if missing or invalid tier."""
    cfg = load_config()
    rank = cfg.get("target_rank")
    if rank not in RANK_TIERS:
        return None
    return rank


def _broadcast_match() -> None:
    snap = agg.to_overlay_dict()
    hub.publish({"type": "match", "data": snap})


def _broadcast_today() -> None:
    if not agg.me_id or db is None:
        hub.publish({"type": "today", "data": dict(EMPTY_TODAY)})
        return
    hub.publish({"type": "today", "data": today_stats(db, agg.me_id)})


def _broadcast_coach() -> None:
    if not agg.me_id or db is None:
        hub.publish({"type": "coach", "data": dict(EMPTY_COACH)})
        return
    target_rank = _resolve_target_rank()
    hub.publish({"type": "coach", "data": coach_stats(db, agg.me_id, target_rank)})


def _persist_current_match() -> bool:
    """Persist the current match if we have enough info. Returns True on insert."""
    if db is None:
        return False
    snap = agg.to_db_snapshot()
    if save_match(db, snap):
        log.info("saved match %s (won=%s)", snap["match_guid"], snap["won"])
        return True
    return False


def handle_event(event: dict) -> None:
    name = event.get("Event")
    data = event.get("Data") or {}

    if name == "Initialized":
        # If a previous match was in progress and never sent MatchEnded, persist
        # it before resetting the aggregator.
        if agg.match_guid:
            _persist_current_match()
        agg.on_initialized(data)
        _broadcast_match()
        return

    if name == "UpdateState":
        # Same defense for the case where the game rotates MatchGuid without
        # an Initialized event (e.g. spectator transitions).
        if agg.is_match_guid_change(data):
            _persist_current_match()
            _broadcast_today()
            _broadcast_coach()
        detected = agg.on_update_state(data)
        if detected:
            log.info("identity detected: %s (%s)", detected["name"], detected["id"])
            _persist_identity()
        _broadcast_match()
        return

    if name == "BallHit":
        agg.on_ball_hit(data)
        _broadcast_match()
        return

    if name == "StatfeedEvent":
        agg.on_statfeed_event(data)
        return

    if name in MATCH_END_EVENTS:
        _persist_current_match()
        _broadcast_today()
        _broadcast_coach()
        return


async def tcp_pump(host: str, port: int) -> None:
    last_update = 0.0
    async for event in stream_events(host=host, port=port):
        if event.get("Event") == "UpdateState":
            now = time.monotonic()
            if now - last_update < UPDATE_STATE_MIN_INTERVAL:
                continue
            last_update = now
        try:
            handle_event(event)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # A single malformed event must not kill the pump task. Log and continue.
            log.warning("dropped event due to handler error: %s — payload=%r", exc, event)


async def _demo_match() -> None:  # pragma: no cover — preview-only synthetic events
    """Run one synthetic demo match. Returns when MatchEnded is fired."""
    me_id = "Steam|76561197960409023|0"
    me_name = "alas"
    teammate = {"Name": "kuxir", "PrimaryId": "Steam|2|0", "TeamNum": 0, "Shortcut": 2}
    opp = {"Name": "jstn", "PrimaryId": "Epic|3|0", "TeamNum": 1, "Shortcut": 3}

    def _player_state(base: dict, **extra) -> dict:
        return {
            **base,
            "Score": 0, "Goals": 0, "Shots": 0, "Saves": 0, "Assists": 0,
            "Demos": 0, "Touches": 0, "CarTouches": 0,
            "bOnGround": True, "bHasCar": True, "Speed": 0.0, "Boost": 50,
            "Location": {"X": 0.0, "Y": -2000.0, "Z": 17.0},
            **extra,
        }

    me_state = _player_state(
        {"Name": me_name, "PrimaryId": me_id, "TeamNum": 0, "Shortcut": 1}
    )
    teammate_state = _player_state(teammate, Location={"X": 1500.0, "Y": -1500.0, "Z": 17.0})
    opp_state = _player_state(opp, Location={"X": 0.0, "Y": 2000.0, "Z": 17.0})
    ball_loc = {"X": 0.0, "Y": 0.0, "Z": 90.0}

    handle_event({"Event": "Initialized", "Data": {"MatchGuid": f"DEMO-{int(time.time())}"}})

    blue_score = 0
    orange_score = 0
    elapsed = 0.0
    next_event_in = 1.5

    # Short demo match (real time ~20s) so the today panel populates quickly
    while elapsed < 20:
        # Live tick: fluctuate boost, speed, ground state and positioning
        for s in (me_state, teammate_state, opp_state):
            # Occasional pad pickup so big/small/stolen counters move
            if random.random() < 0.04 and s["Boost"] <= 12:
                s["Boost"] = 100
            elif random.random() < 0.08:
                s["Boost"] = min(100, s["Boost"] + 12)
            else:
                s["Boost"] = max(0, min(100, s["Boost"] + random.randint(-7, 4)))
            # Speed mostly below supersonic (22) so % supersonic looks realistic
            s["Speed"] = max(0.0, min(35.0, s["Speed"] * 0.6 + random.uniform(0, 20)))
            s["bOnGround"] = random.random() > 0.25
            loc = s["Location"]
            loc["X"] = max(-3500.0, min(3500.0, loc["X"] + random.uniform(-200, 200)))
            loc["Y"] = max(-4500.0, min(4500.0, loc["Y"] + random.uniform(-300, 300)))
            loc["Z"] = 17.0 if s["bOnGround"] else random.uniform(150.0, 1200.0)

        ball_loc["X"] = max(-3500.0, min(3500.0, ball_loc["X"] + random.uniform(-300, 300)))
        ball_loc["Y"] = max(-4500.0, min(4500.0, ball_loc["Y"] + random.uniform(-400, 400)))
        ball_loc["Z"] = max(90.0, min(1500.0, ball_loc["Z"] + random.uniform(-100, 200)))

        update = {
            "Event": "UpdateState",
            "Data": {
                "MatchGuid": agg.match_guid or "DEMO",
                "Players": [me_state, teammate_state, opp_state],
                "Game": {
                    "Teams": [
                        {"Name": "Blue", "TeamNum": 0, "Score": blue_score,
                         "ColorPrimary": "1873FF", "ColorSecondary": "E5E5E5"},
                        {"Name": "Orange", "TeamNum": 1, "Score": orange_score,
                         "ColorPrimary": "C26418", "ColorSecondary": "E5E5E5"},
                    ],
                    "TimeSeconds": max(0, 300 - int(elapsed)),
                    "bOvertime": False,
                    "Ball": {
                        "Speed": random.uniform(20, 90),
                        "TeamNum": 1,
                        "Location": dict(ball_loc),
                    },
                    "bReplay": False,
                    "bHasWinner": False,
                    "Winner": "",
                    "Arena": "cs_p",
                    "bHasTarget": True,
                    "Target": {"Name": me_name, "Shortcut": 1, "TeamNum": 0},
                },
            },
        }
        handle_event(update)

        # Discrete events sprinkled through the match
        next_event_in -= 1 / 15
        if next_event_in <= 0:
            roll = random.random()
            if roll < 0.45:
                # Me hits ball
                me_state["Touches"] += 1
                handle_event({
                    "Event": "BallHit",
                    "Data": {
                        "MatchGuid": agg.match_guid,
                        "Players": [{"Name": me_name, "Shortcut": 1, "TeamNum": 0}],
                        "Ball": {
                            "PreHitSpeed": random.uniform(20, 60),
                            "PostHitSpeed": random.uniform(60, 110),
                            "Location": dict(ball_loc),
                        },
                    },
                })
            elif roll < 0.7:
                # Me shot
                me_state["Shots"] += 1
                me_state["Score"] += 50
                if random.random() < 0.4:
                    me_state["Goals"] += 1
                    me_state["Score"] += 100
                    blue_score += 1
            elif roll < 0.82:
                # Save
                me_state["Saves"] += 1
                me_state["Score"] += 75
            elif roll < 0.9:
                # Demo by opponent (you go bHasCar=False briefly)
                me_state["bHasCar"] = False
            elif roll < 0.95:
                # Opponent goal
                orange_score += 1
                opp_state["Goals"] += 1
            else:
                # Opponent ball touch
                handle_event({
                    "Event": "BallHit",
                    "Data": {
                        "MatchGuid": agg.match_guid,
                        "Players": [{"Name": opp["Name"], "Shortcut": 3, "TeamNum": 1}],
                        "Ball": {
                            "PreHitSpeed": random.uniform(20, 60),
                            "PostHitSpeed": random.uniform(40, 90),
                            "Location": {"X": 0, "Y": 0, "Z": 100},
                        },
                    },
                })
            next_event_in = random.uniform(1.5, 3.5)

        # Respawn after demo
        if not me_state["bHasCar"] and random.random() < 0.3:
            me_state["bHasCar"] = True

        await asyncio.sleep(1 / 15)
        elapsed += 1 / 15

    handle_event({"Event": "MatchEnded", "Data": {"MatchGuid": agg.match_guid}})


async def demo_pump() -> None:  # pragma: no cover — preview-only entry point
    """Run demo matches in a loop. Iterative to avoid stack growth."""
    while True:
        await _demo_match()
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover — exercised via uvicorn at runtime
    global db
    # RL_OVERLAY_DB lets the operator point at an alternate SQLite file — handy
    # for demo runs that shouldn't touch the user's real history.
    db_override = os.environ.get("RL_OVERLAY_DB")
    db = open_db(Path(db_override)) if db_override else open_db()
    _apply_config_identity()

    if app.state.demo:
        agg.me_id = "Steam|76561197960409023|0"
        agg.me_name = "alas"
        task = asyncio.create_task(demo_pump(), name="demo_pump")
    else:
        task = asyncio.create_task(
            tcp_pump(app.state.tcp_host, app.state.tcp_port), name="tcp_pump"
        )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if db is not None:
            db.close()


app = FastAPI(lifespan=lifespan)
app.state.tcp_host = "127.0.0.1"
app.state.tcp_port = 49123
app.state.demo = False

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/coach")
async def coach() -> FileResponse:
    return FileResponse(STATIC_DIR / "coach.html")


@app.get("/api/today")
async def api_today() -> dict:
    if not agg.me_id or db is None:
        return dict(EMPTY_TODAY)
    # Run SQLite I/O in a thread so a slow disk doesn't block the event loop.
    return await asyncio.to_thread(today_stats, db, agg.me_id)


@app.get("/api/coach")
async def api_coach() -> dict:
    if not agg.me_id or db is None:
        return dict(EMPTY_COACH)
    target_rank = _resolve_target_rank()
    return await asyncio.to_thread(coach_stats, db, agg.me_id, target_rank)


@app.post("/api/config/rank")
async def set_rank(req: SetRankRequest) -> dict:
    cfg = load_config()
    if req.rank is None:
        cfg.pop("target_rank", None)
    else:
        cfg["target_rank"] = req.rank
    save_config(cfg)
    _broadcast_coach()
    return {"rank": req.rank}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = hub.subscribe()
    try:
        while True:
            payload = await queue.get()
            # Bound the send so a stuck/slow client can't block us indefinitely.
            await asyncio.wait_for(websocket.send_text(payload), timeout=5.0)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        hub.unsubscribe(queue)


def _windows_documents_path() -> Path:
    """Return the user's Documents folder on Windows (handles OneDrive redirection)."""
    if sys.platform != "win32":
        return Path.home() / "Documents"
    import ctypes
    import ctypes.wintypes
    CSIDL_PERSONAL = 5
    SHGFP_TYPE_CURRENT = 0
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
    return Path(buf.value)


def _install_rl_stats_ini() -> bool:
    """Copy the bundled DefaultStatsAPI.ini to RL's config folder if not already there.
    Returns True if a fresh install was performed (user must restart RL)."""
    rl_cfg = _windows_documents_path() / "My Games" / "Rocket League" / "TAGame" / "Config"
    target = rl_cfg / "DefaultStatsAPI.ini"
    if target.exists():
        return False
    if not BUNDLED_INI.exists():
        log.warning("bundled DefaultStatsAPI.ini not found — set it up manually")
        return False
    rl_cfg.mkdir(parents=True, exist_ok=True)
    target.write_bytes(BUNDLED_INI.read_bytes())
    log.warning("=" * 60)
    log.warning(" DefaultStatsAPI.ini was just installed to:")
    log.warning(" %s", target)
    log.warning(" RESTART ROCKET LEAGUE for the API to activate.")
    log.warning("=" * 60)
    return True


def _open_browser_when_ready(url: str) -> None:  # pragma: no cover — threading + webbrowser
    """Open the user's browser shortly after uvicorn binds the port."""
    import threading
    import webbrowser

    def _delayed():
        time.sleep(1.0)
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 — webbrowser failure is non-fatal
            log.warning("could not open browser: %s", exc)

    threading.Thread(target=_delayed, daemon=True).start()


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Rocket League live stats overlay")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--rl-host", default="127.0.0.1", help="RL Stats API host")
    parser.add_argument("--rl-port", type=int, default=49123, help="RL Stats API port")
    parser.add_argument("--demo", action="store_true", help="Emit fake events (no game required)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser")
    parser.add_argument("--no-ini-setup", action="store_true",
                        help="Skip auto-install of DefaultStatsAPI.ini in the RL config folder")
    args = parser.parse_args()

    app.state.tcp_host = args.rl_host
    app.state.tcp_port = args.rl_port
    app.state.demo = args.demo

    if not args.demo and not args.no_ini_setup and sys.platform == "win32":
        _install_rl_stats_ini()

    if args.host not in ("127.0.0.1", "localhost"):
        log.warning("=" * 60)
        log.warning(" Binding to %s — overlay (incl. PrimaryId) will be reachable", args.host)
        log.warning(" from your LAN. There is no auth. Use 127.0.0.1 unless you")
        log.warning(" really need remote access.")
        log.warning("=" * 60)

    if not args.no_browser:
        _open_browser_when_ready(f"http://{args.host}:{args.port}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
