const historyRows = document.getElementById("history-rows");
const historyEmpty = document.getElementById("history-empty");
let detailChart = null;

function setActiveView(name) {
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.id === `view-${name}`);
  }
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.remove("active");
  }
  const tab = document.getElementById(`tab-${name}`);
  if (tab) tab.classList.add("active");
  if (name === "history") refreshHistory();
}

function fmtDuration(seconds) {
  if (seconds == null) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

async function refreshHistory() {
  try {
    const res = await fetch("/api/sessions");
    const data = await res.json();
    renderHistory(data.sessions);
  } catch (err) {
    console.error("failed to load session history", err);
  }
}

function renderHistory(sessions) {
  historyRows.innerHTML = "";
  historyEmpty.classList.toggle("hidden", sessions.length > 0);

  for (const session of sessions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${session.agent_name}</td>
      <td>${new Date(session.started_at * 1000).toLocaleString()}</td>
      <td>${fmtDuration(session.duration_seconds)}</td>
      <td>${session.peak_rss_mb ? session.peak_rss_mb.toFixed(1) + " MB" : "-"}</td>
      <td>${session.command_count}</td>
      <td>${session.cwd || ""}</td>
    `;
    tr.addEventListener("click", () => openDetail(session.id, session.agent_name));
    historyRows.appendChild(tr);
  }
}

async function openDetail(sessionId, agentName) {
  const res = await fetch(`/api/sessions/${sessionId}/timeline`);
  const data = await res.json();

  document.getElementById("detail-title").textContent = `${agentName} — ${sessionId}`;
  renderTimeline(data.timeline);
  renderChart(data.metrics);

  for (const view of document.querySelectorAll(".view")) {
    view.classList.remove("active");
  }
  document.getElementById("view-detail").classList.add("active");
}

function renderTimeline(entries) {
  const container = document.getElementById("detail-timeline");
  container.innerHTML = "";
  for (const entry of entries) {
    const div = document.createElement("div");
    div.className = `timeline-entry ${entry.type}`;
    const time = new Date(entry.ts * 1000).toLocaleTimeString();
    if (entry.type === "command") {
      div.innerHTML = `<span class="ts">${time}</span><span class="tag">[cmd]</span> ${entry.cmdline || ""}`;
    } else {
      div.innerHTML = `<span class="ts">${time}</span><span class="tag">[${entry.event_type}]</span> ${entry.path}`;
    }
    container.appendChild(div);
  }
}

function renderChart(metrics) {
  const ctx = document.getElementById("detail-chart");
  const labels = metrics.map((m) => new Date(m.ts * 1000).toLocaleTimeString());
  const cpu = metrics.map((m) => m.cpu_pct);
  const rss = metrics.map((m) => m.rss_mb);

  if (detailChart) detailChart.destroy();
  detailChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "CPU %", data: cpu, borderColor: "#39ff14", tension: 0.2, yAxisID: "y" },
        { label: "RAM MB", data: rss, borderColor: "#f5a623", tension: 0.2, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { type: "linear", position: "left", ticks: { color: "#6fa96f" } },
        y1: {
          type: "linear",
          position: "right",
          ticks: { color: "#6fa96f" },
          grid: { drawOnChartArea: false },
        },
        x: { ticks: { color: "#6fa96f" } },
      },
      plugins: { legend: { labels: { color: "#c9f7c9" } } },
    },
  });
}

document.getElementById("tab-history").addEventListener("click", () => setActiveView("history"));
document.getElementById("back-to-history").addEventListener("click", () => setActiveView("history"));
