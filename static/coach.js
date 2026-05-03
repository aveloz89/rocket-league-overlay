(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const conn = $("conn");
  const empty = $("empty");
  const hero = $("hero");
  const cats = $("cats");
  const trendSection = $("trend-section");
  const todayBar = $("today-bar");

  const setConn = (state, label) => {
    conn.dataset.state = state;
    conn.textContent = label;
  };

  // Layout: [{key, label, suffix, source: "last_match" | "rolling_avg", direction}]
  // direction: "high_good" / "low_good" / "neutral" — controls the trend arrow color.
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

  const buildRow = (def, last, avg) => {
    const li = document.createElement("li");
    li.className = "row";

    const label = document.createElement("span");
    label.className = "label";
    label.textContent = def.label;

    const value = document.createElement("span");
    value.className = "value";
    const v = last?.[def.key];
    value.textContent = v === null || v === undefined ? "—" : fmt(v, def.suffix);

    const compare = document.createElement("span");
    compare.className = "compare";
    const a = avg?.[def.key];
    if (a === null || a === undefined || v === null || v === undefined) {
      compare.textContent = "";
      compare.dataset.trend = "flat";
    } else {
      const diff = Number(v) - Number(a);
      compare.textContent = `avg ${fmt(a, def.suffix)}`;
      const tolerance = def.suffix === "%" ? 3 : Math.max(1, Math.abs(Number(a)) * 0.05);
      if (Math.abs(diff) <= tolerance || def.direction === "neutral") {
        compare.dataset.trend = "flat";
      } else if ((def.direction === "high_good" && diff > 0)
                 || (def.direction === "low_good" && diff < 0)) {
        compare.dataset.trend = "up";
      } else {
        compare.dataset.trend = "down";
      }
    }

    li.append(label, value, compare);
    return li;
  };

  const renderRows = (containerId, defs, last, avg) => {
    const container = $(containerId);
    container.replaceChildren();
    for (const def of defs) {
      container.appendChild(buildRow(def, last, avg));
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

  const renderToday = (today) => {
    if (!today || !today.matches) {
      todayBar.hidden = true;
      return;
    }
    todayBar.hidden = false;
    $("today-matches").textContent = today.matches;
    $("today-record").textContent = `${today.wins}W ${today.losses}L`;
    $("today-winrate").textContent = `${today.win_rate}%`;
    $("today-streak").textContent = today.win_streak;
    $("today-best").textContent = today.best_score;
  };

  const render = (data) => {
    if (!data || !data.last_match) {
      empty.hidden = false;
      hero.hidden = true;
      cats.hidden = true;
      trendSection.hidden = true;
      todayBar.hidden = true;
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

    renderInsights(data.insights);
    renderRows("cat-pos", POSITIONING_ROWS, last, avg);
    renderRows("cat-boost", BOOST_ROWS, last, avg);
    renderRows("cat-mech", MECH_ROWS, last, avg);
    renderRows("cat-highlights", HIGHLIGHT_ROWS, last, avg);
    renderTrend(data.trend);
    renderToday(data.today);
  };

  // Initial fetch — covers the case where a user opens /coach with no live game.
  const fetchCoach = async () => {
    try {
      const r = await fetch("/api/coach");
      if (!r.ok) return;
      const data = await r.json();
      render(data);
    } catch (err) {
      // Network failure on first load — the WS will refill once connected.
    }
  };

  // WebSocket — same protocol/origin as the page, served by the same FastAPI app.
  let socket;
  let retryDelay = 500;
  const connect = () => {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    socket = new WebSocket(url);
    socket.onopen = () => {
      retryDelay = 500;
      setConn("connected", "live");
    };
    socket.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch {
        return;
      }
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "coach" && msg.data) render(msg.data);
    };
    socket.onclose = () => {
      setConn("disconnected", "reconnecting…");
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    };
    socket.onerror = () => socket.close();
  };

  fetchCoach();
  connect();
})();
