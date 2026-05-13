"""Per-match aggregator: tracks personal stats, positioning, boost pickups,
aerials and identity detection from the RL Stats API event stream."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# RL "Speed" in the stats API is uu/s / 100. Supersonic in-game = 2200 uu/s.
SUPERSONIC_THRESHOLD = 22.0

# Positioning thresholds (uu, after team normalization). Field is ~10240 long on Y.
DEFENSIVE_Y = -2000.0
OFFENSIVE_Y = 2000.0

# Boost pickup heuristics — derived from frame-to-frame delta of Player.Boost.
# Big pads grant 100 (full); small pads grant 12. Detection windows are loose
# because we sample at 15Hz and pads can chain.
BIG_PAD_DELTA = 90
BIG_PAD_PREV_MAX = 12
SMALL_PAD_MIN_DELTA = 5

# Fast-aerial detection: a takeoff with boost ≥30 that reaches Z > 600 within
# 1.5 real seconds. Z=600 is roughly above the second crossbar height.
FAST_AERIAL_BOOST_MIN = 30
FAST_AERIAL_Z_TARGET = 600.0
FAST_AERIAL_WINDOW_S = 1.5

LAST_TOUCH_NONE = "none"
LAST_TOUCH_SELF = "self"
LAST_TOUCH_TEAM = "team"
LAST_TOUCH_OPPONENT = "opp"

# Allowlist of StatfeedEvent names → MatchAggregator attribute.
# Keys are lowercase for case-insensitive lookup.
_STATFEED_EVENTS: dict[str, str] = {
    "epicsave": "epic_saves",
    "hattrick": "hat_tricks",
    "aerialgoal": "aerial_goals",
    "bicyclegoal": "bicycle_goals",
    "longgoal": "long_goals",
    "center": "centers",
    "centeringball": "centers",
    "poolshot": "pool_shots",
    "saviour": "saviors",
    "savior": "saviors",
    "mvp": "mvps",
}


def _norm_y(y: float, team: int) -> float:
    """Normalize Y so that <0 is always the player's defensive half.

    Blue (team=0) defends the -Y side, Orange (team=1) defends the +Y side.
    """
    return float(y) if team == 0 else -float(y)


@dataclass
class MatchAggregator:
    me_id: str | None = None
    me_name: str | None = None
    match_guid: str | None = None
    started_at: datetime | None = None
    started_monotonic: float | None = None

    # Direct snapshot from latest UpdateState
    score: int = 0
    goals: int = 0
    shots: int = 0
    saves: int = 0
    assists: int = 0
    demos: int = 0
    touches: int = 0
    boost: int = 0
    speed: float = 0.0

    # Derived counters
    boost_sum: int = 0
    frames: int = 0
    frames_zero_boost: int = 0
    frames_full_boost: int = 0
    frames_supersonic: int = 0
    frames_airborne: int = 0
    demos_taken: int = 0
    prev_has_car: bool = True
    hardest_hit: float = 0.0
    ball_hits: int = 0
    own_hit_power_sum: float = 0.0
    last_touch_kind: str = LAST_TOUCH_NONE
    last_touch_name: str = ""

    # Possession: total ball-hit events per team
    team_hits: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})

    # Positioning (sampled when both player and ball expose Location).
    # frames_pos may be < frames if the build doesn't broadcast Location.
    frames_pos: int = 0
    frames_def_third: int = 0
    frames_off_third: int = 0
    frames_behind_ball: int = 0
    frames_last_back: int = 0
    dist_to_ball_sum: float = 0.0

    # Boost pickup detection
    big_pads: int = 0
    small_pads: int = 0
    boost_stolen: int = 0
    _prev_boost: int | None = field(default=None, repr=False)

    # Aerial mechanics
    aerial_touches: int = 0
    fast_aerials: int = 0
    _last_on_ground: bool = field(default=True, repr=False)
    _takeoff_at: float | None = field(default=None, repr=False)
    _takeoff_boost: int = field(default=0, repr=False)
    _fast_aerial_counted: bool = field(default=False, repr=False)

    # Statfeed highlights
    epic_saves: int = 0
    hat_tricks: int = 0
    aerial_goals: int = 0
    bicycle_goals: int = 0
    long_goals: int = 0
    centers: int = 0
    pool_shots: int = 0
    saviors: int = 0
    mvps: int = 0

    # Match context
    blue_score: int = 0
    orange_score: int = 0
    me_team: int = -1
    in_overtime: bool = False
    in_replay: bool = False
    arena: str = ""
    clock: int = 0
    team_size: int | None = None

    # Identity-detection state — track Target during the first frames after Initialized
    _detect_window_frames: int = field(default=0, repr=False)
    _detect_target_counts: dict[str, int] = field(default_factory=dict, repr=False)

    def reset_match(self, match_guid: str) -> None:
        keep = (self.me_id, self.me_name)
        self.__init__()  # type: ignore[misc]
        self.me_id, self.me_name = keep
        self.match_guid = match_guid
        self.started_at = datetime.now(timezone.utc)
        self.started_monotonic = time.monotonic()

    def on_initialized(self, data: dict) -> None:
        guid = data.get("MatchGuid", "unknown")
        self.reset_match(guid)
        # Open the detection window for the first 75 frames (~5s @ 15Hz throttled)
        self._detect_window_frames = 75
        self._detect_target_counts = {}

    def is_match_guid_change(self, data: dict) -> bool:
        """Return True if this UpdateState belongs to a different match than the current one."""
        guid = data.get("MatchGuid")
        return bool(guid) and self.match_guid is not None and self.match_guid != guid

    def on_update_state(self, data: dict) -> dict | None:
        """Update state from an UpdateState event. Returns identity if detected this frame."""
        game = data.get("Game") or {}
        players = data.get("Players") or []

        teams = game.get("Teams") or []
        for t in teams:
            if t.get("TeamNum") == 0:
                self.blue_score = t.get("Score", 0)
            elif t.get("TeamNum") == 1:
                self.orange_score = t.get("Score", 0)

        self.in_overtime = bool(game.get("bOvertime"))
        self.in_replay = bool(game.get("bReplay"))
        self.arena = game.get("Arena", self.arena)
        self.clock = int(game.get("TimeSeconds", self.clock))
        team_size = game.get("TeamSize")
        if isinstance(team_size, int) and team_size > 0:
            self.team_size = team_size

        # Init match guid if first frame is UpdateState (no Initialized seen).
        # NOTE: caller is responsible for persisting the prior match snapshot
        # before this method is invoked when is_match_guid_change() returns True.
        guid = data.get("MatchGuid")
        if guid and self.match_guid != guid:
            self.reset_match(guid)
            self._detect_window_frames = 75
            self._detect_target_counts = {}

        detected = self._maybe_detect(players, game)

        me = self._find_me(players)
        if me is None:
            return detected

        self.me_team = me.get("TeamNum", self.me_team)

        self.score = me.get("Score", self.score)
        self.goals = me.get("Goals", self.goals)
        self.shots = me.get("Shots", self.shots)
        self.saves = me.get("Saves", self.saves)
        self.assists = me.get("Assists", self.assists)
        self.demos = me.get("Demos", self.demos)
        self.touches = me.get("Touches", self.touches)
        self.boost = me.get("Boost", self.boost)
        self.speed = float(me.get("Speed", self.speed))

        on_ground = me.get("bOnGround")
        if isinstance(on_ground, bool):
            self._last_on_ground = on_ground

        # Derived — only count "live" frames (not replays, has car spawned at least once)
        if not self.in_replay:
            self.frames += 1
            boost_now = int(me.get("Boost", 0))
            self.boost_sum += boost_now
            if boost_now == 0:
                self.frames_zero_boost += 1
            if boost_now >= 100:
                self.frames_full_boost += 1

            if self.speed >= SUPERSONIC_THRESHOLD:
                self.frames_supersonic += 1

            if on_ground is False:
                self.frames_airborne += 1

            prev_has_car = self.prev_has_car
            has_car = bool(me.get("bHasCar", True))
            if prev_has_car and not has_car:
                self.demos_taken += 1
            self.prev_has_car = has_car

            # Skip pickup detection on death/respawn frames — boost jumps from
            # an arbitrary value to 0 (death) and from 0 to 33 (respawn);
            # neither is a real pad pickup.
            if has_car and prev_has_car:
                self._track_boost_pickup(me, boost_now)
            else:
                self._prev_boost = boost_now
            self._track_position(me, game, players)
            self._track_aerial(me, on_ground)

        return detected

    def on_ball_hit(self, data: dict) -> None:
        ball = data.get("Ball") or {}
        post = float(ball.get("PostHitSpeed", 0.0))
        hitters = data.get("Players") or []
        names = [p.get("Name") for p in hitters]
        teams = {p.get("Name"): p.get("TeamNum") for p in hitters}

        # Possession tracker — count every BallHit by team, regardless of identity
        for hitter in hitters:
            t = hitter.get("TeamNum")
            if t in (0, 1):
                self.team_hits[t] = self.team_hits.get(t, 0) + 1

        if not self.me_name:
            return

        if self.me_name in names:
            self.ball_hits += 1
            self.own_hit_power_sum += post
            self.last_touch_kind = LAST_TOUCH_SELF
            self.last_touch_name = self.me_name
            if post > self.hardest_hit:
                self.hardest_hit = post
            # Aerial touch: I last left the ground in the most recent UpdateState
            if self._last_on_ground is False:
                self.aerial_touches += 1
        else:
            hitter_name = names[0] if names else ""
            hitter_team = teams.get(hitter_name)
            if hitter_team is None or self.me_team < 0:
                self.last_touch_kind = LAST_TOUCH_NONE
            elif hitter_team == self.me_team:
                self.last_touch_kind = LAST_TOUCH_TEAM
            else:
                self.last_touch_kind = LAST_TOUCH_OPPONENT
            self.last_touch_name = hitter_name

    def on_statfeed_event(self, data: dict) -> None:
        """Increment a highlight counter when the user is the causer.

        Drops the event silently if: in replay, no match active, no causer,
        causer is not the user, or event name is not in the allowlist
        (logged at DEBUG for future allowlist expansion).
        """
        if self.in_replay:
            return
        if self.match_guid is None:
            return

        event_name = data.get("Event")
        if not event_name:
            return

        causer = data.get("Causer") or {}
        causer_id = causer.get("PrimaryId")
        causer_name = causer.get("Name")

        if not causer_id and not causer_name:
            return

        if not self._is_me(causer_id, causer_name):
            return

        attr = _STATFEED_EVENTS.get(event_name.lower())
        if attr is None:
            log.debug("statfeed event not in allowlist: %s (causer=%s)", event_name, causer_name)
            return

        setattr(self, attr, getattr(self, attr) + 1)

    def _is_me(self, causer_id: str | None, causer_name: str | None) -> bool:
        """Return True if causer matches the identified user (id preferred, name fallback)."""
        if self.me_id and causer_id == self.me_id:
            return True
        if self.me_name and causer_name == self.me_name:
            return True
        return False

    def _track_boost_pickup(self, me: dict, boost_now: int) -> None:
        prev = self._prev_boost
        self._prev_boost = boost_now
        if prev is None:
            return
        delta = boost_now - prev
        if delta >= BIG_PAD_DELTA and prev <= BIG_PAD_PREV_MAX:
            self.big_pads += 1
            if self._is_in_opponent_half(me):
                self.boost_stolen += 1
        elif SMALL_PAD_MIN_DELTA <= delta < BIG_PAD_DELTA:
            self.small_pads += 1
            if self._is_in_opponent_half(me):
                self.boost_stolen += 1

    def _is_in_opponent_half(self, me: dict) -> bool:
        loc = me.get("Location")
        if not isinstance(loc, dict) or self.me_team < 0:
            return False
        y = loc.get("Y")
        if y is None:
            return False
        return _norm_y(y, self.me_team) > 0

    def _track_position(self, me: dict, game: dict, players: list[dict]) -> None:
        me_loc = me.get("Location")
        ball = game.get("Ball") or {}
        ball_loc = ball.get("Location")
        if (
            not isinstance(me_loc, dict)
            or not isinstance(ball_loc, dict)
            or self.me_team < 0
            or me_loc.get("Y") is None
            or ball_loc.get("Y") is None
        ):
            return

        my_y = _norm_y(me_loc["Y"], self.me_team)
        ball_y = _norm_y(ball_loc["Y"], self.me_team)

        self.frames_pos += 1
        if my_y < DEFENSIVE_Y:
            self.frames_def_third += 1
        elif my_y > OFFENSIVE_Y:
            self.frames_off_third += 1

        if my_y < ball_y:
            self.frames_behind_ball += 1

        # 3D distance to ball — magnitude is sign-invariant, so we can reuse
        # the already-normalized Y values without affecting the result.
        dx = float(me_loc.get("X", 0.0)) - float(ball_loc.get("X", 0.0))
        dy = my_y - ball_y
        dz = float(me_loc.get("Z", 0.0)) - float(ball_loc.get("Z", 0.0))
        self.dist_to_ball_sum += (dx * dx + dy * dy + dz * dz) ** 0.5

        # Last back: my normalized Y is the smallest among my own team
        teammate_ys = [
            _norm_y(p["Location"]["Y"], self.me_team)
            for p in players
            if isinstance(p.get("Location"), dict)
            and p.get("Location", {}).get("Y") is not None
            and p.get("TeamNum") == self.me_team
        ]
        if teammate_ys and my_y <= min(teammate_ys):
            self.frames_last_back += 1

    def _track_aerial(self, me: dict, on_ground: bool | None) -> None:
        if on_ground is None:
            return
        now = time.monotonic()
        boost_now = int(me.get("Boost", 0))
        loc = me.get("Location")
        z = float(loc["Z"]) if isinstance(loc, dict) and loc.get("Z") is not None else None

        # Detect takeoff
        if self.prev_has_car and on_ground is False and self._takeoff_at is None:
            self._takeoff_at = now
            self._takeoff_boost = boost_now
            self._fast_aerial_counted = False

        # Reset on landing
        if on_ground is True:
            self._takeoff_at = None
            self._fast_aerial_counted = False
            return

        if (
            self._takeoff_at is not None
            and not self._fast_aerial_counted
            and self._takeoff_boost >= FAST_AERIAL_BOOST_MIN
            and z is not None
            and z > FAST_AERIAL_Z_TARGET
            and (now - self._takeoff_at) <= FAST_AERIAL_WINDOW_S
        ):
            self.fast_aerials += 1
            self._fast_aerial_counted = True

    def _maybe_detect(self, players: list[dict], game: dict) -> dict | None:
        if self.me_id or self._detect_window_frames <= 0:
            return None
        self._detect_window_frames -= 1
        if not game.get("bHasTarget"):
            return None
        target = game.get("Target") or {}
        target_name = target.get("Name")
        if not target_name:
            return None
        self._detect_target_counts[target_name] = (
            self._detect_target_counts.get(target_name, 0) + 1
        )
        # Lock identity once a target name has been seen for >= 30 frames (~2s at 15Hz)
        for name, count in self._detect_target_counts.items():
            if count >= 30:
                resolved = next((p for p in players if p.get("Name") == name), None)
                if resolved and resolved.get("PrimaryId"):
                    self.me_id = resolved["PrimaryId"]
                    self.me_name = name
                    self._detect_window_frames = 0
                    return {"id": self.me_id, "name": self.me_name}
        return None

    def _find_me(self, players: list[dict]) -> dict | None:
        if self.me_id:
            for p in players:
                if p.get("PrimaryId") == self.me_id:
                    return p
            # Fall back to name match if id not found this frame
        if self.me_name:
            for p in players:
                if p.get("Name") == self.me_name:
                    return p
        return None

    def shot_accuracy_pct(self) -> int:
        return round(100 * self.goals / self.shots) if self.shots > 0 else 0

    def boost_avg(self) -> int:
        return round(self.boost_sum / self.frames) if self.frames > 0 else 0

    def time_zero_boost_pct(self) -> int:
        return round(100 * self.frames_zero_boost / self.frames) if self.frames > 0 else 0

    def time_supersonic_pct(self) -> int:
        return round(100 * self.frames_supersonic / self.frames) if self.frames > 0 else 0

    def time_airborne_pct(self) -> int:
        return round(100 * self.frames_airborne / self.frames) if self.frames > 0 else 0

    def boost_wasted_pct(self) -> int:
        return round(100 * self.frames_full_boost / self.frames) if self.frames > 0 else 0

    def avg_shot_power(self) -> float:
        return round(self.own_hit_power_sum / self.ball_hits, 1) if self.ball_hits > 0 else 0.0

    def score_per_minute(self) -> int:
        # Use real elapsed time since match started rather than the in-game clock —
        # the clock counts down from 300 in regular play but jumps to 0 in overtime,
        # and a spectator joining mid-match would inherit a misleading "elapsed".
        if self.started_monotonic is None or self.frames == 0:
            return 0
        elapsed_min = (time.monotonic() - self.started_monotonic) / 60
        if elapsed_min < 0.1:
            return 0
        return round(self.score / elapsed_min)

    def goal_participation_pct(self) -> int:
        if self.me_team < 0:
            return 0
        team_goals = self.blue_score if self.me_team == 0 else self.orange_score
        if team_goals <= 0:
            return 0
        return round(100 * (self.goals + self.assists) / team_goals)

    def possession_pct(self) -> int:
        if self.me_team < 0:
            return 0
        my_team_hits = self.team_hits.get(self.me_team, 0)
        total = self.team_hits.get(0, 0) + self.team_hits.get(1, 0)
        return round(100 * my_team_hits / total) if total > 0 else 0

    def time_def_third_pct(self) -> int:
        return round(100 * self.frames_def_third / self.frames_pos) if self.frames_pos > 0 else 0

    def time_off_third_pct(self) -> int:
        return round(100 * self.frames_off_third / self.frames_pos) if self.frames_pos > 0 else 0

    def time_mid_third_pct(self) -> int:
        if self.frames_pos == 0:
            return 0
        return max(0, 100 - self.time_def_third_pct() - self.time_off_third_pct())

    def behind_ball_pct(self) -> int:
        return round(100 * self.frames_behind_ball / self.frames_pos) if self.frames_pos > 0 else 0

    def last_back_pct(self) -> int:
        return round(100 * self.frames_last_back / self.frames_pos) if self.frames_pos > 0 else 0

    def dist_to_ball_avg(self) -> int:
        return round(self.dist_to_ball_sum / self.frames_pos) if self.frames_pos > 0 else 0

    def air_touch_pct(self) -> int:
        return round(100 * self.aerial_touches / self.ball_hits) if self.ball_hits > 0 else 0

    def has_positioning_data(self) -> bool:
        return self.frames_pos > 0

    def won(self) -> bool | None:
        if self.me_team < 0:
            return None
        my_score = self.blue_score if self.me_team == 0 else self.orange_score
        opp_score = self.orange_score if self.me_team == 0 else self.blue_score
        if my_score == opp_score:
            return None
        return my_score > opp_score

    def to_overlay_dict(self) -> dict:
        return {
            "me": {"id": self.me_id, "name": self.me_name, "team": self.me_team},
            "context": {
                "blue": self.blue_score,
                "orange": self.orange_score,
                "clock": self.clock,
                "overtime": self.in_overtime,
                "replay": self.in_replay,
                "arena": self.arena,
            },
            "match": {
                "score": self.score,
                "goals": self.goals,
                "shots": self.shots,
                "shot_accuracy": self.shot_accuracy_pct(),
                "saves": self.saves,
                "assists": self.assists,
                "touches": self.touches,
                "demos_given": self.demos,
                "demos_taken": self.demos_taken,
                "boost": self.boost,
                "boost_avg": self.boost_avg(),
                "time_zero_boost_pct": self.time_zero_boost_pct(),
                "time_supersonic_pct": self.time_supersonic_pct(),
                "time_airborne_pct": self.time_airborne_pct(),
                "hardest_hit": round(self.hardest_hit, 1),
                "last_touch": self.last_touch_kind,
                "last_touch_name": self.last_touch_name,
                "speed": round(self.speed, 1),
                "ball_hits": self.ball_hits,
                "avg_shot_power": self.avg_shot_power(),
                "score_per_min": self.score_per_minute(),
                "goal_participation_pct": self.goal_participation_pct(),
                "possession_pct": self.possession_pct(),
                "boost_wasted_pct": self.boost_wasted_pct(),
            },
        }

    def to_db_snapshot(self) -> dict:
        return {
            "match_guid": self.match_guid,
            "player_id": self.me_id,
            "player_name": self.me_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "blue_score": self.blue_score,
            "orange_score": self.orange_score,
            "me_team": self.me_team,
            "team_size": self.team_size,
            "won": self.won(),
            "score": self.score,
            "goals": self.goals,
            "shots": self.shots,
            "saves": self.saves,
            "assists": self.assists,
            "demos": self.demos,
            "demos_taken": self.demos_taken,
            "touches": self.touches,
            "boost_avg": self.boost_avg(),
            "time_zero_boost_pct": self.time_zero_boost_pct(),
            "time_supersonic_pct": self.time_supersonic_pct(),
            "time_airborne_pct": self.time_airborne_pct(),
            "hardest_hit": round(self.hardest_hit, 1),
            "ball_hits": self.ball_hits,
            "avg_shot_power": self.avg_shot_power(),
            "boost_wasted_pct": self.boost_wasted_pct(),
            "possession_pct": self.possession_pct(),
            "score_per_min": self.score_per_minute(),
            "time_def_third_pct": self.time_def_third_pct() if self.has_positioning_data() else None,
            "time_off_third_pct": self.time_off_third_pct() if self.has_positioning_data() else None,
            "behind_ball_pct": self.behind_ball_pct() if self.has_positioning_data() else None,
            "last_back_pct": self.last_back_pct() if self.has_positioning_data() else None,
            "dist_to_ball_avg": self.dist_to_ball_avg() if self.has_positioning_data() else None,
            "big_pads": self.big_pads,
            "small_pads": self.small_pads,
            "boost_stolen": self.boost_stolen,
            "aerial_touches": self.aerial_touches,
            "fast_aerials": self.fast_aerials,
            "epic_saves": self.epic_saves,
            "hat_tricks": self.hat_tricks,
            "aerial_goals": self.aerial_goals,
            "bicycle_goals": self.bicycle_goals,
            "long_goals": self.long_goals,
            "centers": self.centers,
            "pool_shots": self.pool_shots,
            "saviors": self.saviors,
            "mvps": self.mvps,
        }
