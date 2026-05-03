from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
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

# UpdateState comes at PacketSendRate (default 60Hz) — throttle to one update
# every 5 s for the UI. The overlay is for aggregated stats; live SPEED/BOOST
# already show in the in-game HUD, so we don't need frame-rate updates here.
# Discrete events (BallHit, goals, shots) still broadcast immediately.
UPDATE_STATE_MIN_INTERVAL = 5.0
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


DEMO_MATCH_SNAPSHOT = {
    "me": {"id": "Steam|76561197960409023|0", "name": "alas", "team": 0},
    "context": {
        "blue": 2, "orange": 1, "clock": 187,
        "overtime": False, "replay": False, "arena": "cs_p",
    },
    "match": {
        "score": 425, "goals": 2, "shots": 5, "shot_accuracy": 40,
        "saves": 3, "assists": 1, "touches": 18,
        "demos_given": 1, "demos_taken": 2,
        "boost": 62, "boost_avg": 54, "time_zero_boost_pct": 4,
        "time_supersonic_pct": 48, "time_airborne_pct": 22,
        "hardest_hit": 87.4, "last_touch": "self", "last_touch_name": "alas",
        "speed": 18.2, "ball_hits": 14, "avg_shot_power": 71.2,
        "score_per_min": 425, "goal_participation_pct": 67,
        "possession_pct": 62, "boost_wasted_pct": 7,
    },
}

DEMO_TODAY_SNAPSHOT = {
    "matches": 12, "wins": 7, "losses": 5, "win_rate": 58,
    "goals": 14, "shots": 38, "shot_accuracy": 37,
    "saves": 22, "assists": 5, "demos": 4, "demos_taken": 9, "touches": 167,
    "avg_score": 318, "best_score": 612,
    "avg_boost": 54, "avg_supersonic_pct": 48,
    "total_ball_hits": 167, "best_hit": 109.4, "win_streak": 3,
}

DEMO_COACH_SNAPSHOT = {
    "last_match": {
        "id": 12, "match_guid": "DEMO-FIXED", "player_name": "alas",
        "started_at": "2026-05-02T20:00:00+00:00",
        "ended_at": "2026-05-02T20:05:00+00:00",
        "blue_score": 4, "orange_score": 2, "me_team": 0, "won": True,
        "score": 425, "goals": 2, "shots": 5, "saves": 3, "assists": 1,
        "demos": 1, "demos_taken": 2, "touches": 18,
        "boost_avg": 54.0, "time_zero_boost_pct": 4.0,
        "time_supersonic_pct": 48.0, "time_airborne_pct": 22.0,
        "hardest_hit": 87.4, "ball_hits": 14, "avg_shot_power": 71.2,
        "boost_wasted_pct": 7.0, "possession_pct": 62.0, "score_per_min": 425,
        "time_def_third_pct": 38.0, "time_off_third_pct": 12.0,
        "behind_ball_pct": 81.0, "last_back_pct": 47.0, "dist_to_ball_avg": 3120.0,
        "big_pads": 9, "small_pads": 22, "boost_stolen": 5,
        "aerial_touches": 4, "fast_aerials": 11,
        "epic_saves": 1, "hat_tricks": 0, "aerial_goals": 1, "bicycle_goals": 0,
        "long_goals": 0, "centers": 2, "pool_shots": 0, "saviors": 1, "mvps": 1,
        "shot_accuracy": 40, "air_touch_pct": 28, "duration_min": 5,
    },
    "rolling_avg": {
        "count": 20,
        "score": 312.0, "goals": 1.4, "shots": 3.7, "saves": 2.1, "assists": 0.6,
        "score_per_min": 392.0, "possession_pct": 58.0,
        "boost_avg": 49.0, "boost_wasted_pct": 8.5,
        "time_zero_boost_pct": 6.0, "time_supersonic_pct": 44.0, "time_airborne_pct": 19.0,
        "time_def_third_pct": 42.0, "time_off_third_pct": 10.0,
        "behind_ball_pct": 73.0, "last_back_pct": 51.0, "dist_to_ball_avg": 3290.0,
        "big_pads": 7.2, "small_pads": 18.5, "boost_stolen": 3.4,
        "aerial_touches": 2.5, "fast_aerials": 8.0, "ball_hits": 11.6,
        "epic_saves": 0.3, "hat_tricks": 0.0, "aerial_goals": 0.4, "bicycle_goals": 0.0,
        "long_goals": 0.1, "centers": 1.2, "pool_shots": 0.0, "saviors": 0.5, "mvps": 0.4,
        "shot_accuracy": 35, "air_touch_pct": 22,
    },
    "trend": [
        {"day": "2026-04-19", "matches": 6, "win_rate": 33, "shot_accuracy": 28,
         "score_per_min": 310, "behind_ball_pct": 68, "possession_pct": 51},
        {"day": "2026-04-22", "matches": 9, "win_rate": 44, "shot_accuracy": 31,
         "score_per_min": 340, "behind_ball_pct": 71, "possession_pct": 54},
        {"day": "2026-04-26", "matches": 11, "win_rate": 45, "shot_accuracy": 33,
         "score_per_min": 365, "behind_ball_pct": 74, "possession_pct": 55},
        {"day": "2026-04-29", "matches": 14, "win_rate": 50, "shot_accuracy": 35,
         "score_per_min": 388, "behind_ball_pct": 76, "possession_pct": 57},
        {"day": "2026-05-02", "matches": 12, "win_rate": 58, "shot_accuracy": 37,
         "score_per_min": 410, "behind_ball_pct": 81, "possession_pct": 62},
    ],
    "insights": [
        "Behind ball 81% (avg 73%) — your defensive read is sharper this match.",
        "Boost wasted 7% (avg 8%) — solid pad management.",
        "Aerial touches 4 (avg 2.5) — keep going up; your air game is improving.",
    ],
    "today": DEMO_TODAY_SNAPSHOT,
    "rank_benchmark": None,
}


def publish_demo_snapshot() -> None:  # pragma: no cover — preview-only entry point
    """Push fixed hardcoded snapshots to the hub once at startup.

    The hub caches the latest payload per type, so any client that connects
    later will receive these. No simulation, no loop — just a static UI for
    iterating on the frontend.
    """
    hub.publish({"type": "match", "data": DEMO_MATCH_SNAPSHOT})
    hub.publish({"type": "today", "data": DEMO_TODAY_SNAPSHOT})
    hub.publish({"type": "coach", "data": DEMO_COACH_SNAPSHOT})


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover — exercised via uvicorn at runtime
    global db
    # RL_OVERLAY_DB lets the operator point at an alternate SQLite file — handy
    # for demo runs that shouldn't touch the user's real history.
    db_override = os.environ.get("RL_OVERLAY_DB")
    db = open_db(Path(db_override)) if db_override else open_db()
    _apply_config_identity()

    task: asyncio.Task | None = None
    if app.state.demo:
        agg.me_id = "Steam|76561197960409023|0"
        agg.me_name = "alas"
        publish_demo_snapshot()
    else:
        task = asyncio.create_task(
            tcp_pump(app.state.tcp_host, app.state.tcp_port), name="tcp_pump"
        )
    try:
        yield
    finally:
        if task is not None:
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
async def coach() -> RedirectResponse:
    # Coach was unified into the main dashboard — keep the URL as a permanent
    # redirect so existing bookmarks / OBS sources don't break.
    return RedirectResponse(url="/", status_code=308)


@app.get("/api/today")
async def api_today() -> dict:
    if app.state.demo:
        return dict(DEMO_TODAY_SNAPSHOT)
    if not agg.me_id or db is None:
        return dict(EMPTY_TODAY)
    # Run SQLite I/O in a thread so a slow disk doesn't block the event loop.
    return await asyncio.to_thread(today_stats, db, agg.me_id)


@app.get("/api/coach")
async def api_coach() -> dict:
    if app.state.demo:
        return dict(DEMO_COACH_SNAPSHOT)
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
