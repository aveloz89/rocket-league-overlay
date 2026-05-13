import logging
import sys
from pathlib import Path

import pytest

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


# ── Positioning ─────────────────────────────────────────────────────────────


def make_state_with_locations(
    *, me_y, ball_y, me_team=0, me_x=0.0, me_z=17.0,
    ball_x=0.0, ball_z=90.0, teammate_y=None,
):
    """Build an UpdateState with Location data for both player and ball."""
    me = {
        "Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": me_team,
        "Score": 100, "Goals": 0, "Shots": 0, "Saves": 0, "Assists": 0,
        "Demos": 0, "Touches": 0, "Boost": 50, "Speed": 0,
        "bOnGround": True, "bHasCar": True,
        "Location": {"X": me_x, "Y": me_y, "Z": me_z},
    }
    players = [me]
    if teammate_y is not None:
        players.append({
            "Name": "kuxir", "PrimaryId": "Steam|2|0", "TeamNum": me_team,
            "Score": 0, "Goals": 0, "Shots": 0, "Saves": 0, "Assists": 0,
            "Demos": 0, "Touches": 0, "Boost": 50, "Speed": 0,
            "bOnGround": True, "bHasCar": True,
            "Location": {"X": 1500.0, "Y": teammate_y, "Z": 17.0},
        })
    return {
        "MatchGuid": "M1",
        "Players": players,
        "Game": {
            "Teams": [{"TeamNum": 0, "Score": 0}, {"TeamNum": 1, "Score": 0}],
            "TimeSeconds": 240, "bOvertime": False, "bReplay": False,
            "Arena": "stadium", "bHasTarget": False,
            "Ball": {"Speed": 0, "Location": {"X": ball_x, "Y": ball_y, "Z": ball_z}},
        },
    }


def test_positioning_def_off_thirds_for_blue_team():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0  # Blue defends -Y
    agg.on_initialized({"MatchGuid": "M1"})

    # Blue team — defensive third is Y < -2000, offensive Y > 2000
    for me_y in (-3000, -3000, 0, 3000):  # 2 def, 1 mid, 1 off
        agg.on_update_state(make_state_with_locations(me_y=me_y, ball_y=0))

    assert agg.frames_pos == 4
    assert agg.time_def_third_pct() == 50
    assert agg.time_off_third_pct() == 25
    assert agg.time_mid_third_pct() == 25


def test_positioning_mirrors_for_orange_team():
    """Orange defends +Y, so Y=+3000 should count as defensive for them."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 1
    agg.on_initialized({"MatchGuid": "M1"})

    for me_y in (3000, 3000, 0, -3000):
        agg.on_update_state(make_state_with_locations(me_y=me_y, ball_y=0, me_team=1))

    assert agg.time_def_third_pct() == 50
    assert agg.time_off_third_pct() == 25


def test_behind_ball_pct():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0
    agg.on_initialized({"MatchGuid": "M1"})

    # Blue team: "behind ball" means me_y < ball_y (more negative = more defensive)
    for me_y, ball_y in ((-3000, -1000), (-2000, -1000), (1000, 0), (-500, 500)):
        agg.on_update_state(make_state_with_locations(me_y=me_y, ball_y=ball_y))

    # 3 of 4 frames have me_y < ball_y
    assert agg.behind_ball_pct() == 75


def test_dist_to_ball_avg():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0
    agg.on_initialized({"MatchGuid": "M1"})

    # Place me at (0,0,0) and ball at (0, dist, 0) so dist == |dy|
    for dist in (1000, 2000, 3000):
        agg.on_update_state(make_state_with_locations(
            me_y=0, ball_y=dist, me_z=0, ball_z=0,
        ))

    assert agg.dist_to_ball_avg() == 2000


def test_last_back_pct_with_teammate():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0
    agg.on_initialized({"MatchGuid": "M1"})

    # me deeper than teammate → last back
    agg.on_update_state(make_state_with_locations(me_y=-3000, ball_y=0, teammate_y=-1000))
    # teammate deeper than me → not last back
    agg.on_update_state(make_state_with_locations(me_y=-1000, ball_y=0, teammate_y=-3000))

    assert agg.last_back_pct() == 50


def test_positioning_unchanged_when_no_location():
    """If the API doesn't expose Location, positioning counters stay at zero
    and pct accessors return 0 rather than crashing."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    for _ in range(5):
        agg.on_update_state(make_state())  # no Location field

    assert agg.frames_pos == 0
    assert agg.time_def_third_pct() == 0
    assert agg.behind_ball_pct() == 0
    assert agg.has_positioning_data() is False


# ── Boost pickup detection ──────────────────────────────────────────────────


def test_big_pad_detected_from_zero_to_full_boost():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    # 0 → 100 jump qualifies as big pad pickup
    agg.on_update_state(make_state(me_boost=0))
    agg.on_update_state(make_state(me_boost=100))
    assert agg.big_pads == 1
    assert agg.small_pads == 0


def test_small_pad_detected_from_modest_increment():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    # 50 → 62 (delta 12) qualifies as small pad
    agg.on_update_state(make_state(me_boost=50))
    agg.on_update_state(make_state(me_boost=62))
    assert agg.small_pads == 1
    assert agg.big_pads == 0


def test_no_pad_for_natural_consumption():
    """Decreasing boost (consumption) must not be classified as a pickup."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    for boost in (100, 80, 60, 40, 20, 0):
        agg.on_update_state(make_state(me_boost=boost))

    assert agg.big_pads == 0
    assert agg.small_pads == 0


def test_respawn_does_not_count_as_pad_pickup():
    """Demolition + respawn sequence: bHasCar flips True→False (death) then
    False→True with Boost=33 (RL gives 1/3 boost on respawn). The 33-point
    jump must NOT be counted as a small-pad pickup."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    # Healthy frame at 60 boost
    agg.on_update_state(make_state(me_boost=60, me_has_car=True))
    # Demolition: bHasCar=False, Boost=0
    agg.on_update_state(make_state(me_boost=0, me_has_car=False))
    # Respawn: bHasCar=True, Boost=33
    agg.on_update_state(make_state(me_boost=33, me_has_car=True))

    assert agg.demos_taken == 1
    assert agg.big_pads == 0
    assert agg.small_pads == 0

    # Real pickup after respawn should still register
    agg.on_update_state(make_state(me_boost=33, me_has_car=True))
    agg.on_update_state(make_state(me_boost=45, me_has_car=True))
    assert agg.small_pads == 1


def test_boost_stolen_detected_in_opponent_half():
    """A pickup at Y > 0 (after team mirror) is counted as stolen."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0
    agg.on_initialized({"MatchGuid": "M1"})

    # Pickup in own half (Y < 0): not stolen.
    # First frame seeds prev_boost=10, second frame jumps to 100 → big pad.
    s_low = make_state_with_locations(me_y=-1000, ball_y=0)
    s_low["Players"][0]["Boost"] = 10
    agg.on_update_state(s_low)
    s_full = make_state_with_locations(me_y=-1000, ball_y=0)
    s_full["Players"][0]["Boost"] = 100
    agg.on_update_state(s_full)
    assert agg.big_pads == 1
    assert agg.boost_stolen == 0

    # Pickup in opponent half: stolen.
    s2 = make_state_with_locations(me_y=2000, ball_y=0)
    s2["Players"][0]["Boost"] = 5
    agg.on_update_state(s2)
    s3 = make_state_with_locations(me_y=2000, ball_y=0)
    s3["Players"][0]["Boost"] = 100
    agg.on_update_state(s3)
    assert agg.boost_stolen == 1


# ── Aerials ─────────────────────────────────────────────────────────────────


def test_aerial_touch_counted_when_ball_hit_while_airborne():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0

    # First frame establishes me as airborne
    agg.on_update_state(make_state(me_on_ground=False))
    agg.on_ball_hit({
        "Players": [{"Name": "alas", "TeamNum": 0}],
        "Ball": {"PostHitSpeed": 80},
    })
    assert agg.aerial_touches == 1
    assert agg.ball_hits == 1

    # Now grounded — next ball hit is not aerial
    agg.on_update_state(make_state(me_on_ground=True))
    agg.on_ball_hit({
        "Players": [{"Name": "alas", "TeamNum": 0}],
        "Ball": {"PostHitSpeed": 50},
    })
    assert agg.aerial_touches == 1
    assert agg.ball_hits == 2


def test_air_touch_pct():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0

    agg.on_update_state(make_state(me_on_ground=False))
    for _ in range(3):
        agg.on_ball_hit({
            "Players": [{"Name": "alas", "TeamNum": 0}],
            "Ball": {"PostHitSpeed": 50},
        })
    agg.on_update_state(make_state(me_on_ground=True))
    for _ in range(2):
        agg.on_ball_hit({
            "Players": [{"Name": "alas", "TeamNum": 0}],
            "Ball": {"PostHitSpeed": 50},
        })

    # 3 aerial of 5 total
    assert agg.air_touch_pct() == 60


def test_fast_aerial_counted_when_takeoff_with_boost_reaches_high_z(monkeypatch):
    import state as state_module
    fake_now = [1000.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: fake_now[0])

    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    # On ground with full boost
    s_ground = make_state_with_locations(me_y=0, ball_y=0, me_z=17.0)
    s_ground["Players"][0]["Boost"] = 50
    agg.on_update_state(s_ground)

    # Takeoff
    fake_now[0] = 1000.5
    s_air = make_state_with_locations(me_y=0, ball_y=0, me_z=200.0)
    s_air["Players"][0]["Boost"] = 50
    s_air["Players"][0]["bOnGround"] = False
    agg.on_update_state(s_air)

    # Reaches high Z within window
    fake_now[0] = 1001.5
    s_high = make_state_with_locations(me_y=0, ball_y=0, me_z=900.0)
    s_high["Players"][0]["Boost"] = 30
    s_high["Players"][0]["bOnGround"] = False
    agg.on_update_state(s_high)

    assert agg.fast_aerials == 1


def test_fast_aerial_not_counted_when_takeoff_boost_too_low(monkeypatch):
    import state as state_module
    fake_now = [1000.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: fake_now[0])

    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    s_ground = make_state_with_locations(me_y=0, ball_y=0, me_z=17.0)
    s_ground["Players"][0]["Boost"] = 10  # below FAST_AERIAL_BOOST_MIN
    agg.on_update_state(s_ground)

    fake_now[0] = 1000.5
    s_air = make_state_with_locations(me_y=0, ball_y=0, me_z=900.0)
    s_air["Players"][0]["Boost"] = 5
    s_air["Players"][0]["bOnGround"] = False
    agg.on_update_state(s_air)

    assert agg.fast_aerials == 0


# ── DB snapshot exposes new fields ──────────────────────────────────────────


def test_db_snapshot_includes_new_fields():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.me_team = 0
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_update_state(make_state_with_locations(me_y=-3000, ball_y=0))

    snap = agg.to_db_snapshot()
    assert "time_def_third_pct" in snap
    assert "behind_ball_pct" in snap
    assert "big_pads" in snap
    assert "aerial_touches" in snap
    assert "fast_aerials" in snap
    assert "score_per_min" in snap
    # With Location present, positioning is captured (not None)
    assert snap["time_def_third_pct"] == 100  # all frames in def third


# ── Statfeed highlights ──────────────────────────────────────────────────────


def _make_statfeed(event_name: str, causer_id: str = "Steam|1|0",
                   causer_name: str = "alas") -> dict:
    """Build a StatfeedEvent Data payload."""
    return {
        "MatchGuid": "M1",
        "Event": event_name,
        "Causer": {"Name": causer_name, "PrimaryId": causer_id, "TeamNum": 0},
        "Victim": {"Name": "jstn", "PrimaryId": "Epic|2|0", "TeamNum": 1},
    }


# T1: _STATFEED_EVENTS mapping has correct keys and values
def test_statfeed_events_mapping_keys_and_aliases():
    from state import _STATFEED_EVENTS

    # All 9 attributes are reachable
    assert set(_STATFEED_EVENTS.values()) == {
        "epic_saves", "hat_tricks", "aerial_goals", "bicycle_goals",
        "long_goals", "centers", "pool_shots", "saviors", "mvps",
    }
    # Aliases map to the same attribute
    assert _STATFEED_EVENTS["center"] == _STATFEED_EVENTS["centeringball"] == "centers"
    assert _STATFEED_EVENTS["saviour"] == _STATFEED_EVENTS["savior"] == "saviors"
    # Total: 11 keys (9 unique events + 2 aliases)
    assert len(_STATFEED_EVENTS) == 11


# T2: new attributes default to 0 on a fresh aggregator
def test_statfeed_attributes_default_to_zero():
    agg = MatchAggregator()
    for attr in ("epic_saves", "hat_tricks", "aerial_goals", "bicycle_goals",
                 "long_goals", "centers", "pool_shots", "saviors", "mvps"):
        assert getattr(agg, attr) == 0, f"{attr} should default to 0"


# T3: EpicSave increments epic_saves when Causer.PrimaryId matches
def test_on_statfeed_event_increments_epic_saves_by_id():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event(_make_statfeed("EpicSave"))
    assert agg.epic_saves == 1


# T4: ignore event when Causer does not match user
def test_on_statfeed_event_ignores_other_player():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event(_make_statfeed("EpicSave", causer_id="Epic|999|0",
                                         causer_name="jstn"))
    assert agg.epic_saves == 0


# T5: ignore event during replay
def test_on_statfeed_event_ignores_during_replay():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.in_replay = True

    agg.on_statfeed_event(_make_statfeed("EpicSave"))
    assert agg.epic_saves == 0


# T6: ignore event without Causer or without Event
def test_on_statfeed_event_ignores_missing_causer():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    # No Causer key
    agg.on_statfeed_event({"MatchGuid": "M1", "Event": "EpicSave"})
    assert agg.epic_saves == 0


def test_on_statfeed_event_ignores_missing_event_name():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event({
        "MatchGuid": "M1",
        "Causer": {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0},
    })
    assert agg.epic_saves == 0


# T7: ignore event when match_guid is None
def test_on_statfeed_event_ignores_without_active_match():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    # match_guid is None (no Initialized called)

    agg.on_statfeed_event(_make_statfeed("EpicSave"))
    assert agg.epic_saves == 0


# T8: fallback by name when me_id not yet locked
def test_on_statfeed_event_fallback_by_name():
    agg = MatchAggregator()
    agg.me_name = "alas"  # name known, id not yet locked
    agg.me_id = None
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event(_make_statfeed("EpicSave", causer_id="", causer_name="alas"))
    assert agg.epic_saves == 1


# T9: alias events Center/CenteringBall → centers; Saviour/Savior → saviors
def test_on_statfeed_event_center_and_centeringball_aliases():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event(_make_statfeed("Center"))
    agg.on_statfeed_event(_make_statfeed("CenteringBall"))
    assert agg.centers == 2


def test_on_statfeed_event_saviour_and_savior_aliases():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event(_make_statfeed("Saviour"))
    agg.on_statfeed_event(_make_statfeed("Savior"))
    assert agg.saviors == 2


# T10: all remaining event types increment their respective counters
@pytest.mark.parametrize("event_name,attr", [
    ("HatTrick", "hat_tricks"),
    ("AerialGoal", "aerial_goals"),
    ("BicycleGoal", "bicycle_goals"),
    ("LongGoal", "long_goals"),
    ("PoolShot", "pool_shots"),
    ("MVP", "mvps"),
    ("Center", "centers"),
    ("Savior", "saviors"),
])
def test_on_statfeed_event_all_event_types(event_name, attr):
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_statfeed_event(_make_statfeed(event_name))
    assert getattr(agg, attr) == 1


# T11: unknown event logs debug and does not increment anything
def test_on_statfeed_event_unknown_logs_debug_and_no_increment(caplog):
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    with caplog.at_level(logging.DEBUG):
        agg.on_statfeed_event(_make_statfeed("FlipReset"))

    assert agg.epic_saves == 0
    assert any("FlipReset" in r.message or "flipreset" in r.message.lower()
               for r in caplog.records)


# T12: to_db_snapshot includes the 9 highlight keys
def test_db_snapshot_includes_highlight_fields():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_statfeed_event(_make_statfeed("EpicSave"))
    agg.on_statfeed_event(_make_statfeed("HatTrick"))

    snap = agg.to_db_snapshot()
    assert snap["epic_saves"] == 1
    assert snap["hat_tricks"] == 1
    for key in ("aerial_goals", "bicycle_goals", "long_goals",
                "centers", "pool_shots", "saviors", "mvps"):
        assert snap[key] == 0


# T13: reset_match (via on_initialized) resets all highlight counters to 0
def test_statfeed_counters_reset_on_new_match():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_statfeed_event(_make_statfeed("EpicSave"))
    assert agg.epic_saves == 1

    agg.on_initialized({"MatchGuid": "M2"})
    assert agg.epic_saves == 0


# ── team_size ────────────────────────────────────────────────────────────────


def test_team_size_captured_from_update_state():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    state = make_state()
    state["Game"]["TeamSize"] = 2
    agg.on_update_state(state)

    assert agg.team_size == 2
    assert agg.to_db_snapshot()["team_size"] == 2


def test_team_size_remains_none_when_field_missing():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_update_state(make_state())  # no TeamSize

    assert agg.team_size is None
    assert agg.to_db_snapshot()["team_size"] is None


def test_team_size_ignores_non_int_payload():
    """Bad payload from the API must not corrupt team_size."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    state = make_state()
    state["Game"]["TeamSize"] = "two"
    agg.on_update_state(state)

    assert agg.team_size is None


def test_team_size_resets_with_new_match():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    state = make_state()
    state["Game"]["TeamSize"] = 3
    agg.on_update_state(state)
    assert agg.team_size == 3

    agg.on_initialized({"MatchGuid": "M2"})
    assert agg.team_size is None


# ── Identity detection cascade ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "key", ["TargetPlayer", "FocusedPlayer", "Player", "SpectatedPlayer", "PrimaryPlayer"]
)
def test_identity_detected_via_alternate_target_key(key):
    """Psyonix has shipped the spectated-player payload under several keys
    over RL's history. Detection must still lock on when only the alternate
    key is populated."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})
    for _ in range(30):
        s = make_state(target="alas")
        s["Game"].pop("Target", None)
        s["Game"][key] = {"Name": "alas", "Shortcut": 1, "TeamNum": 0}
        agg.on_update_state(s)
    assert agg.me_id == "Steam|1|0"


def test_identity_detection_ignores_bhas_target_flag():
    """Some builds populate Target.Name without setting bHasTarget=True."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})
    for _ in range(30):
        s = make_state(target="alas")
        s["Game"]["bHasTarget"] = False
        agg.on_update_state(s)
    assert agg.me_id == "Steam|1|0"


# ── Ball state in UpdateState ────────────────────────────────────────────────


def test_ball_speed_and_team_captured_from_update_state():
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    s = make_state()
    s["Game"]["Ball"] = {"Speed": 28.4, "TeamNum": 1}
    agg.on_update_state(s)

    out = agg.to_overlay_dict()
    assert out["context"]["ball_speed"] == 28.4
    assert out["context"]["ball_team"] == 1


def test_ball_team_rejects_invalid_payload():
    """Speed must be numeric; TeamNum must be 0 or 1."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    s = make_state()
    s["Game"]["Ball"] = {"Speed": "fast", "TeamNum": 5}
    agg.on_update_state(s)

    out = agg.to_overlay_dict()
    assert out["context"]["ball_speed"] == 0.0
    assert out["context"]["ball_team"] is None


# ── Lobby roster ────────────────────────────────────────────────────────────


def test_overlay_dict_exposes_slim_players():
    """to_overlay_dict surfaces a slim per-player payload for the compact roster."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_update_state(make_state(me_boost=42))

    out = agg.to_overlay_dict()
    assert len(out["players"]) == 2
    me = next(p for p in out["players"] if p["is_me"])
    opp = next(p for p in out["players"] if not p["is_me"])
    assert me == {"id": "Steam|1|0", "name": "alas", "team": 0, "boost": 42, "is_me": True}
    assert opp["name"] == "jstn"
    assert opp["team"] == 1
    assert opp["boost"] == 60


def test_overlay_dict_players_skips_entries_without_id_or_name():
    """A spectator or transient placeholder slot must not show up in the roster."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})
    s = make_state()
    s["Players"].append({"Name": "", "PrimaryId": None, "TeamNum": 1, "Boost": 0})
    s["Players"].append({"Name": "ghost", "TeamNum": 0, "Boost": 0})  # no PrimaryId
    agg.on_update_state(s)

    out = agg.to_overlay_dict()
    assert len(out["players"]) == 2  # only the two real players


def test_to_db_snapshots_returns_me_plus_others():
    """to_db_snapshots yields the enriched me-row + a slim row per other player."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_update_state(make_state(blue=2, orange=1))

    snaps = agg.to_db_snapshots()
    assert len(snaps) == 2
    me_snap = next(s for s in snaps if s["player_id"] == "Steam|1|0")
    opp_snap = next(s for s in snaps if s["player_id"] == "Epic|2|0")

    # me-row carries derived metrics (boost_avg, time_supersonic_pct, ...)
    assert "boost_avg" in me_snap
    assert me_snap["me_team"] == 0
    assert me_snap["won"] is True

    # other-row is slim — only end-of-match player stats
    assert opp_snap["player_name"] == "jstn"
    assert opp_snap["me_team"] == 1
    assert opp_snap["won"] is False
    assert opp_snap["goals"] == 0
    assert opp_snap["assists"] == 1
    assert "boost_avg" not in opp_snap  # derived metrics are not built for non-me players


def test_to_db_snapshots_skips_me_row_without_identity():
    """If me_id was never detected, only the non-me rows are emitted."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_update_state(make_state())

    snaps = agg.to_db_snapshots()
    # Both Players have a PrimaryId so both come through; no me-row added
    assert len(snaps) == 2
    assert all(s.get("player_id") for s in snaps)


def test_won_for_team_handles_each_side():
    """The per-team won() helper used for non-me rows must mirror won() for me."""
    agg = MatchAggregator()
    agg.blue_score = 3
    agg.orange_score = 1
    assert agg._won_for_team(0) is True
    assert agg._won_for_team(1) is False
    assert agg._won_for_team(-1) is None
    agg.orange_score = 3
    assert agg._won_for_team(0) is None  # tie


# ── Discrete events (goal / kickoff cycles) ─────────────────────────────────


def test_on_goal_scored_buffers_event_with_scorer_and_speed():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_goal_scored({
        "Scorer": {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0},
        "GoalSpeed": 84.5,
        "GoalTime": 22.0,
    })

    assert len(agg.events) == 1
    evt = agg.events[0]
    assert evt["type"] == "goal"
    assert evt["actor_id"] == "Steam|1|0"
    assert evt["actor_name"] == "alas"
    assert evt["actor_team"] == 0
    assert evt["payload"] == {"speed": 84.5, "time": 22.0}


@pytest.mark.parametrize("nested_key", ["Goal", "Player"])
def test_on_goal_scored_scorer_cascade(nested_key):
    """If Psyonix exposes the scorer under Goal or Player instead of Scorer,
    we still pick up the name."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_goal_scored({nested_key: {"Name": "jstn", "TeamNum": 1}, "GoalSpeed": 60})

    evt = agg.events[0]
    assert evt["actor_name"] == "jstn"
    assert evt["actor_team"] == 1


def test_on_goal_scored_falls_back_to_top_level_player_name():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_goal_scored({"PlayerName": "kio", "TeamNum": 0})

    evt = agg.events[0]
    assert evt["actor_name"] == "kio"
    assert evt["actor_team"] == 0


def test_on_goal_scored_omits_speed_when_payload_invalid():
    """Garbage payloads (e.g. speed=None) must not pollute the persisted event."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_goal_scored({"Scorer": {"Name": "alas"}, "GoalSpeed": None, "GoalTime": "fast"})

    evt = agg.events[0]
    assert evt["payload"] == {}


def test_kickoff_cycle_events_buffered_without_actor():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    agg.on_countdown_begin({})
    agg.on_round_started({})

    types = [e["type"] for e in agg.events]
    assert types == ["countdown_begin", "round_started"]
    for e in agg.events:
        assert e["actor_id"] is None
        assert e["actor_name"] is None
        assert e["actor_team"] is None


def test_events_buffer_resets_on_new_match():
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})
    agg.on_goal_scored({"Scorer": {"Name": "alas"}})
    assert len(agg.events) == 1

    agg.on_initialized({"MatchGuid": "M2"})
    assert agg.events == []


def test_event_ignored_when_no_match_active():
    """Events that arrive before MatchInitialized must be dropped silently."""
    agg = MatchAggregator()
    agg.on_goal_scored({"Scorer": {"Name": "alas"}})
    agg.on_countdown_begin({})
    assert agg.events == []


# ── Team totals (macro view) ────────────────────────────────────────────────


def test_team_totals_sum_per_team():
    """teams.{blue,orange} aggregate Goals/Saves/Assists/Demos across the live
    Players[]. Distinct from blue_score/orange_score which come from Game.Teams."""
    agg = MatchAggregator()
    agg.me_id, agg.me_name = "Steam|1|0", "alas"
    agg.on_initialized({"MatchGuid": "M1"})

    s = make_state()
    s["Players"] = [
        {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
         "Score": 0, "Goals": 1, "Saves": 2, "Assists": 0, "Demos": 1,
         "Shots": 0, "Touches": 0, "Boost": 0, "Speed": 0,
         "bOnGround": True, "bHasCar": True},
        {"Name": "tide", "PrimaryId": "Steam|2|0", "TeamNum": 0,
         "Score": 0, "Goals": 0, "Saves": 1, "Assists": 2, "Demos": 0,
         "Shots": 0, "Touches": 0, "Boost": 0, "Speed": 0,
         "bOnGround": True, "bHasCar": True},
        {"Name": "jstn", "PrimaryId": "Epic|4|0", "TeamNum": 1,
         "Score": 0, "Goals": 2, "Saves": 0, "Assists": 1, "Demos": 0,
         "Shots": 0, "Touches": 0, "Boost": 0, "Speed": 0,
         "bOnGround": True, "bHasCar": True},
    ]
    agg.on_update_state(s)

    out = agg.to_overlay_dict()
    assert out["teams"]["blue"] == {"goals": 1, "saves": 3, "assists": 2, "demos": 1}
    assert out["teams"]["orange"] == {"goals": 2, "saves": 0, "assists": 1, "demos": 0}


def test_team_totals_skip_players_without_team():
    """Spectators / placeholders without TeamNum 0/1 must not contribute."""
    agg = MatchAggregator()
    agg.on_initialized({"MatchGuid": "M1"})

    s = make_state()
    # Override with a single legit player + a spectator-style entry
    s["Players"] = [
        {"Name": "alas", "PrimaryId": "Steam|1|0", "TeamNum": 0,
         "Score": 0, "Goals": 3, "Saves": 0, "Assists": 0, "Demos": 0,
         "Shots": 0, "Touches": 0, "Boost": 0, "Speed": 0,
         "bOnGround": True, "bHasCar": True},
        {"Name": "spec", "PrimaryId": "Steam|9|0", "TeamNum": 99,
         "Goals": 100, "Saves": 100},  # bogus team
    ]
    agg.on_update_state(s)

    out = agg.to_overlay_dict()
    assert out["teams"]["blue"]["goals"] == 3
    assert out["teams"]["orange"]["goals"] == 0


def test_team_totals_zero_when_no_players():
    agg = MatchAggregator()
    out = agg.to_overlay_dict()
    assert out["teams"] == {
        "blue":   {"goals": 0, "saves": 0, "assists": 0, "demos": 0},
        "orange": {"goals": 0, "saves": 0, "assists": 0, "demos": 0},
    }
