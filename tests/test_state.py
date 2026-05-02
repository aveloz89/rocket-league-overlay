import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state import (  # noqa: E402
    LAST_TOUCH_OPPONENT,
    LAST_TOUCH_SELF,
    LAST_TOUCH_TEAM,
    MatchAggregator,
)


def make_state(*, me_boost=50, me_speed=0.0, me_on_ground=True, me_has_car=True,
               blue=0, orange=0, target="alas", in_replay=False):
    return {
        "MatchGuid": "M1",
        "Players": [
            {
                "Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
                "Score": 100, "Goals": 1, "Shots": 2, "Saves": 1, "Assists": 0,
                "Demos": 0, "Touches": 5, "Boost": me_boost, "Speed": me_speed,
                "bOnGround": me_on_ground, "bHasCar": me_has_car,
            },
            {
                "Name": "jstn", "PrimaryId": "Epic|2|0", "TeamNum": 1,
                "Score": 200, "Goals": 0, "Shots": 1, "Saves": 0, "Assists": 1,
                "Demos": 0, "Touches": 3, "Boost": 60, "Speed": 0,
                "bOnGround": True, "bHasCar": True,
            },
        ],
        "Game": {
            "Teams": [
                {"TeamNum": 0, "Score": blue},
                {"TeamNum": 1, "Score": orange},
            ],
            "TimeSeconds": 240,
            "bOvertime": False,
            "bReplay": in_replay,
            "Arena": "stadium",
            "bHasTarget": True,
            "Target": {"Name": target, "Shortcut": 1, "TeamNum": 0},
        },
    }


def test_identity_detected_after_30_target_frames():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    detected = None
    for _ in range(30):
        d = agg.on_update_state(make_state(target="alas"))
        detected = detected or d

    assert detected == {"id": "Steam|1|0", "name": "alas"}
    assert agg.me_id == "Steam|1|0"


def test_identity_not_detected_when_target_flips():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    # Cycle through 3 names so none reaches 30 in the 75-frame window (max 25 each)
    names = ["alas", "jstn", "kuxir"]
    for i in range(75):
        agg.on_update_state(make_state(target=names[i % 3]))

    assert agg.me_id is None


def test_boost_avg_and_zero_boost_pct():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    for boost in (100, 50, 0, 0):
        agg.on_update_state(make_state(me_boost=boost))

    assert agg.boost_avg() == round((100 + 50 + 0 + 0) / 4)
    assert agg.time_zero_boost_pct() == 50


def test_supersonic_pct_threshold():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    for speed in (0, 10, 22, 25, 30):
        agg.on_update_state(make_state(me_speed=speed))

    # 22, 25, 30 are >= 22 → 3/5 = 60%
    assert agg.time_supersonic_pct() == 60


def test_airborne_pct_from_on_ground():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    for on_ground in (True, False, False, True):
        agg.on_update_state(make_state(me_on_ground=on_ground))

    assert agg.time_airborne_pct() == 50


def test_demos_taken_counts_true_to_false_transitions():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    transitions = [True, True, False, False, True, False]
    for has_car in transitions:
        agg.on_update_state(make_state(me_has_car=has_car))

    # T→F transitions: idx 1→2, 4→5 = 2 demos taken
    assert agg.demos_taken == 2


def test_replay_frames_skipped_for_derivations():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_update_state(make_state(me_speed=30, in_replay=False))
    agg.on_update_state(make_state(me_speed=30, in_replay=True))
    agg.on_update_state(make_state(me_speed=0, in_replay=False))

    assert agg.frames == 2  # replay frame skipped
    assert agg.time_supersonic_pct() == 50


def test_ball_hit_self_updates_hardest_and_last_touch():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0

    agg.on_ball_hit({
        "Players": [{"Name": "alas", "TeamNum": 0}],
        "Ball": {"PreHitSpeed": 30, "PostHitSpeed": 85.5},
    })
    agg.on_ball_hit({
        "Players": [{"Name": "alas", "TeamNum": 0}],
        "Ball": {"PreHitSpeed": 30, "PostHitSpeed": 60.0},
    })

    assert agg.hardest_hit == 85.5
    assert agg.last_touch_kind == LAST_TOUCH_SELF


def test_ball_hit_classifies_team_vs_opponent():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0

    agg.on_ball_hit({
        "Players": [{"Name": "kuxir", "TeamNum": 0}],
        "Ball": {"PostHitSpeed": 50},
    })
    assert agg.last_touch_kind == LAST_TOUCH_TEAM

    agg.on_ball_hit({
        "Players": [{"Name": "jstn", "TeamNum": 1}],
        "Ball": {"PostHitSpeed": 50},
    })
    assert agg.last_touch_kind == LAST_TOUCH_OPPONENT


def test_won_returns_correct_team_outcome():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_update_state(make_state(blue=3, orange=1))
    assert agg.won() is True

    agg.on_update_state(make_state(blue=1, orange=3))
    assert agg.won() is False


def test_boost_wasted_pct_counts_full_boost_frames():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    for boost in (100, 100, 100, 50):
        agg.on_update_state(make_state(me_boost=boost))

    assert agg.boost_wasted_pct() == 75


def test_avg_shot_power_only_counts_own_hits():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0

    agg.on_ball_hit({"Players": [{"Name": "alas", "TeamNum": 0}], "Ball": {"PostHitSpeed": 80}})
    agg.on_ball_hit({"Players": [{"Name": "alas", "TeamNum": 0}], "Ball": {"PostHitSpeed": 60}})
    agg.on_ball_hit({"Players": [{"Name": "jstn", "TeamNum": 1}], "Ball": {"PostHitSpeed": 30}})

    assert agg.ball_hits == 2
    assert agg.avg_shot_power() == 70.0


def test_possession_pct_from_team_hits():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0

    for _ in range(3):
        agg.on_ball_hit({"Players": [{"Name": "alas", "TeamNum": 0}], "Ball": {"PostHitSpeed": 50}})
    for _ in range(2):
        agg.on_ball_hit({"Players": [{"Name": "jstn", "TeamNum": 1}], "Ball": {"PostHitSpeed": 50}})

    assert agg.possession_pct() == 60


def test_goal_participation_pct():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    # team scored 3, I scored 1 + 1 assist = 67%
    state = make_state(blue=3)
    state["Players"][0]["Goals"] = 1
    state["Players"][0]["Assists"] = 1
    agg.on_update_state(state)

    assert agg.goal_participation_pct() == 67


def test_speed_exposed_in_overlay_dict():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_update_state(make_state(me_speed=18.5))

    out = agg.to_overlay_dict()
    assert out["match"]["speed"] == 18.5


def test_score_per_minute_uses_real_elapsed_time(monkeypatch):
    """score_per_min should track wall time since match start, not the in-game clock."""
    import state as state_module

    fake_now = [1000.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: fake_now[0])

    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})  # started_monotonic = 1000.0

    # 60 seconds (1 min) elapsed, score 200 → 200/min
    fake_now[0] = 1060.0
    state = make_state()
    state["Players"][0]["Score"] = 200
    agg.on_update_state(state)

    assert agg.score_per_minute() == 200


def test_score_per_minute_zero_until_first_frame(monkeypatch):
    import state as state_module

    monkeypatch.setattr(state_module.time, "monotonic", lambda: 1000.0)
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    # No frames seen yet
    assert agg.score_per_minute() == 0


def test_is_match_guid_change_detects_rotation():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    assert agg.is_match_guid_change({"MatchGuid": "M1"}) is False
    assert agg.is_match_guid_change({"MatchGuid": "M2"}) is True
    # Empty/missing guid is not a change
    assert agg.is_match_guid_change({}) is False
    assert agg.is_match_guid_change({"MatchGuid": ""}) is False


def test_first_event_without_initialized_still_inits_match():
    """If we join mid-stream and the first event is an UpdateState, the aggregator
    should still set up the match guid and start tracking."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_update_state(make_state())

    assert agg.match_guid == "M1"
    assert agg.started_at is not None
    assert agg.frames == 1
