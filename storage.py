"""SQLite storage for per-match snapshots + JSON config for player identity.

Rank benchmark data lives in static/rank_benchmarks.json and is validated
against RankBenchmarks on first load. The loaded model is cached via
_load_benchmarks (functools.cache) so disk I/O happens only once per process.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

log = logging.getLogger("rl-overlay.storage")

CONFIG_DIR = Path.home() / ".rl-overlay"
DB_PATH = CONFIG_DIR / "stats.db"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Path to the static JSON; resolved relative to this file so PyInstaller's
# _MEIPASS resolution in app.py is not needed here — storage is always loaded
# from the same directory as app.py.
_BENCHMARKS_PATH: Path = Path(__file__).parent / "static" / "rank_benchmarks.json"

RANK_TIERS: tuple[str, ...] = (
    "bronze",
    "silver",
    "gold",
    "platinum",
    "diamond",
    "champion",
    "grand_champion",
    "supersonic_legend",
)

BENCHMARK_KEYS: tuple[str, ...] = (
    "shot_accuracy",
    "score_per_min",
    "possession_pct",
    "boost_avg",
    "boost_wasted_pct",
    "time_zero_boost_pct",
    "time_supersonic_pct",
    "time_airborne_pct",
    "time_def_third_pct",
    "time_off_third_pct",
    "behind_ball_pct",
    "last_back_pct",
    "aerial_touches",
    "fast_aerials",
    "air_touch_pct",
)


class RankBenchmarks(BaseModel):
    version: str
    tiers: list[str]
    labels: dict[str, str]
    stats: dict[str, dict[str, float]]  # stat_key -> {tier: value}

    @field_validator("tiers")
    @classmethod
    def _tiers_match(cls, v: list[str]) -> list[str]:
        if tuple(v) != RANK_TIERS:
            raise ValueError("tiers must match RANK_TIERS exactly and in order")
        return v

    @field_validator("stats")
    @classmethod
    def _stats_complete(cls, v: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        for stat, by_tier in v.items():
            missing = set(RANK_TIERS) - set(by_tier.keys())
            if missing:
                raise ValueError(f"{stat} missing tiers: {missing}")
        return v


@cache
def _load_benchmarks(path: Path) -> RankBenchmarks | None:
    """Read, parse, and validate rank_benchmarks.json.

    Cached so the file is read only once per process. Returns None if the file
    is missing or invalid — the feature degrades silently, the app keeps running.
    """
    try:
        raw: Any = json.loads(path.read_text())
        return RankBenchmarks.model_validate(raw)
    except FileNotFoundError:
        log.warning("rank_benchmarks.json not found at %s — benchmark feature disabled", path)
        return None
    except Exception as exc:  # noqa: BLE001 — validation errors, JSON errors, IO errors
        log.warning("rank_benchmarks.json failed to load: %s — benchmark feature disabled", exc)
        return None


def _benchmark_for_tier(
    benchmarks: RankBenchmarks, tier: str
) -> dict[str, float] | None:
    """Extract per-tier values for all benchmark keys.

    Returns None when tier is not in the data (e.g. stale config with a
    tier name that no longer exists in the JSON).
    """
    if tier not in RANK_TIERS:
        return None
    return {key: benchmarks.stats[key][tier] for key in BENCHMARK_KEYS if key in benchmarks.stats}

# Rolling-average window used by /coach. Tuned so a single off-day won't
# dominate the average and so insights are stable across short sessions.
COACH_ROLLING_WINDOW = 20
# Minimum sample size before insights are generated. Below this the rolling
# average is too noisy to give actionable feedback.
COACH_INSIGHT_MIN_SAMPLES = 5
# Trend window in days (inclusive of today).
COACH_TREND_DAYS = 14
# Insight thresholds — flag a metric when last-match deviates from rolling
# avg by ≥ this much (relative) OR by ≥ this many absolute points.
INSIGHT_REL_THRESHOLD = 0.25
INSIGHT_ABS_THRESHOLD = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS match_events (
    id INTEGER PRIMARY KEY,
    match_guid TEXT NOT NULL,
    type TEXT NOT NULL,
    actor_id TEXT,
    actor_name TEXT,
    actor_team INTEGER,
    occurred_at TEXT NOT NULL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_match_events_guid ON match_events(match_guid);
CREATE INDEX IF NOT EXISTS idx_match_events_type ON match_events(type);

CREATE TABLE IF NOT EXISTS ball_touches (
    id INTEGER PRIMARY KEY,
    match_guid TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player_name TEXT,
    team INTEGER,
    post_hit_speed REAL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ball_touches_guid ON ball_touches(match_guid);
CREATE INDEX IF NOT EXISTS idx_ball_touches_player ON ball_touches(player_id);

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
    score_per_min INTEGER DEFAULT 0,
    time_def_third_pct REAL,
    time_off_third_pct REAL,
    behind_ball_pct REAL,
    last_back_pct REAL,
    dist_to_ball_avg REAL,
    big_pads INTEGER DEFAULT 0,
    small_pads INTEGER DEFAULT 0,
    boost_stolen INTEGER DEFAULT 0,
    aerial_touches INTEGER DEFAULT 0,
    fast_aerials INTEGER DEFAULT 0,
    epic_saves INTEGER DEFAULT 0,
    hat_tricks INTEGER DEFAULT 0,
    aerial_goals INTEGER DEFAULT 0,
    bicycle_goals INTEGER DEFAULT 0,
    long_goals INTEGER DEFAULT 0,
    centers INTEGER DEFAULT 0,
    pool_shots INTEGER DEFAULT 0,
    saviors INTEGER DEFAULT 0,
    mvps INTEGER DEFAULT 0,
    team_size INTEGER,
    UNIQUE(match_guid, player_id)
);
"""

_NEW_COLUMNS: list[tuple[str, str]] = [
    ("ball_hits", "INTEGER DEFAULT 0"),
    ("avg_shot_power", "REAL DEFAULT 0"),
    ("boost_wasted_pct", "REAL DEFAULT 0"),
    ("possession_pct", "REAL DEFAULT 0"),
    ("score_per_min", "INTEGER DEFAULT 0"),
    ("time_def_third_pct", "REAL"),
    ("time_off_third_pct", "REAL"),
    ("behind_ball_pct", "REAL"),
    ("last_back_pct", "REAL"),
    ("dist_to_ball_avg", "REAL"),
    ("big_pads", "INTEGER DEFAULT 0"),
    ("small_pads", "INTEGER DEFAULT 0"),
    ("boost_stolen", "INTEGER DEFAULT 0"),
    ("aerial_touches", "INTEGER DEFAULT 0"),
    ("fast_aerials", "INTEGER DEFAULT 0"),
    ("epic_saves", "INTEGER DEFAULT 0"),
    ("hat_tricks", "INTEGER DEFAULT 0"),
    ("aerial_goals", "INTEGER DEFAULT 0"),
    ("bicycle_goals", "INTEGER DEFAULT 0"),
    ("long_goals", "INTEGER DEFAULT 0"),
    ("centers", "INTEGER DEFAULT 0"),
    ("pool_shots", "INTEGER DEFAULT 0"),
    ("saviors", "INTEGER DEFAULT 0"),
    ("mvps", "INTEGER DEFAULT 0"),
    ("team_size", "INTEGER"),
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


def get_match_events(conn: sqlite3.Connection, match_guid: str) -> list[dict]:
    """Return events for the given match_guid ordered by occurrence.

    `payload` is decoded from JSON; an empty dict is returned if the column
    is NULL or unparseable so callers don't have to handle either case.
    """
    if not match_guid:
        return []
    cur = conn.execute(
        "SELECT type, actor_name, actor_team, occurred_at, payload "
        "FROM match_events WHERE match_guid = ? ORDER BY occurred_at, id",
        (match_guid,),
    )
    out: list[dict] = []
    for type_name, actor_name, actor_team, occurred_at, payload_json in cur.fetchall():
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        out.append({
            "type": type_name,
            "actor_name": actor_name,
            "actor_team": actor_team,
            "occurred_at": occurred_at,
            "payload": payload,
        })
    return out


def save_ball_touches(
    conn: sqlite3.Connection, match_guid: str | None, touches: list[dict]
) -> int:
    """Insert every buffered ball touch. Returns the number of rows written.

    Same caller-clears-buffer convention as save_match_events: there is no
    UNIQUE constraint so replaying the same list would duplicate rows.
    """
    if not match_guid or not touches:
        return 0
    rows = [
        (
            match_guid,
            touch["player_id"],
            touch.get("player_name"),
            touch.get("team"),
            touch.get("post_hit_speed"),
            touch["occurred_at"],
        )
        for touch in touches
    ]
    conn.executemany(
        "INSERT INTO ball_touches "
        "(match_guid, player_id, player_name, team, post_hit_speed, occurred_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def save_match_events(
    conn: sqlite3.Connection, match_guid: str | None, events: list[dict]
) -> int:
    """Insert every buffered match event. Returns the number of rows written.

    Caller is responsible for clearing the buffer afterwards — replays of the
    same event list would duplicate rows because we don't enforce a UNIQUE
    constraint (two goals from the same scorer at the same second is legal).
    """
    if not match_guid or not events:
        return 0
    rows = [
        (
            match_guid,
            evt["type"],
            evt.get("actor_id"),
            evt.get("actor_name"),
            evt.get("actor_team"),
            evt["occurred_at"],
            json.dumps(evt.get("payload") or {}),
        )
        for evt in events
    ]
    conn.executemany(
        "INSERT INTO match_events "
        "(match_guid, type, actor_id, actor_name, actor_team, occurred_at, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


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
            hardest_hit, ball_hits, avg_shot_power, boost_wasted_pct, possession_pct,
            score_per_min, time_def_third_pct, time_off_third_pct, behind_ball_pct,
            last_back_pct, dist_to_ball_avg, big_pads, small_pads, boost_stolen,
            aerial_touches, fast_aerials,
            epic_saves, hat_tricks, aerial_goals, bicycle_goals, long_goals,
            centers, pool_shots, saviors, mvps, team_size
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
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
            snapshot.get("score_per_min", 0),
            snapshot.get("time_def_third_pct"),
            snapshot.get("time_off_third_pct"),
            snapshot.get("behind_ball_pct"),
            snapshot.get("last_back_pct"),
            snapshot.get("dist_to_ball_avg"),
            snapshot.get("big_pads", 0),
            snapshot.get("small_pads", 0),
            snapshot.get("boost_stolen", 0),
            snapshot.get("aerial_touches", 0),
            snapshot.get("fast_aerials", 0),
            snapshot.get("epic_saves", 0),
            snapshot.get("hat_tricks", 0),
            snapshot.get("aerial_goals", 0),
            snapshot.get("bicycle_goals", 0),
            snapshot.get("long_goals", 0),
            snapshot.get("centers", 0),
            snapshot.get("pool_shots", 0),
            snapshot.get("saviors", 0),
            snapshot.get("mvps", 0),
            snapshot.get("team_size"),
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def today_stats(
    conn: sqlite3.Connection, player_id: str, mode: int | None = None
) -> dict:
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
        AND (? IS NULL OR team_size = ?)
        """,
        (player_id, mode, mode),
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
        "win_streak": _current_win_streak(conn, player_id, mode),
    }


def _current_win_streak(
    conn: sqlite3.Connection, player_id: str, mode: int | None = None
) -> int:
    """Consecutive wins from the most recent match, today only.
    Both losses and draws (won = NULL) break the streak."""
    cur = conn.execute(
        """
        SELECT won FROM matches
        WHERE player_id = ?
          AND date(started_at, 'localtime') = date('now', 'localtime')
          AND (? IS NULL OR team_size = ?)
        ORDER BY ended_at DESC
        """,
        (player_id, mode, mode),
    )
    streak = 0
    for (won,) in cur.fetchall():
        if won == 1:
            streak += 1
        else:
            break
    return streak


# ── Coach view ───────────────────────────────────────────────────────────────


# Insight definitions: (key, label, suffix, direction, advice).
# direction: +1 = high is good (worse when below avg). -1 = low is good (worse when above avg).
_INSIGHT_DEFS: list[tuple[str, str, str, int, str]] = [
    ("shot_accuracy", "Shot accuracy", "%", 1, "take calmer shots"),
    ("possession_pct", "Possession", "%", 1, "fight harder for the ball"),
    ("score_per_min", "Score/min", "", 1, "go for high-impact actions"),
    ("behind_ball_pct", "Behind ball", "%", 1, "you're overextending"),
    ("time_zero_boost_pct", "Zero boost", "%", -1, "pick up more pads"),
    ("boost_wasted_pct", "Boost wasted", "%", -1, "don't refill above 50"),
    ("aerial_touches", "Aerial touches", "", 1, "take to the air more"),
]


def _row_to_match(row: sqlite3.Row, columns: list[str]) -> dict:
    raw = dict(zip(columns, row))
    # Defense in depth: the WS / HTTP responses don't need to echo the platform
    # PrimaryId back. The frontend only uses player_name. If origin checks ever
    # leak, dropping this here narrows what an attacker can read.
    raw.pop("player_id", None)
    won = raw.get("won")
    raw["won"] = None if won is None else bool(won)
    shots = raw.get("shots") or 0
    goals = raw.get("goals") or 0
    raw["shot_accuracy"] = round(100 * goals / shots) if shots else 0
    ball_hits = raw.get("ball_hits") or 0
    aerial_touches = raw.get("aerial_touches") or 0
    raw["air_touch_pct"] = round(100 * aerial_touches / ball_hits) if ball_hits else 0
    raw["duration_min"] = _duration_minutes(raw.get("started_at"), raw.get("ended_at"))
    return raw


def _duration_minutes(started_at: str | None, ended_at: str | None) -> int:
    if not started_at or not ended_at:
        return 0
    try:
        s = datetime.fromisoformat(started_at)
        e = datetime.fromisoformat(ended_at)
    except ValueError:
        return 0
    delta = (e - s).total_seconds() / 60
    return max(0, round(delta))


def _last_match(
    conn: sqlite3.Connection, player_id: str, mode: int | None = None
) -> dict | None:
    cur = conn.execute(
        """
        SELECT * FROM matches
        WHERE player_id = ?
          AND (? IS NULL OR team_size = ?)
        ORDER BY ended_at DESC
        LIMIT 1
        """,
        (player_id, mode, mode),
    )
    row = cur.fetchone()
    if row is None:
        return None
    columns = [c[0] for c in cur.description]
    return _row_to_match(row, columns)


def _rolling_avg(
    conn: sqlite3.Connection,
    player_id: str,
    exclude_id: int | None,
    mode: int | None = None,
    window: int = COACH_ROLLING_WINDOW,
) -> dict:
    cur = conn.execute(
        """
        SELECT
            COUNT(*) AS count,
            AVG(score) AS score,
            AVG(goals) AS goals,
            AVG(shots) AS shots,
            AVG(saves) AS saves,
            AVG(assists) AS assists,
            AVG(score_per_min) AS score_per_min,
            AVG(possession_pct) AS possession_pct,
            AVG(boost_avg) AS boost_avg,
            AVG(boost_wasted_pct) AS boost_wasted_pct,
            AVG(time_zero_boost_pct) AS time_zero_boost_pct,
            AVG(time_supersonic_pct) AS time_supersonic_pct,
            AVG(time_airborne_pct) AS time_airborne_pct,
            AVG(time_def_third_pct) AS time_def_third_pct,
            AVG(time_off_third_pct) AS time_off_third_pct,
            AVG(behind_ball_pct) AS behind_ball_pct,
            AVG(last_back_pct) AS last_back_pct,
            AVG(dist_to_ball_avg) AS dist_to_ball_avg,
            AVG(big_pads) AS big_pads,
            AVG(small_pads) AS small_pads,
            AVG(boost_stolen) AS boost_stolen,
            AVG(aerial_touches) AS aerial_touches,
            AVG(fast_aerials) AS fast_aerials,
            AVG(ball_hits) AS ball_hits,
            AVG(epic_saves) AS epic_saves,
            AVG(hat_tricks) AS hat_tricks,
            AVG(aerial_goals) AS aerial_goals,
            AVG(bicycle_goals) AS bicycle_goals,
            AVG(long_goals) AS long_goals,
            AVG(centers) AS centers,
            AVG(pool_shots) AS pool_shots,
            AVG(saviors) AS saviors,
            AVG(mvps) AS mvps
        FROM (
            SELECT * FROM matches
            WHERE player_id = ?
              AND (? IS NULL OR id != ?)
              AND (? IS NULL OR team_size = ?)
            ORDER BY ended_at DESC
            LIMIT ?
        )
        """,
        (player_id, exclude_id, exclude_id, mode, mode, window),
    )
    row = cur.fetchone()
    columns = [c[0] for c in cur.description]
    raw = dict(zip(columns, row))
    avg = {k: (round(v, 2) if isinstance(v, (int, float)) else v) for k, v in raw.items()}
    # Synthesize shot_accuracy and air_touch_pct from the underlying averages
    shots_avg = raw.get("shots") or 0
    goals_avg = raw.get("goals") or 0
    avg["shot_accuracy"] = round(100 * goals_avg / shots_avg) if shots_avg else 0
    ball_hits_avg = raw.get("ball_hits") or 0
    aerial_avg = raw.get("aerial_touches") or 0
    avg["air_touch_pct"] = round(100 * aerial_avg / ball_hits_avg) if ball_hits_avg else 0
    return avg


def _trend_by_day(
    conn: sqlite3.Connection,
    player_id: str,
    mode: int | None = None,
    days: int = COACH_TREND_DAYS,
) -> list[dict]:
    cur = conn.execute(
        """
        SELECT
            date(started_at, 'localtime') AS day,
            COUNT(*) AS matches,
            COALESCE(SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END), 0) AS wins,
            COALESCE(SUM(goals), 0) AS goals,
            COALESCE(SUM(shots), 0) AS shots,
            AVG(score_per_min) AS score_per_min,
            AVG(behind_ball_pct) AS behind_ball_pct,
            AVG(possession_pct) AS possession_pct
        FROM matches
        WHERE player_id = ?
          AND date(started_at, 'localtime') >= date('now', 'localtime', ?)
          AND (? IS NULL OR team_size = ?)
        GROUP BY day
        ORDER BY day ASC
        """,
        (player_id, f"-{days - 1} days", mode, mode),
    )
    out: list[dict] = []
    for row in cur.fetchall():
        day, matches, wins, goals, shots, spm, bbp, poss = row
        out.append({
            "day": day,
            "matches": matches,
            "win_rate": round(100 * wins / matches) if matches else 0,
            "shot_accuracy": round(100 * goals / shots) if shots else 0,
            "score_per_min": round(spm) if spm is not None else 0,
            "behind_ball_pct": round(bbp) if bbp is not None else None,
            "possession_pct": round(poss) if poss is not None else None,
        })
    return out


def _generate_insights(last: dict, avg: dict) -> list[str]:
    """Return up to 3 actionable phrases comparing the latest match against the rolling avg.

    Only flags metrics where last is *worse* than avg by enough magnitude to be
    meaningful (relative ≥ INSIGHT_REL_THRESHOLD or absolute ≥ INSIGHT_ABS_THRESHOLD).
    """
    candidates: list[tuple[float, str]] = []
    for key, label, suffix, direction, advice in _INSIGHT_DEFS:
        last_v = last.get(key)
        avg_v = avg.get(key)
        if last_v is None or avg_v is None:
            continue
        diff = float(last_v) - float(avg_v)
        worse_by = -direction * diff  # positive when last is worse than avg
        # Treat avg=0 explicitly so the relative threshold doesn't fire on
        # every tiny absolute deviation when the baseline is a true zero.
        abs_avg = abs(avg_v) if avg_v != 0 else 1
        rel = worse_by / abs_avg
        if worse_by >= INSIGHT_ABS_THRESHOLD or rel >= INSIGHT_REL_THRESHOLD:
            phrase = (
                f"{label} {round(last_v)}{suffix} (avg {round(avg_v)}{suffix}) — {advice}"
            )
            candidates.append((worse_by, phrase))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [phrase for _, phrase in candidates[:3]]


def _build_rank_benchmark(target_rank: str | None) -> dict | None:
    """Return the rank_benchmark payload for the given tier, or None."""
    if not target_rank:
        return None
    benchmarks = _load_benchmarks(_BENCHMARKS_PATH)
    if benchmarks is None:
        return None
    stats = _benchmark_for_tier(benchmarks, target_rank)
    if stats is None:
        log.warning("target_rank %r not found in benchmarks — treating as None", target_rank)
        return None
    return {
        "tier": target_rank,
        "label": benchmarks.labels.get(target_rank, target_rank),
        "stats": stats,
    }


def coach_stats(
    conn: sqlite3.Connection,
    player_id: str,
    target_rank: str | None = None,
    mode: int | None = None,
) -> dict:
    last = _last_match(conn, player_id, mode)
    exclude_id = last["id"] if last else None
    avg = _rolling_avg(conn, player_id, exclude_id, mode)
    trend = _trend_by_day(conn, player_id, mode)
    rolling_count = avg.get("count") or 0
    insights = (
        _generate_insights(last, avg)
        if last and rolling_count >= COACH_INSIGHT_MIN_SAMPLES
        else []
    )
    last_events = get_match_events(conn, last["match_guid"]) if last else []
    return {
        "last_match": last,
        "rolling_avg": avg,
        "trend": trend,
        "insights": insights,
        "last_match_events": last_events,
        "today": today_stats(conn, player_id, mode),
        "rank_benchmark": _build_rank_benchmark(target_rank),
    }


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
