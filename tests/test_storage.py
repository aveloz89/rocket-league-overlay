from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import (  # noqa: E402
    coach_stats,
    get_match_events,
    open_db,
    save_ball_touches,
    save_match,
    save_match_events,
    today_stats,
)


def make_snapshot(match_guid="M1", won=True, **overrides):
    base = {
        "match_guid": match_guid,
        "player_id": "Steam|1|0",
        "player_name": "alas",
        "started_at": "2026-04-30T20:00:00+00:00",
        "ended_at": "2026-04-30T20:05:00+00:00",
        "blue_score": 3,
        "orange_score": 1,
        "me_team": 0,
        "won": won,
        "score": 400,
        "goals": 2,
        "shots": 4,
        "saves": 1,
        "assists": 1,
        "demos": 0,
        "demos_taken": 1,
        "touches": 12,
        "boost_avg": 55,
        "time_zero_boost_pct": 10,
        "time_supersonic_pct": 25,
        "time_airborne_pct": 30,
        "hardest_hit": 92.4,
    }
    base.update(overrides)
    return base


def test_save_match_inserts_row(tmp_path):
    db = open_db(tmp_path / "stats.db")
    assert save_match(db, make_snapshot()) is True

    rows = db.execute("SELECT match_guid, won FROM matches").fetchall()
    assert rows == [("M1", 1)]


def test_save_match_dedups_by_guid_and_player(tmp_path):
    db = open_db(tmp_path / "stats.db")
    save_match(db, make_snapshot())
    inserted = save_match(db, make_snapshot())
    assert inserted is False
    count = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    assert count == 1


def test_today_stats_aggregates_today_only(tmp_path, monkeypatch):
    db = open_db(tmp_path / "stats.db")

    # Use the user's actual current local date — the storage layer's
    # `today_stats` query filters by `date(started_at, 'localtime')` so the
    # input must be a real ISO timestamp the same comparator can evaluate.
    from datetime import datetime
    today_iso = datetime.now().astimezone().isoformat()
    save_match(db, make_snapshot(match_guid="A", started_at=today_iso, won=True, goals=2, shots=4, saves=1, score=400))
    save_match(db, make_snapshot(match_guid="B", started_at=today_iso, won=False, goals=1, shots=3, saves=2, score=250))
    # Yesterday — should not count
    save_match(db, make_snapshot(match_guid="C", started_at="2020-01-01T00:00:00+00:00", won=True, goals=10, shots=20))

    stats = today_stats(db, "Steam|1|0")

    assert stats["matches"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 50
    assert stats["goals"] == 3
    assert stats["shots"] == 7
    assert stats["shot_accuracy"] == round(100 * 3 / 7)
    assert stats["saves"] == 3
    assert stats["best_score"] == 400
    assert stats["avg_score"] == round((400 + 250) / 2)


def test_save_match_rejects_missing_keys(tmp_path):
    db = open_db(tmp_path / "stats.db")
    snap = make_snapshot()
    snap["match_guid"] = None
    assert save_match(db, snap) is False


def test_today_stats_includes_new_aggregates(tmp_path):
    db = open_db(tmp_path / "stats.db")

    from datetime import datetime
    today_iso = datetime.now().astimezone().isoformat()
    save_match(db, make_snapshot(match_guid="A", started_at=today_iso, ended_at=today_iso,
                                 won=True, ball_hits=10, hardest_hit=80.0))
    save_match(db, make_snapshot(match_guid="B", started_at=today_iso, ended_at=today_iso,
                                 won=True, ball_hits=15, hardest_hit=110.5))

    stats = today_stats(db, "Steam|1|0")
    assert stats["total_ball_hits"] == 25
    assert stats["best_hit"] == 110.5
    assert stats["win_streak"] == 2


def test_win_streak_breaks_on_loss(tmp_path):
    db = open_db(tmp_path / "stats.db")
    from datetime import datetime, timedelta
    base = datetime.now().astimezone()

    # Order matters — ended_at determines recency
    save_match(db, make_snapshot(match_guid="1", started_at=base.isoformat(),
                                 ended_at=(base - timedelta(minutes=30)).isoformat(), won=True))
    save_match(db, make_snapshot(match_guid="2", started_at=base.isoformat(),
                                 ended_at=(base - timedelta(minutes=20)).isoformat(), won=False))
    save_match(db, make_snapshot(match_guid="3", started_at=base.isoformat(),
                                 ended_at=(base - timedelta(minutes=10)).isoformat(), won=True))
    save_match(db, make_snapshot(match_guid="4", started_at=base.isoformat(),
                                 ended_at=base.isoformat(), won=True))

    # Most recent two are wins → streak = 2 (broken by loss earlier)
    assert today_stats(db, "Steam|1|0")["win_streak"] == 2


def test_save_match_rejects_missing_started_at(tmp_path):
    db = open_db(tmp_path / "stats.db")
    snap = make_snapshot()
    snap["started_at"] = None
    assert save_match(db, snap) is False
    count = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    assert count == 0


# ── New columns persistence ─────────────────────────────────────────────────


def test_save_match_persists_new_columns(tmp_path):
    db = open_db(tmp_path / "stats.db")
    snap = make_snapshot(
        score_per_min=312, time_def_third_pct=32.5, time_off_third_pct=27.0,
        behind_ball_pct=68.0, last_back_pct=22.0, dist_to_ball_avg=1245.0,
        big_pads=14, small_pads=38, boost_stolen=3,
        aerial_touches=9, fast_aerials=2,
    )
    save_match(db, snap)

    row = db.execute(
        "SELECT score_per_min, time_def_third_pct, behind_ball_pct, big_pads, "
        "aerial_touches, fast_aerials FROM matches WHERE match_guid='M1'"
    ).fetchone()
    assert row == (312, 32.5, 68.0, 14, 9, 2)


# ── coach_stats ─────────────────────────────────────────────────────────────


def test_coach_stats_empty_db_returns_safe_defaults(tmp_path):
    db = open_db(tmp_path / "stats.db")
    result = coach_stats(db, "Steam|1|0")

    assert result["last_match"] is None
    assert result["rolling_avg"]["count"] == 0
    assert result["trend"] == []
    assert result["insights"] == []
    # today_stats is also embedded for convenience
    assert result["today"]["matches"] == 0


def test_coach_stats_returns_last_match_only_below_insight_threshold(tmp_path):
    """With <5 prior matches, no insights should be generated even if the latest
    is much worse — the rolling average is too noisy to be actionable."""
    db = open_db(tmp_path / "stats.db")
    from datetime import datetime, timedelta

    base = datetime.now().astimezone()
    for i in range(3):
        save_match(db, make_snapshot(
            match_guid=f"AVG{i}",
            started_at=(base - timedelta(hours=i + 2)).isoformat(),
            ended_at=(base - timedelta(hours=i + 1, minutes=55)).isoformat(),
            won=True, score=600, goals=3, shots=6, score_per_min=200,
            possession_pct=60, behind_ball_pct=60,
        ))
    # Latest match is much worse
    save_match(db, make_snapshot(
        match_guid="LATEST",
        started_at=base.isoformat(),
        ended_at=(base + timedelta(minutes=5)).isoformat(),
        won=False, score=200, goals=0, shots=10, score_per_min=80,
        possession_pct=30, behind_ball_pct=20,
    ))

    result = coach_stats(db, "Steam|1|0")
    assert result["last_match"]["match_guid"] == "LATEST"
    assert result["rolling_avg"]["count"] == 3
    assert result["insights"] == []  # below COACH_INSIGHT_MIN_SAMPLES


def test_coach_stats_generates_insights_when_last_is_worse(tmp_path):
    db = open_db(tmp_path / "stats.db")
    from datetime import datetime, timedelta

    base = datetime.now().astimezone()
    # 6 strong matches as baseline
    for i in range(6):
        save_match(db, make_snapshot(
            match_guid=f"AVG{i}",
            started_at=(base - timedelta(hours=i + 2)).isoformat(),
            ended_at=(base - timedelta(hours=i + 1, minutes=55)).isoformat(),
            won=True, score=600, goals=3, shots=6, score_per_min=300,
            possession_pct=65, boost_wasted_pct=8, time_zero_boost_pct=5,
        ))
    # Latest is much worse on multiple metrics
    save_match(db, make_snapshot(
        match_guid="LATEST",
        started_at=base.isoformat(),
        ended_at=(base + timedelta(minutes=5)).isoformat(),
        won=False, score=150, goals=0, shots=10, score_per_min=80,
        possession_pct=25, boost_wasted_pct=30, time_zero_boost_pct=22,
    ))

    result = coach_stats(db, "Steam|1|0")
    assert len(result["insights"]) >= 1
    # Should mention the most-deviated metric
    text_blob = " ".join(result["insights"])
    assert any(
        phrase in text_blob.lower()
        for phrase in ["shot accuracy", "possession", "score/min", "boost wasted", "zero boost"]
    )


def test_coach_stats_rolling_avg_excludes_latest_match(tmp_path):
    """The rolling avg used for comparison should not include the very match
    we're comparing against — otherwise deviations get diluted."""
    db = open_db(tmp_path / "stats.db")
    from datetime import datetime, timedelta

    base = datetime.now().astimezone()
    for i in range(3):
        save_match(db, make_snapshot(
            match_guid=f"P{i}",
            started_at=(base - timedelta(hours=i + 2)).isoformat(),
            ended_at=(base - timedelta(hours=i + 1)).isoformat(),
            score=300,
        ))
    save_match(db, make_snapshot(
        match_guid="LATEST",
        started_at=base.isoformat(),
        ended_at=base.isoformat(),
        score=900,  # outlier
    ))

    result = coach_stats(db, "Steam|1|0")
    # Rolling avg score should be 300 (the 3 prior), not 450 (incl. latest)
    assert result["rolling_avg"]["score"] == 300
    assert result["last_match"]["score"] == 900


def test_coach_stats_trend_groups_by_local_date(tmp_path):
    """Trend should bucket matches by date and respect the 14-day window."""
    db = open_db(tmp_path / "stats.db")
    from datetime import datetime, timedelta

    today = datetime.now().astimezone()
    yesterday = today - timedelta(days=1)
    too_old = today - timedelta(days=20)

    save_match(db, make_snapshot(match_guid="T1", started_at=today.isoformat(),
                                 ended_at=today.isoformat(), won=True, score_per_min=200))
    save_match(db, make_snapshot(match_guid="Y1", started_at=yesterday.isoformat(),
                                 ended_at=yesterday.isoformat(), won=False, score_per_min=150))
    save_match(db, make_snapshot(match_guid="OLD", started_at=too_old.isoformat(),
                                 ended_at=too_old.isoformat(), won=True))

    result = coach_stats(db, "Steam|1|0")
    days_in_trend = [d["day"] for d in result["trend"]]
    assert today.strftime("%Y-%m-%d") in days_in_trend
    assert yesterday.strftime("%Y-%m-%d") in days_in_trend
    assert too_old.strftime("%Y-%m-%d") not in days_in_trend


def test_coach_stats_last_match_includes_derived_fields(tmp_path):
    """last_match should expose shot_accuracy, air_touch_pct, duration_min as
    convenience fields (computed, not stored)."""
    db = open_db(tmp_path / "stats.db")
    save_match(db, make_snapshot(
        match_guid="DERIVED",
        goals=4, shots=10,
        ball_hits=20, aerial_touches=5,
        started_at="2026-04-30T20:00:00+00:00",
        ended_at="2026-04-30T20:08:00+00:00",
    ))

    result = coach_stats(db, "Steam|1|0")
    last = result["last_match"]
    assert last["shot_accuracy"] == 40
    assert last["air_touch_pct"] == 25
    assert last["duration_min"] == 8


# ── Statfeed highlight columns ───────────────────────────────────────────────


# T14/T15/T16: save_match persists highlight columns, round-trip via SELECT
def test_save_match_persists_highlight_columns(tmp_path):
    db = open_db(tmp_path / "stats.db")
    snap = make_snapshot(
        epic_saves=2, hat_tricks=1, aerial_goals=3, bicycle_goals=0,
        long_goals=1, centers=4, pool_shots=0, saviors=2, mvps=1,
    )
    save_match(db, snap)

    row = db.execute(
        "SELECT epic_saves, hat_tricks, aerial_goals, bicycle_goals, long_goals, "
        "centers, pool_shots, saviors, mvps FROM matches WHERE match_guid='M1'"
    ).fetchone()
    assert row == (2, 1, 3, 0, 1, 4, 0, 2, 1)


# T16: highlights default to 0 when not provided in snapshot
def test_save_match_highlight_columns_default_to_zero(tmp_path):
    db = open_db(tmp_path / "stats.db")
    # make_snapshot does not include highlight keys — must default to 0
    save_match(db, make_snapshot())

    row = db.execute(
        "SELECT epic_saves, hat_tricks, aerial_goals, bicycle_goals, long_goals, "
        "centers, pool_shots, saviors, mvps FROM matches WHERE match_guid='M1'"
    ).fetchone()
    assert row == (0, 0, 0, 0, 0, 0, 0, 0, 0)


# T17: rolling_avg exposes the 9 highlight averages
def test_rolling_avg_includes_highlight_averages(tmp_path):
    db = open_db(tmp_path / "stats.db")
    from datetime import datetime, timedelta

    base = datetime.now().astimezone()
    save_match(db, make_snapshot(
        match_guid="A",
        started_at=(base - timedelta(hours=2)).isoformat(),
        ended_at=(base - timedelta(hours=1)).isoformat(),
        epic_saves=2, hat_tricks=0, aerial_goals=1, bicycle_goals=0,
        long_goals=0, centers=2, pool_shots=0, saviors=1, mvps=0,
    ))
    save_match(db, make_snapshot(
        match_guid="B",
        started_at=(base - timedelta(hours=4)).isoformat(),
        ended_at=(base - timedelta(hours=3)).isoformat(),
        epic_saves=0, hat_tricks=1, aerial_goals=1, bicycle_goals=0,
        long_goals=0, centers=0, pool_shots=0, saviors=1, mvps=0,
    ))

    result = coach_stats(db, "Steam|1|0")
    avg = result["rolling_avg"]
    # avg epic_saves: (2+0)/2 = 1.0, but the latest match is excluded from avg
    # With 2 matches, last_match is A (most recent) and avg window uses B only
    assert "epic_saves" in avg
    assert "hat_tricks" in avg
    assert "saviors" in avg
    # B: epic_saves=0, hat_tricks=1, saviors=1
    assert avg["epic_saves"] == 0.0
    assert avg["hat_tricks"] == 1.0
    assert avg["saviors"] == 1.0


# T18: migration test — DB without highlight columns gets them added
def test_migration_adds_highlight_columns_to_existing_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "old.db"
    # Create a DB with the OLD schema (no highlight columns)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE matches (
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
            aerial_touches INTEGER DEFAULT 0,
            fast_aerials INTEGER DEFAULT 0,
            UNIQUE(match_guid, player_id)
        )
    """)
    # Insert an existing row to verify data is preserved
    conn.execute(
        "INSERT INTO matches (match_guid, player_id, started_at, ended_at, "
        "aerial_touches, fast_aerials) VALUES (?, ?, ?, ?, ?, ?)",
        ("OLD_MATCH", "Steam|1|0", "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:05:00+00:00", 5, 2),
    )
    conn.commit()
    conn.close()

    # Open with our open_db — must migrate without data loss
    migrated = open_db(db_path)
    columns = {row[1] for row in migrated.execute("PRAGMA table_info(matches)")}

    for col in ("epic_saves", "hat_tricks", "aerial_goals", "bicycle_goals",
                "long_goals", "centers", "pool_shots", "saviors", "mvps"):
        assert col in columns, f"column {col!r} missing after migration"

    # Existing data preserved
    row = migrated.execute(
        "SELECT aerial_touches, fast_aerials FROM matches WHERE match_guid='OLD_MATCH'"
    ).fetchone()
    assert row == (5, 2)

    # New columns default to 0 for the old row
    row2 = migrated.execute(
        "SELECT epic_saves, hat_tricks FROM matches WHERE match_guid='OLD_MATCH'"
    ).fetchone()
    assert row2 == (0, 0)


# ── Rank benchmarks ──────────────────────────────────────────────────────────


@pytest.fixture
def benchmarks_json(tmp_path: Path) -> Path:
    """Write a minimal but valid rank_benchmarks.json to a temp dir."""
    from storage import BENCHMARK_KEYS, RANK_TIERS

    stats: dict[str, dict[str, float]] = {
        key: {tier: float(i + j) for j, tier in enumerate(RANK_TIERS)}
        for i, key in enumerate(BENCHMARK_KEYS)
    }
    data = {
        "version": "test-2026",
        "tiers": list(RANK_TIERS),
        "labels": {t: t.capitalize() for t in RANK_TIERS},
        "stats": stats,
    }
    p = tmp_path / "rank_benchmarks.json"
    p.write_text(json.dumps(data))
    return p


def test_load_benchmarks_returns_none_for_missing_file(tmp_path: Path) -> None:
    from storage import _load_benchmarks

    result = _load_benchmarks(tmp_path / "does_not_exist.json")
    assert result is None


def test_load_benchmarks_returns_none_for_invalid_json(tmp_path: Path) -> None:
    from storage import _load_benchmarks

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert _load_benchmarks(bad) is None


def test_load_benchmarks_returns_none_for_wrong_tiers(tmp_path: Path) -> None:
    from storage import _load_benchmarks, BENCHMARK_KEYS

    data = {
        "version": "v1",
        "tiers": ["only_one_tier"],
        "labels": {"only_one_tier": "Only"},
        "stats": {k: {"only_one_tier": 1.0} for k in BENCHMARK_KEYS},
    }
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps(data))
    assert _load_benchmarks(p) is None


def test_load_benchmarks_returns_none_when_stat_missing_tier(tmp_path: Path) -> None:
    """A stat that is missing one tier entry should fail validation."""
    from storage import _load_benchmarks, RANK_TIERS, BENCHMARK_KEYS

    stats: dict[str, dict[str, float]] = {
        key: {tier: 1.0 for tier in RANK_TIERS}
        for key in BENCHMARK_KEYS
    }
    # Remove one tier from the first stat key to trigger validation failure
    first_key = BENCHMARK_KEYS[0]
    del stats[first_key]["bronze"]

    data = {
        "version": "v1",
        "tiers": list(RANK_TIERS),
        "labels": {t: t for t in RANK_TIERS},
        "stats": stats,
    }
    p = tmp_path / "incomplete.json"
    p.write_text(json.dumps(data))
    assert _load_benchmarks(p) is None


def test_load_benchmarks_returns_model_for_valid_file(benchmarks_json: Path) -> None:
    from storage import _load_benchmarks, RankBenchmarks

    result = _load_benchmarks(benchmarks_json)
    assert result is not None
    assert isinstance(result, RankBenchmarks)
    assert result.version == "test-2026"


def test_load_benchmarks_validates_real_json() -> None:
    """The production JSON must parse and validate without errors."""
    from storage import _load_benchmarks, RankBenchmarks, BENCHMARK_KEYS, RANK_TIERS

    project_root = Path(__file__).resolve().parents[1]
    json_path = project_root / "static" / "rank_benchmarks.json"
    result = _load_benchmarks(json_path)
    assert result is not None, "static/rank_benchmarks.json is missing or invalid"
    assert isinstance(result, RankBenchmarks)
    # All 15 benchmark keys must be present
    for key in BENCHMARK_KEYS:
        assert key in result.stats, f"missing stat key: {key}"
    # All 8 tiers must be present in each stat
    for key in BENCHMARK_KEYS:
        for tier in RANK_TIERS:
            assert tier in result.stats[key], f"missing tier {tier!r} in stat {key!r}"


def test_benchmark_for_tier_returns_stats_dict(benchmarks_json: Path) -> None:
    from storage import _load_benchmarks, _benchmark_for_tier, BENCHMARK_KEYS

    bm = _load_benchmarks(benchmarks_json)
    assert bm is not None
    result = _benchmark_for_tier(bm, "diamond")
    assert result is not None
    assert set(result.keys()) == set(BENCHMARK_KEYS)


def test_benchmark_for_tier_returns_none_for_invalid_tier(benchmarks_json: Path) -> None:
    from storage import _load_benchmarks, _benchmark_for_tier

    bm = _load_benchmarks(benchmarks_json)
    assert bm is not None
    assert _benchmark_for_tier(bm, "mythical") is None


def test_coach_stats_without_target_rank_has_null_benchmark(tmp_path: Path) -> None:
    db = open_db(tmp_path / "stats.db")
    result = coach_stats(db, "Steam|1|0")
    assert "rank_benchmark" in result
    assert result["rank_benchmark"] is None


def test_coach_stats_with_target_rank_returns_benchmark(tmp_path: Path, monkeypatch) -> None:
    import storage
    from storage import _load_benchmarks

    project_root = Path(__file__).resolve().parents[1]
    json_path = project_root / "static" / "rank_benchmarks.json"

    db = open_db(tmp_path / "stats.db")
    bm = _load_benchmarks(json_path)
    # Patch _load_benchmarks to return cached benchmarks without hitting the real path
    monkeypatch.setattr(storage, "_BENCHMARKS_PATH", json_path)

    result = coach_stats(db, "Steam|1|0", target_rank="diamond")
    rb = result["rank_benchmark"]
    assert rb is not None
    assert rb["tier"] == "diamond"
    assert rb["label"] == "Diamond"
    assert isinstance(rb["stats"], dict)
    assert "shot_accuracy" in rb["stats"]


def test_coach_stats_with_invalid_target_rank_returns_null(tmp_path: Path, monkeypatch) -> None:
    import storage

    project_root = Path(__file__).resolve().parents[1]
    json_path = project_root / "static" / "rank_benchmarks.json"
    monkeypatch.setattr(storage, "_BENCHMARKS_PATH", json_path)

    db = open_db(tmp_path / "stats.db")
    result = coach_stats(db, "Steam|1|0", target_rank="nonexistent")
    assert result["rank_benchmark"] is None


# ── team_size persistence + mode filtering ──────────────────────────────────


def test_save_match_persists_team_size(tmp_path):
    db = open_db(tmp_path / "stats.db")
    save_match(db, make_snapshot(match_guid="2V2", team_size=2))
    row = db.execute("SELECT team_size FROM matches WHERE match_guid='2V2'").fetchone()
    assert row == (2,)


def test_save_match_team_size_defaults_to_null(tmp_path):
    """Snapshots from older code paths without team_size must store NULL, not 0."""
    db = open_db(tmp_path / "stats.db")
    save_match(db, make_snapshot())  # no team_size key
    row = db.execute("SELECT team_size FROM matches WHERE match_guid='M1'").fetchone()
    assert row == (None,)


def test_today_stats_filters_by_mode(tmp_path):
    from datetime import datetime

    db = open_db(tmp_path / "stats.db")
    today_iso = datetime.now().astimezone().isoformat()
    save_match(db, make_snapshot(match_guid="A", started_at=today_iso, ended_at=today_iso,
                                 won=True, team_size=2))
    save_match(db, make_snapshot(match_guid="B", started_at=today_iso, ended_at=today_iso,
                                 won=False, team_size=3))
    save_match(db, make_snapshot(match_guid="C", started_at=today_iso, ended_at=today_iso,
                                 won=True, team_size=None))  # legacy row

    # All — includes every match regardless of team_size
    assert today_stats(db, "Steam|1|0")["matches"] == 3
    # Filtered modes exclude legacy NULL rows
    assert today_stats(db, "Steam|1|0", mode=2)["matches"] == 1
    assert today_stats(db, "Steam|1|0", mode=3)["matches"] == 1
    assert today_stats(db, "Steam|1|0", mode=1)["matches"] == 0


def test_coach_stats_filters_last_match_by_mode(tmp_path):
    from datetime import datetime, timedelta

    db = open_db(tmp_path / "stats.db")
    base = datetime.now().astimezone()
    # Most recent overall is 1v1
    save_match(db, make_snapshot(
        match_guid="ONES",
        started_at=(base - timedelta(hours=1)).isoformat(),
        ended_at=base.isoformat(),
        team_size=1, score=900,
    ))
    save_match(db, make_snapshot(
        match_guid="TWOS",
        started_at=(base - timedelta(hours=2)).isoformat(),
        ended_at=(base - timedelta(minutes=30)).isoformat(),
        team_size=2, score=500,
    ))

    assert coach_stats(db, "Steam|1|0")["last_match"]["match_guid"] == "ONES"
    assert coach_stats(db, "Steam|1|0", mode=1)["last_match"]["match_guid"] == "ONES"
    assert coach_stats(db, "Steam|1|0", mode=2)["last_match"]["match_guid"] == "TWOS"
    assert coach_stats(db, "Steam|1|0", mode=3)["last_match"] is None


def test_coach_stats_mode_excludes_legacy_null_rows(tmp_path):
    """Matches saved before team_size tracking (NULL) must not appear under
    a specific mode tab — only in 'All'."""
    db = open_db(tmp_path / "stats.db")
    save_match(db, make_snapshot(match_guid="OLD", team_size=None))
    save_match(db, make_snapshot(
        match_guid="NEW", team_size=2,
        started_at="2026-05-01T20:00:00+00:00",
        ended_at="2026-05-01T20:05:00+00:00",
    ))

    assert coach_stats(db, "Steam|1|0", mode=2)["last_match"]["match_guid"] == "NEW"
    # In "All", the most recent (NEW) is the last_match and rolling avg sees OLD
    assert coach_stats(db, "Steam|1|0")["last_match"]["match_guid"] == "NEW"
    assert coach_stats(db, "Steam|1|0")["rolling_avg"]["count"] == 1


def test_coach_stats_rolling_avg_filters_by_mode(tmp_path):
    from datetime import datetime, timedelta

    db = open_db(tmp_path / "stats.db")
    base = datetime.now().astimezone()
    # Latest match is 2v2 with score 300 — excluded from rolling avg
    save_match(db, make_snapshot(
        match_guid="LATEST",
        started_at=(base - timedelta(minutes=5)).isoformat(),
        ended_at=base.isoformat(),
        team_size=2, score=300,
    ))
    # Two 2v2 history matches with score 600 each
    for i in range(2):
        save_match(db, make_snapshot(
            match_guid=f"P2V2_{i}",
            started_at=(base - timedelta(hours=i + 2)).isoformat(),
            ended_at=(base - timedelta(hours=i + 1)).isoformat(),
            team_size=2, score=600,
        ))
    # One 3v3 history match with score 100 — must not pollute the 2v2 avg
    save_match(db, make_snapshot(
        match_guid="THREES",
        started_at=(base - timedelta(hours=5)).isoformat(),
        ended_at=(base - timedelta(hours=4)).isoformat(),
        team_size=3, score=100,
    ))

    res = coach_stats(db, "Steam|1|0", mode=2)
    assert res["rolling_avg"]["score"] == 600  # only the two P2V2 history rows


def test_coach_stats_trend_filters_by_mode(tmp_path):
    from datetime import datetime, timedelta

    db = open_db(tmp_path / "stats.db")
    today = datetime.now().astimezone()
    yesterday = today - timedelta(days=1)

    save_match(db, make_snapshot(match_guid="T2V2", started_at=today.isoformat(),
                                 ended_at=today.isoformat(), won=True, team_size=2))
    save_match(db, make_snapshot(match_guid="Y3V3", started_at=yesterday.isoformat(),
                                 ended_at=yesterday.isoformat(), won=False, team_size=3))

    res_2 = coach_stats(db, "Steam|1|0", mode=2)
    days = [d["day"] for d in res_2["trend"]]
    assert today.strftime("%Y-%m-%d") in days
    assert yesterday.strftime("%Y-%m-%d") not in days


# ── match_events ────────────────────────────────────────────────────────────


def test_open_db_creates_match_events_table(tmp_path):
    db = open_db(tmp_path / "stats.db")
    cols = {row[1] for row in db.execute("PRAGMA table_info(match_events)")}
    assert {"id", "match_guid", "type", "actor_id", "actor_name",
            "actor_team", "occurred_at", "payload"} <= cols


def test_save_match_events_persists_rows(tmp_path):
    db = open_db(tmp_path / "stats.db")
    events = [
        {
            "type": "goal", "actor_id": "Steam|1|0", "actor_name": "alas",
            "actor_team": 0, "occurred_at": "2026-05-13T20:00:01+00:00",
            "payload": {"speed": 84.5, "time": 22.0},
        },
        {
            "type": "countdown_begin", "actor_id": None, "actor_name": None,
            "actor_team": None, "occurred_at": "2026-05-13T19:59:55+00:00",
            "payload": {},
        },
    ]

    inserted = save_match_events(db, "M1", events)
    assert inserted == 2

    rows = db.execute(
        "SELECT type, actor_name, payload FROM match_events "
        "WHERE match_guid='M1' ORDER BY occurred_at"
    ).fetchall()
    assert rows[0][0] == "countdown_begin"
    assert rows[1] == ("goal", "alas", json.dumps({"speed": 84.5, "time": 22.0}))


def test_save_match_events_noop_for_empty_inputs(tmp_path):
    db = open_db(tmp_path / "stats.db")
    assert save_match_events(db, "M1", []) == 0
    assert save_match_events(db, None, [{"type": "goal", "occurred_at": "x"}]) == 0


def test_get_match_events_round_trips_with_payload(tmp_path):
    db = open_db(tmp_path / "stats.db")
    save_match_events(db, "M1", [
        {"type": "countdown_begin", "occurred_at": "2026-05-13T20:00:00+00:00", "payload": {}},
        {"type": "goal", "actor_id": "Steam|1|0", "actor_name": "alas", "actor_team": 0,
         "occurred_at": "2026-05-13T20:01:14+00:00", "payload": {"speed": 78.1}},
    ])

    events = get_match_events(db, "M1")
    assert [e["type"] for e in events] == ["countdown_begin", "goal"]
    assert events[1]["actor_name"] == "alas"
    assert events[1]["payload"] == {"speed": 78.1}
    assert events[0]["payload"] == {}


def test_get_match_events_empty_for_unknown_guid(tmp_path):
    db = open_db(tmp_path / "stats.db")
    assert get_match_events(db, "DOESNT_EXIST") == []
    assert get_match_events(db, "") == []


def test_coach_stats_includes_last_match_events(tmp_path):
    db = open_db(tmp_path / "stats.db")
    save_match(db, make_snapshot(match_guid="WITH_EVENTS"))
    save_match_events(db, "WITH_EVENTS", [
        {"type": "goal", "actor_name": "alas", "actor_team": 0,
         "occurred_at": "2026-04-30T20:02:00+00:00", "payload": {"speed": 80.0}},
    ])

    result = coach_stats(db, "Steam|1|0")
    assert len(result["last_match_events"]) == 1
    assert result["last_match_events"][0]["actor_name"] == "alas"


def test_coach_stats_last_match_events_empty_when_no_last_match(tmp_path):
    db = open_db(tmp_path / "stats.db")
    result = coach_stats(db, "Steam|1|0")
    assert result["last_match_events"] == []


# ── ball_touches ────────────────────────────────────────────────────────────


def test_open_db_creates_ball_touches_table(tmp_path):
    db = open_db(tmp_path / "stats.db")
    cols = {row[1] for row in db.execute("PRAGMA table_info(ball_touches)")}
    assert {"id", "match_guid", "player_id", "player_name", "team",
            "post_hit_speed", "occurred_at"} <= cols


def test_save_ball_touches_persists_rows(tmp_path):
    db = open_db(tmp_path / "stats.db")
    touches = [
        {"player_id": "Steam|1|0", "player_name": "alas", "team": 0,
         "post_hit_speed": 92.5, "occurred_at": "2026-05-13T20:00:01+00:00"},
        {"player_id": "Epic|2|0", "player_name": "jstn", "team": 1,
         "post_hit_speed": 60.0, "occurred_at": "2026-05-13T20:00:02+00:00"},
    ]

    inserted = save_ball_touches(db, "M1", touches)
    assert inserted == 2

    rows = db.execute(
        "SELECT player_name, team, post_hit_speed FROM ball_touches "
        "WHERE match_guid='M1' ORDER BY id"
    ).fetchall()
    assert rows == [("alas", 0, 92.5), ("jstn", 1, 60.0)]


def test_save_ball_touches_noop_for_empty_inputs(tmp_path):
    db = open_db(tmp_path / "stats.db")
    assert save_ball_touches(db, "M1", []) == 0
    assert save_ball_touches(db, None, [
        {"player_id": "x", "occurred_at": "y", "post_hit_speed": 1.0}
    ]) == 0


def test_migration_adds_team_size_column_to_existing_db(tmp_path):
    """A DB created before team_size existed must get the column on open."""
    import sqlite3

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            match_guid TEXT NOT NULL,
            player_id TEXT NOT NULL,
            player_name TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            UNIQUE(match_guid, player_id)
        )
    """)
    conn.execute(
        "INSERT INTO matches (match_guid, player_id, started_at, ended_at) "
        "VALUES (?, ?, ?, ?)",
        ("OLD", "Steam|1|0", "2026-01-01T00:00:00+00:00", "2026-01-01T00:05:00+00:00"),
    )
    conn.commit()
    conn.close()

    migrated = open_db(db_path)
    columns = {row[1] for row in migrated.execute("PRAGMA table_info(matches)")}
    assert "team_size" in columns

    row = migrated.execute("SELECT team_size FROM matches WHERE match_guid='OLD'").fetchone()
    assert row == (None,)
