(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const conn = $("conn");
  const empty = $("empty");
  const live = $("live");
  const hero = $("hero");
  const cats = $("cats");
  const trendSection = $("trend-section");
  const todayDetail = $("today-detail");
  const banner = $("banner");
  const headerScoreline = $("header-scoreline");

  const setConn = (state, label) => {
    conn.dataset.state = state;
    conn.textContent = label;
  };

  // Match snapshots stop arriving when the user is in a menu / queue. Mark the
  // header as "in match" while they're flowing and fall back to "waiting for
  // match" if none arrive within MATCH_ACTIVE_TIMEOUT_MS — comfortably above
  // the 5 s server-side throttle so a normal stream keeps the indicator green.
  const MATCH_ACTIVE_TIMEOUT_MS = 12000;
  let matchActiveTimer = null;
  const markMatchActive = () => {
    setConn("active", "live · in match");
    if (matchActiveTimer !== null) clearTimeout(matchActiveTimer);
    matchActiveTimer = setTimeout(() => {
      setConn("connected", "live · waiting for match");
      matchActiveTimer = null;
    }, MATCH_ACTIVE_TIMEOUT_MS);
  };
  const clearMatchActive = () => {
    if (matchActiveTimer !== null) {
      clearTimeout(matchActiveTimer);
      matchActiveTimer = null;
    }
  };

  // ── Live match render ────────────────────────────────────────────

  const ALLOWED_TOUCH = { self: "you", team: "teammate", opp: "opponent", none: null };

  const renderMatch = (snap) => {
    live.hidden = false;
    headerScoreline.hidden = false;
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
    $("m-boost").textContent = m.boost ?? 0;
    $("m-speed").textContent = m.speed ?? 0;
    $("m-goals").textContent = m.goals ?? 0;
    $("m-shots").textContent = m.shots ?? 0;
    $("m-acc").textContent = `(${m.shot_accuracy ?? 0}%)`;
    $("m-saves").textContent = m.saves ?? 0;
    $("m-assists").textContent = m.assists ?? 0;
    $("m-touches").textContent = m.touches ?? 0;
    $("m-ball-hits").textContent = m.ball_hits ?? 0;
    $("m-demos").textContent = m.demos_given ?? 0;
    $("m-demos-taken").textContent = m.demos_taken ?? 0;
    $("m-possession").textContent = `${m.possession_pct ?? 0}%`;
    $("m-score-min").textContent = m.score_per_min ?? 0;
    $("m-goal-part").textContent = `${m.goal_participation_pct ?? 0}%`;
    $("m-hardest").textContent = m.hardest_hit ?? 0;
    $("m-avg-shot-pwr").textContent = m.avg_shot_power ?? 0;
    $("m-boost-avg").textContent = m.boost_avg ?? 0;
    $("m-boost-wasted").textContent = `${m.boost_wasted_pct ?? 0}%`;
    $("m-zero-boost").textContent = `${m.time_zero_boost_pct ?? 0}%`;
    $("m-supersonic").textContent = `${m.time_supersonic_pct ?? 0}%`;
    $("m-airborne").textContent = `${m.time_airborne_pct ?? 0}%`;

    const rawTouch = m.last_touch;
    const touchKey = Object.prototype.hasOwnProperty.call(ALLOWED_TOUCH, rawTouch) ? rawTouch : "none";
    const lastEl = $("m-last-touch");
    lastEl.className = touchKey === "none" ? "" : touchKey;
    if (touchKey === "none") {
      lastEl.textContent = "—";
    } else {
      const who = m.last_touch_name ? ` (${m.last_touch_name})` : "";
      lastEl.textContent = `${ALLOWED_TOUCH[touchKey]}${who}`;
    }
  };

  // ── Today (detailed) render ──────────────────────────────────────

  const renderToday = (t) => {
    if (!t || !t.matches) {
      todayDetail.hidden = true;
      return;
    }
    todayDetail.hidden = false;
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
    $("t-ball-hits").textContent = t.total_ball_hits ?? 0;
    $("t-best-hit").textContent = t.best_hit ?? 0;
    $("t-streak").textContent = t.win_streak ?? 0;
  };

  // ── Coach render (last match + insights + cats + trend) ─────────

  const POSITIONING_ROWS = [
    { key: "time_def_third_pct", label: "Defensive third", suffix: "%", direction: "neutral" },
    { key: "time_off_third_pct", label: "Offensive third", suffix: "%", direction: "neutral" },
    { key: "behind_ball_pct", label: "Behind ball", suffix: "%", direction: "high_good" },
    { key: "last_back_pct", label: "Last back", suffix: "%", direction: "neutral" },
    { key: "dist_to_ball_avg", label: "Ball distance", suffix: "", direction: "neutral" },
  ];

  const BOOST_ROWS = [
    { key: "big_pads", label: "Big pads", suffix: "", direction: "high_good" },
    { key: "small_pads", label: "Small pads", suffix: "", direction: "high_good" },
    { key: "boost_stolen", label: "Stolen in opp half", suffix: "", direction: "high_good" },
    { key: "boost_avg", label: "Avg boost", suffix: "", direction: "high_good" },
    { key: "boost_wasted_pct", label: "Wasted", suffix: "%", direction: "low_good" },
    { key: "time_zero_boost_pct", label: "Zero boost", suffix: "%", direction: "low_good" },
  ];

  const MECH_ROWS = [
    { key: "aerial_touches", label: "Aerial touches", suffix: "", direction: "high_good" },
    { key: "air_touch_pct", label: "Aerial touch %", suffix: "%", direction: "high_good" },
    { key: "fast_aerials", label: "Fast aerials", suffix: "", direction: "high_good" },
    { key: "time_supersonic_pct", label: "Supersonic", suffix: "%", direction: "high_good" },
    { key: "time_airborne_pct", label: "Airborne", suffix: "%", direction: "high_good" },
    { key: "hardest_hit", label: "Hardest hit", suffix: "", direction: "high_good" },
  ];

  const HIGHLIGHT_ROWS = [
    { key: "epic_saves",    label: "Epic saves",   suffix: "", direction: "high_good" },
    { key: "saviors",       label: "Saviors",      suffix: "", direction: "high_good" },
    { key: "aerial_goals",  label: "Aerial goals", suffix: "", direction: "high_good" },
    { key: "bicycle_goals", label: "Bicycle goals", suffix: "", direction: "high_good" },
    { key: "long_goals",    label: "Long goals",   suffix: "", direction: "high_good" },
    { key: "centers",       label: "Centers",      suffix: "", direction: "high_good" },
    { key: "pool_shots",    label: "Pool shots",   suffix: "", direction: "high_good" },
    { key: "hat_tricks",    label: "Hat tricks",   suffix: "", direction: "high_good" },
    { key: "mvps",          label: "MVPs",         suffix: "", direction: "high_good" },
  ];

  const TREND_METRICS = [
    { key: "win_rate", label: "Win rate", suffix: "%", direction: "high_good" },
    { key: "score_per_min", label: "Score/min", suffix: "", direction: "high_good" },
    { key: "shot_accuracy", label: "Shot accuracy", suffix: "%", direction: "high_good" },
    { key: "behind_ball_pct", label: "Behind ball", suffix: "%", direction: "high_good" },
    { key: "possession_pct", label: "Possession", suffix: "%", direction: "high_good" },
  ];

  const fmt = (val, suffix) => {
    if (val === null || val === undefined) return "—";
    const n = typeof val === "number" ? val : Number(val);
    if (Number.isNaN(n)) return "—";
    const rounded = Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10;
    return `${rounded}${suffix ?? ""}`;
  };

  const trendFromDiff = (diff, tolerance, direction) => {
    if (Math.abs(diff) <= tolerance || direction === "neutral") return "flat";
    if ((direction === "high_good" && diff > 0) || (direction === "low_good" && diff < 0)) return "up";
    return "down";
  };

  const buildCompareSpan = (def, v, a, className, prefix) => {
    const span = document.createElement("span");
    span.className = className;
    if (a === null || a === undefined || v === null || v === undefined) {
      span.textContent = "";
      span.dataset.trend = "flat";
      return span;
    }
    const diff = Number(v) - Number(a);
    span.textContent = `${prefix} ${fmt(a, def.suffix)}`;
    const tolerance = def.suffix === "%" ? 3 : Math.max(1, Math.abs(Number(a)) * 0.05);
    span.dataset.trend = trendFromDiff(diff, tolerance, def.direction);
    return span;
  };

  const buildRow = (def, last, avg, benchmark) => {
    const li = document.createElement("li");
    li.className = "row";

    const label = document.createElement("span");
    label.className = "label";
    label.textContent = def.label;

    const value = document.createElement("span");
    value.className = "value";
    const v = last?.[def.key];
    value.textContent = v === null || v === undefined ? "—" : fmt(v, def.suffix);

    const compare = buildCompareSpan(def, v, avg?.[def.key], "compare", "avg");

    const bVal = benchmark?.[def.key] ?? null;
    const benchmarkSpan = buildCompareSpan(def, v, bVal, "benchmark", "rk");

    li.append(label, value, compare, benchmarkSpan);
    return li;
  };

  const renderRows = (containerId, defs, last, avg, benchmark) => {
    const container = $(containerId);
    container.replaceChildren();
    for (const def of defs) {
      container.appendChild(buildRow(def, last, avg, benchmark));
    }
  };

  const renderInsights = (insights) => {
    const list = $("insights");
    const emptyMsg = $("insights-empty");
    list.replaceChildren();
    if (!insights || insights.length === 0) {
      emptyMsg.hidden = false;
      list.hidden = true;
      return;
    }
    emptyMsg.hidden = true;
    list.hidden = false;
    for (const text of insights) {
      const li = document.createElement("li");
      li.textContent = text;
      list.appendChild(li);
    }
  };

  const buildSparkline = (values) => {
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 100 30");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");

    if (!values || values.length === 0) return svg;

    const filtered = values.filter((v) => v !== null && v !== undefined);
    // A line needs at least two points; sparse series with a single non-null
    // value would otherwise produce a degenerate path that closes onto itself.
    if (filtered.length < 2) return svg;

    const min = Math.min(...filtered);
    const max = Math.max(...filtered);
    // `||` is intentional here: when min === max the range is 0 (falsy) and
    // we want 1 to avoid divide-by-zero. `??` would only fire on null/undefined.
    const range = max - min || 1;
    const stepX = 100 / (values.length - 1);

    const points = values.map((v, i) => {
      if (v === null || v === undefined) return null;
      const x = i * stepX;
      const y = 28 - ((v - min) / range) * 26;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    const linePath = points
      .map((p, i) => (p ? `${i === 0 ? "M" : "L"}${p}` : ""))
      .filter(Boolean)
      .join(" ");

    const firstNonNull = points.findIndex((p) => p !== null);
    const lastNonNull = points.length - 1 - [...points].reverse().findIndex((p) => p !== null);
    const firstX = (firstNonNull * stepX).toFixed(1);
    const lastX = (lastNonNull * stepX).toFixed(1);
    const areaPath = `${linePath} L${lastX},30 L${firstX},30 Z`;

    const area = document.createElementNS(svgNS, "path");
    area.setAttribute("d", areaPath);
    area.setAttribute("class", "area");

    const line = document.createElementNS(svgNS, "path");
    line.setAttribute("d", linePath);

    svg.append(area, line);
    return svg;
  };

  const renderTrend = (trend) => {
    const container = $("trend-rows");
    container.replaceChildren();
    if (!trend || trend.length < 2) {
      trendSection.hidden = true;
      return;
    }
    trendSection.hidden = false;

    for (const metric of TREND_METRICS) {
      const series = trend.map((d) => d[metric.key]);
      const filtered = series.filter((v) => v !== null && v !== undefined);
      if (filtered.length < 2) continue;

      const li = document.createElement("li");
      li.className = "trend-row";

      const label = document.createElement("span");
      label.className = "label";
      label.textContent = metric.label;

      const spark = buildSparkline(series);

      const last = document.createElement("span");
      last.className = "last";
      last.textContent = fmt(filtered[filtered.length - 1], metric.suffix);

      const arrow = document.createElement("span");
      arrow.className = "arrow";
      const first = filtered[0];
      const lastVal = filtered[filtered.length - 1];
      const tolerance = metric.suffix === "%" ? 3 : Math.max(1, Math.abs(first) * 0.05);
      if (Math.abs(lastVal - first) <= tolerance) {
        arrow.textContent = "→";
        arrow.dataset.dir = "flat";
      } else if ((metric.direction === "high_good" && lastVal > first)
                 || (metric.direction === "low_good" && lastVal < first)) {
        arrow.textContent = "↑";
        arrow.dataset.dir = "up";
      } else {
        arrow.textContent = "↓";
        arrow.dataset.dir = "down";
      }

      li.append(label, spark, last, arrow);
      container.appendChild(li);
    }
  };

  const renderCoach = (data) => {
    if (!data || !data.last_match) {
      empty.hidden = false;
      hero.hidden = true;
      cats.hidden = true;
      trendSection.hidden = true;
      return;
    }
    empty.hidden = true;
    hero.hidden = false;
    cats.hidden = false;

    const last = data.last_match;
    const avg = data.rolling_avg ?? {};

    $("player-name").textContent = last.player_name ?? "—";

    const result = $("last-result");
    const myScore = last.me_team === 0 ? last.blue_score : last.orange_score;
    const oppScore = last.me_team === 0 ? last.orange_score : last.blue_score;
    if (last.won === true) {
      result.textContent = `W ${myScore}-${oppScore}`;
      result.dataset.outcome = "win";
    } else if (last.won === false) {
      result.textContent = `L ${myScore}-${oppScore}`;
      result.dataset.outcome = "loss";
    } else {
      result.textContent = `${last.blue_score ?? 0}-${last.orange_score ?? 0}`;
      result.dataset.outcome = "draw";
    }
    $("last-score").textContent = last.score ?? 0;
    $("last-duration").textContent = last.duration_min ?? 0;

    const benchmark = data.rank_benchmark?.stats ?? {};
    const rankSelect = $("rank-select");
    rankSelect.value = data.rank_benchmark?.tier ?? "";

    renderInsights(data.insights);
    renderRows("cat-pos", POSITIONING_ROWS, last, avg, benchmark);
    renderRows("cat-boost", BOOST_ROWS, last, avg, benchmark);
    renderRows("cat-mech", MECH_ROWS, last, avg, benchmark);
    renderRows("cat-highlights", HIGHLIGHT_ROWS, last, avg, benchmark);
    renderTrend(data.trend);
  };

  // ── Mode filter (1v1 / 2v2 / 3v3 / All) ──────────────────────────

  const VALID_MODES = new Set(["1", "2", "3"]);
  let currentMode = "";

  const readModeFromUrl = () => {
    const m = new URL(location.href).searchParams.get("mode");
    return VALID_MODES.has(m) ? m : "";
  };

  const writeModeToUrl = (mode) => {
    const url = new URL(location.href);
    if (mode) url.searchParams.set("mode", mode);
    else url.searchParams.delete("mode");
    history.replaceState(null, "", url);
  };

  const syncModeButtons = () => {
    for (const btn of document.querySelectorAll(".mode-tab")) {
      btn.setAttribute("aria-pressed", btn.dataset.mode === currentMode ? "true" : "false");
    }
  };

  const buildModeQuery = () => (currentMode ? `?mode=${currentMode}` : "");

  // ── Initial fetches (covers refresh with no live game) ───────────

  const fetchCoachAndToday = async () => {
    try {
      const q = buildModeQuery();
      const [coachRes, todayRes] = await Promise.all([
        fetch(`/api/coach${q}`),
        fetch(`/api/today${q}`),
      ]);
      if (coachRes.ok) renderCoach(await coachRes.json());
      if (todayRes.ok) renderToday(await todayRes.json());
    } catch {
      // Network failure on first load — the WS will refill once connected.
    }
  };

  const fetchInitial = fetchCoachAndToday;

  const setMode = (mode) => {
    const normalized = VALID_MODES.has(mode) ? mode : "";
    if (normalized === currentMode) return;
    currentMode = normalized;
    writeModeToUrl(currentMode);
    syncModeButtons();
    fetchCoachAndToday();
  };

  const initModeTabs = () => {
    currentMode = readModeFromUrl();
    syncModeButtons();
    document.getElementById("mode-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".mode-tab");
      if (!btn) return;
      setMode(btn.dataset.mode ?? "");
    });
  };

  // ── WebSocket ────────────────────────────────────────────────────

  let socket;
  let retryDelay = 500;
  const connect = () => {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    socket = new WebSocket(url);
    socket.onopen = () => {
      retryDelay = 500;
      setConn("connected", "live · waiting for match");
    };
    socket.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch {
        return;
      }
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "match" && msg.data) {
        renderMatch(msg.data);
        markMatchActive();
      } else if (msg.type === "today" && msg.data) {
        // When a mode filter is active, the coach broadcast triggers a fresh
        // filtered fetch that also refreshes today — skip the unfiltered push.
        if (!currentMode) renderToday(msg.data);
      } else if (msg.type === "coach" && msg.data) {
        if (currentMode) fetchCoachAndToday();
        else renderCoach(msg.data);
      }
    };
    socket.onclose = () => {
      clearMatchActive();
      setConn("disconnected", "reconnecting…");
      // Cap at 30s so a dashboard left running through a long server outage
      // doesn't keep hammering once a second after the cap.
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    };
    socket.onerror = () => socket.close();
  };

  // ── Rank picker ──────────────────────────────────────────────────

  const initRankSelect = () => {
    const select = $("rank-select");
    select.addEventListener("change", async () => {
      const previous = select.dataset.committed ?? "";
      const chosen = select.value;
      const body = chosen === "" ? { rank: null } : { rank: chosen };
      try {
        const r = await fetch("/api/config/rank", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (r.ok) {
          select.dataset.committed = chosen;
          // WS broadcast will push the updated payload — renderCoach syncs select.value.
        } else {
          select.value = previous;
          select.dataset.state = "error";
          setTimeout(() => { select.dataset.state = ""; }, 1000);
        }
      } catch {
        select.value = previous;
      }
    });
    select.dataset.committed = select.value;
  };

  initModeTabs();
  fetchInitial();
  connect();
  initRankSelect();
})();
