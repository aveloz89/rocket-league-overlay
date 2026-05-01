"""Per-match aggregator: tracks the 15 personal stats + identity detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# RL "Speed" in the stats API is uu/s / 100. Supersonic in-game = 2200 uu/s.
SUPERSONIC_THRESHOLD = 22.0

LAST_TOUCH_NONE = "none"
LAST_TOUCH_SELF = "self"
LAST_TOUCH_TEAM = "team"
LAST_TOUCH_OPPONENT = "opp"


@dataclass
class MatchAggregator:
    me_id: str | None = None
    me_name: str | None = None
    match_guid: str | None = None
    started_at: datetime | None = None

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

    # Match context
    blue_score: int = 0
    orange_score: int = 0
    me_team: int = -1
    in_overtime: bool = False
    in_replay: bool = False
    arena: str = ""
    clock: int = 0

    # Identity-detection state — track Target during the first frames after Initialized
    _detect_window_frames: int = field(default=0, repr=False)
    _detect_target_counts: dict[str, int] = field(default_factory=dict, repr=False)

    def reset_match(self, match_guid: str) -> None:
        keep = (self.me_id, self.me_name)
        self.__init__()  # type: ignore[misc]
        self.me_id, self.me_name = keep
        self.match_guid = match_guid
        self.started_at = datetime.now(timezone.utc)

    def on_initialized(self, data: dict) -> None:
        guid = data.get("MatchGuid", "unknown")
        self.reset_match(guid)
        # Open the detection window for the first 75 frames (~5s @ 15Hz throttled)
        self._detect_window_frames = 75
        self._detect_target_counts = {}

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

        # Init match guid if first frame is UpdateState (no Initialized seen)
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

            if me.get("bOnGround") is False:
                self.frames_airborne += 1

            has_car = bool(me.get("bHasCar", True))
            if self.prev_has_car and not has_car:
                self.demos_taken += 1
            self.prev_has_car = has_car

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
        # Match clock counts down from 300; elapsed = 300 - clock once a real frame has been seen
        elapsed_min = max(0.5, (300 - self.clock) / 60)
        return round(self.score / elapsed_min) if self.frames > 0 else 0

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
        }
