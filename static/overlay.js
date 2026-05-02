(() => {
  const status = document.getElementById("status");
  const overlay = document.getElementById("overlay");
  const banner = document.getElementById("banner");

  const $ = (id) => document.getElementById(id);

  const setStatus = (text) => {
    status.hidden = false;
    overlay.hidden = true;
    status.textContent = text;
  };

  const showOverlay = () => {
    status.hidden = true;
    overlay.hidden = false;
  };

  const renderMatch = (snap) => {
    showOverlay();
    const ctx = snap.context || {};
    const m = snap.match || {};
    const me = snap.me || {};

    $("ctx-blue-score").textContent = ctx.blue ?? 0;
    $("ctx-orange-score").textContent = ctx.orange ?? 0;
    const sec = ctx.clock ?? 0;
    $("ctx-clock").textContent =
      `${Math.floor(sec / 60)}:${(sec % 60).toString().padStart(2, "0")}`;

    if (ctx.replay) {
      banner.hidden = false;
      banner.className = "banner replay";
      banner.textContent = "GOAL REPLAY";
    } else if (ctx.overtime) {
      banner.hidden = false;
      banner.className = "banner overtime";
      banner.textContent = "OVERTIME";
    } else {
      banner.hidden = true;
    }

    $("me-name").textContent = me.name ?? "detecting…";
    const teamLabel = me.team === 0 ? "blue team" : me.team === 1 ? "orange team" : "—";
    $("me-team").textContent = teamLabel;
    $("me-team").style.color = me.team === 0 ? "#1873ff" : me.team === 1 ? "#ff8a1f" : "";

    $("m-score").textContent = m.score ?? 0;

    $("m-goals").textContent = m.goals ?? 0;
    $("m-shots").textContent = m.shots ?? 0;
    $("m-acc").textContent = `(${m.shot_accuracy ?? 0}%)`;
    $("m-saves").textContent = m.saves ?? 0;
    $("m-assists").textContent = m.assists ?? 0;
    $("m-touches").textContent = m.touches ?? 0;
    $("m-ball-hits").textContent = m.ball_hits ?? 0;
    $("m-demos").textContent = m.demos_given ?? 0;
    $("m-demos-taken").textContent = m.demos_taken ?? 0;
    $("m-boost-avg").textContent = m.boost_avg ?? 0;
    $("m-boost-wasted").textContent = `${m.boost_wasted_pct ?? 0}%`;
    $("m-zero-boost").textContent = `${m.time_zero_boost_pct ?? 0}%`;
    $("m-supersonic").textContent = `${m.time_supersonic_pct ?? 0}%`;
    $("m-airborne").textContent = `${m.time_airborne_pct ?? 0}%`;
    $("m-possession").textContent = `${m.possession_pct ?? 0}%`;
    $("m-score-min").textContent = m.score_per_min ?? 0;
    $("m-goal-part").textContent = `${m.goal_participation_pct ?? 0}%`;
    $("m-hardest").textContent = m.hardest_hit ?? 0;
    $("m-avg-shot-pwr").textContent = m.avg_shot_power ?? 0;

    const ALLOWED_TOUCH = { self: "you", team: "teammate", opp: "opponent", none: null };
    const rawTouch = m.last_touch;
    const touchKey = Object.prototype.hasOwnProperty.call(ALLOWED_TOUCH, rawTouch) ? rawTouch : "none";
    const last = $("m-last-touch");
    last.className = touchKey === "none" ? "" : touchKey;
    if (touchKey === "none") {
      last.textContent = "—";
    } else {
      const who = m.last_touch_name ? ` (${m.last_touch_name})` : "";
      last.textContent = `${ALLOWED_TOUCH[touchKey]}${who}`;
    }
  };

  const renderToday = (t) => {
    $("t-wins").textContent = t.wins ?? 0;
    $("t-losses").textContent = t.losses ?? 0;
    $("t-winrate").textContent = t.win_rate ?? 0;
    $("t-matches").textContent = t.matches ?? 0;
    $("t-goals").textContent = t.goals ?? 0;
    $("t-shots").textContent = t.shots ?? 0;
    $("t-acc").textContent = `(${t.shot_accuracy ?? 0}%)`;
    $("t-saves").textContent = t.saves ?? 0;
    $("t-assists").textContent = t.assists ?? 0;
    $("t-demos").textContent = t.demos ?? 0;
    $("t-avg-score").textContent = t.avg_score ?? 0;
    $("t-best-score").textContent = t.best_score ?? 0;
    $("t-avg-boost").textContent = t.avg_boost ?? 0;
    $("t-avg-super").textContent = `${t.avg_supersonic_pct ?? 0}%`;
    $("t-ball-hits").textContent = t.total_ball_hits ?? 0;
    $("t-best-hit").textContent = t.best_hit ?? 0;
    $("t-streak").textContent = t.win_streak ?? 0;
  };

  let socket;
  let retryDelay = 500;
  const connect = () => {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    socket = new WebSocket(url);
    socket.onopen = () => {
      retryDelay = 500;
      setStatus("connected — waiting for match…");
    };
    socket.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch (err) {
        console.warn("overlay: dropped malformed WS message", err);
        return;
      }
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "match" && msg.data) renderMatch(msg.data);
      else if (msg.type === "today" && msg.data) renderToday(msg.data);
    };
    socket.onclose = () => {
      setStatus("disconnected — retrying…");
      setTimeout(connect, retryDelay);
      // Cap at 30s so an overlay left running through a long server outage
      // doesn't keep hammering once a second after the cap.
      retryDelay = Math.min(retryDelay * 2, 30000);
    };
    socket.onerror = () => socket.close();
  };

  connect();
})();
