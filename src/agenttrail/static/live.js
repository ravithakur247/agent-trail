const liveContainer = document.getElementById("live-sessions");
const liveEmpty = document.getElementById("live-empty");

function fmtMb(mb) {
  if (mb == null) return "-";
  return `${mb.toFixed(1)} MB`;
}

function fmtPct(pct) {
  if (pct == null) return "-";
  return `${pct.toFixed(1)}%`;
}

async function refreshLive() {
  try {
    const res = await fetch("/api/live");
    const data = await res.json();
    renderLive(data.sessions);
  } catch (err) {
    console.error("failed to load live sessions", err);
  }
}

function renderLive(sessions) {
  liveContainer.innerHTML = "";
  liveEmpty.classList.toggle("hidden", sessions.length > 0);

  for (const session of sessions) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${session.agent_name} <span style="color:var(--fg-dim);font-size:12px">pid ${session.pid}</span></h3>
      <div class="meta">${session.cwd || ""}</div>
      <div class="stats">
        <div>cpu<strong>${fmtPct(session.cpu_pct)}</strong></div>
        <div>ram<strong>${fmtMb(session.rss_mb)}</strong></div>
        <div>children<strong>${session.child_count}</strong></div>
      </div>
      <button class="btn-kill" data-id="${session.id}">kill session</button>
    `;
    card.querySelector(".btn-kill").addEventListener("click", () => killSession(session.id));
    liveContainer.appendChild(card);
  }
}

async function killSession(sessionId) {
  if (!confirm("Terminate this agent session's entire process tree?")) return;
  await fetch(`/api/sessions/${sessionId}/kill`, { method: "POST" });
  refreshLive();
}

document.getElementById("tab-live").addEventListener("click", () => setActiveView("live"));

refreshLive();
setInterval(refreshLive, 3000);
