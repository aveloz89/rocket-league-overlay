import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import coach_stats, open_db, save_match, today_stats  # noqa: E402


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
        for phrase in ["shot accuracy", "posesión", "score/min", "boost wasted", "sin boost"]
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
