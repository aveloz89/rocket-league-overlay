"""SQLite storage for per-match snapshots + JSON config for player identity."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CONFIG_DIR = Path.home() / ".rl-overlay"
DB_PATH = CONFIG_DIR / "stats.db"
CONFIG_PATH = CONFIG_DIR / "config.json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    match_guid TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player_name TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    blue_score INTEGER,
    orange_score INTEGER,
    me_team INTEGER,
    won INTEGER,
    score INTEGER,
    goals INTEGER,
    shots INTEGER,
    saves INTEGER,
    assists INTEGER,
    demos INTEGER,
    demos_taken INTEGER,
    touches INTEGER,
    boost_avg REAL,
    time_zero_boost_pct REAL,
    time_supersonic_pct REAL,
    time_airborne_pct REAL,
    hardest_hit REAL,
    ball_hits INTEGER DEFAULT 0,
    avg_shot_power REAL DEFAULT 0,
    boost_wasted_pct REAL DEFAULT 0,
    possession_pct REAL DEFAULT 0,
    UNIQUE(match_guid, player_id)
);
"""

_NEW_COLUMNS = [
    ("ball_hits", "INTEGER DEFAULT 0"),
    ("avg_shot_power", "REAL DEFAULT 0"),
    ("boost_wasted_pct", "REAL DEFAULT 0"),
    ("possession_pct", "REAL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    for name, decl in _NEW_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {name} {decl}")
    conn.commit()


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False so the same connection can be used from the event
    # loop and from threads spawned via asyncio.to_thread for read-only queries.
    # WAL mode + the "writes only from the event loop, reads may be threaded"
    # convention keep this safe in practice.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def save_match(conn: sqlite3.Connection, snapshot: dict) -> bool:
    if not snapshot.get("match_guid") or not snapshot.get("player_id"):
        return False
    # started_at is NOT NULL in the schema — without this guard the INSERT raises
    # IntegrityError, which would crash the event-loop task that owns the call.
    if not snapshot.get("started_at"):
        return False
    won = snapshot.get("won")
    won_int = None if won is None else (1 if won else 0)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO matches (
            match_guid, player_id, player_name, started_at, ended_at,
            blue_score, orange_score, me_team, won,
            score, goals, shots, saves, assists, demos, demos_taken, touches,
            boost_avg, time_zero_boost_pct, time_supersonic_pct, time_airborne_pct,
            hardest_hit, ball_hits, avg_shot_power, boost_wasted_pct, possession_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["match_guid"],
            snapshot["player_id"],
            snapshot.get("player_name"),
            snapshot["started_at"],
            snapshot["ended_at"],
            snapshot.get("blue_score"),
            snapshot.get("orange_score"),
            snapshot.get("me_team"),
            won_int,
            snapshot.get("score"),
            snapshot.get("goals"),
            snapshot.get("shots"),
            snapshot.get("saves"),
            snapshot.get("assists"),
            snapshot.get("demos"),
            snapshot.get("demos_taken"),
            snapshot.get("touches"),
            snapshot.get("boost_avg"),
            snapshot.get("time_zero_boost_pct"),
            snapshot.get("time_supersonic_pct"),
            snapshot.get("time_airborne_pct"),
            snapshot.get("hardest_hit"),
            snapshot.get("ball_hits", 0),
            snapshot.get("avg_shot_power", 0),
            snapshot.get("boost_wasted_pct", 0),
            snapshot.get("possession_pct", 0),
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def today_stats(conn: sqlite3.Connection, player_id: str) -> dict:
    cur = conn.execute(
        """
        SELECT
            COUNT(*) AS matches,
            COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS wins,
            COALESCE(SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END), 0) AS losses,
            COALESCE(SUM(goals), 0) AS goals,
            COALESCE(SUM(shots), 0) AS shots,
            COALESCE(SUM(saves), 0) AS saves,
            COALESCE(SUM(assists), 0) AS assists,
            COALESCE(SUM(demos), 0) AS demos,
            COALESCE(SUM(demos_taken), 0) AS demos_taken,
            COALESCE(SUM(touches), 0) AS touches,
            COALESCE(AVG(score), 0) AS avg_score,
            COALESCE(MAX(score), 0) AS best_score,
            COALESCE(AVG(boost_avg), 0) AS avg_boost,
            COALESCE(AVG(time_supersonic_pct), 0) AS avg_supersonic,
            COALESCE(SUM(ball_hits), 0) AS total_ball_hits,
            COALESCE(MAX(hardest_hit), 0) AS best_hit
        FROM matches
        WHERE player_id = ?
        AND date(started_at, 'localtime') = date('now', 'localtime')
        """,
        (player_id,),
    )
    row = cur.fetchone()
    columns = [c[0] for c in cur.description]
    raw = dict(zip(columns, row))
    matches = raw["matches"] or 0
    win_rate = round(100 * raw["wins"] / matches) if matches else 0
    shot_acc = round(100 * raw["goals"] / raw["shots"]) if raw["shots"] else 0
    return {
        "matches": matches,
        "wins": raw["wins"],
        "losses": raw["losses"],
        "win_rate": win_rate,
        "goals": raw["goals"],
        "shots": raw["shots"],
        "shot_accuracy": shot_acc,
        "saves": raw["saves"],
        "assists": raw["assists"],
        "demos": raw["demos"],
        "demos_taken": raw["demos_taken"],
        "touches": raw["touches"],
        "avg_score": round(raw["avg_score"]),
        "best_score": raw["best_score"],
        "avg_boost": round(raw["avg_boost"]),
        "avg_supersonic_pct": round(raw["avg_supersonic"]),
        "total_ball_hits": raw["total_ball_hits"],
        "best_hit": round(raw["best_hit"], 1),
        "win_streak": _current_win_streak(conn, player_id),
    }


def _current_win_streak(conn: sqlite3.Connection, player_id: str) -> int:
    """Consecutive wins from the most recent match, today only.
    Both losses and draws (won = NULL) break the streak."""
    cur = conn.execute(
        """
        SELECT won FROM matches
        WHERE player_id = ?
          AND date(started_at, 'localtime') = date('now', 'localtime')
        ORDER BY ended_at DESC
        """,
        (player_id,),
    )
    streak = 0
    for (won,) in cur.fetchall():
        if won == 1:
            streak += 1
        else:
            break
    return streak


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2))
