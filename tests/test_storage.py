import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import open_db, save_match, today_stats  # noqa: E402


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
