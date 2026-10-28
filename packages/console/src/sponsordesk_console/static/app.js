const $ = (sel, root = document) => root.querySelector(sel);
const state = { tickets: [], filter: "", query: "", current: null, meta: null };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const human = (s) => String(s ?? "").replaceAll("_", " ");
const pct = (v) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);
const ago = (iso) => {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
    " · " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
};

const highlight = (text) =>
  esc(text)
    .replace(/\[[A-Z0-9_]+_\d+\]/g, (m) => `<span class="tok">${m}</span>`)
    .replace(/(?:\*+-?){2,}\*?-?\d{4}|\*{4,}\d{4}/g, (m) => `<span class="mask">${m}</span>`);

function reviewer() {
  return $("#reviewer").value.trim() || "Reviewer";
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || "Request failed");
    err.detail = data.detail;
    throw err;
  }
  return data;
}

// --- KPIs ---------------------------------------------------------------------------

async function loadMetrics() {
  const m = await api("/api/metrics");
  state.metrics = m;
  const q = m.quality;
  const ev = m.events || {};
  $("#kpis").innerHTML = [
    ["Tickets", m.tickets],
    ["Classifier acc.", pct(q.classifier_accuracy), "good"],
    ["Routing acc.", pct(q.routing_accuracy), "good"],
    ["PII leaks", `${q.pii_leaks} / ${q.pii_spans}`, q.pii_leaks === 0 ? "good" : ""],
    ["Approval rate", pct(m.approval_rate)],
    ["Blocked closes", ev.close_blocked || 0],
  ].map(([k, v, cls]) => `<div class="kpi ${cls || ""}">${k}<b>${v}</b></div>`).join("");
}

// --- Inbox --------------------------------------------------------------------------

async function loadTickets() {
  const params = new URLSearchParams();
  if (state.filter) params.set("status", state.filter);
  if (state.query) params.set("q", state.query);
  state.tickets = await api(`/api/tickets?${params}`);
  renderList();
}

function renderList() {
  const list = $("#ticket-list");
  if (!state.tickets.length) {
    list.innerHTML = `<li class="ticket-item"><div class="ti-meta">No tickets match.</div></li>`;
    return;
  }
  list.innerHTML = state.tickets.map((t) => `
    <li class="ticket-item ${state.current === t.ticket_id ? "active" : ""}" data-id="${esc(t.ticket_id)}" tabindex="0">
      <div class="ti-top"><span class="mono">${esc(t.ticket_id)}</span><span>${ago(t.created_at)}</span></div>
      <div class="ti-subject">${esc(t.subject)}</div>
      <div class="ti-meta">
        <span>${esc(t.sponsor)}</span>
        ${t.priority === "urgent" || t.priority === "high" ? `<span class="badge ${t.priority}">${t.priority}</span>` : ""}
        ${t.status !== "new" ? `<span class="badge ${t.status}">${human(t.status)}</span>` : ""}
      </div>
    </li>`).join("");
}

// --- Detail -------------------------------------------------------------------------

async function openTicket(id) {
  state.current = id;
  renderList();
  const t = await api(`/api/tickets/${id}`);
  renderDetail(t);
}

function renderDetail(t) {
  const p = t.proposal;
  const masked = Object.entries(t.masked_counts || {}).filter(([k]) => ["ssn", "account_number", "ein"].includes(k));
  $("#detail").innerHTML = `
    <div class="grid fade-in">
      <div>
        <div class="card ticket-head">
          <div class="meta-row">
            <span class="mono">${esc(t.ticket_id)}</span>
            <span class="badge ${t.status}">${human(t.status)}</span>
            <span class="badge ${t.priority}">${t.priority}</span>
            <span class="badge">${human(t.channel)}</span>
          </div>
          <h2>${highlight(t.subject)}</h2>
          <div class="meta-row">
            <span>${esc(t.sponsor.name)}</span>
            <span>${esc(t.requester.name)}, ${esc(t.requester.title)}</span>
            <span>${ago(t.created_at)}</span>
          </div>
          <div class="body-text">${highlight(t.body)}</div>
          ${masked.length ? `<div class="redaction-sum">${masked.map(([k, n]) => `<span class="badge warn">${n} ${human(k)} masked for reviewer</span>`).join("")}</div>` : ""}
          ${t.status === "new" ? `<div class="actions"><button class="btn" id="run-triage">Run triage</button></div>` : ""}
        </div>
        ${p ? modelCard(p) : ""}
        ${timeline(t.events)}
      </div>
      <div>${p ? proposalCard(t, p) : waitingCard()}</div>
    </div>`;
  bindDetail(t);
}

function waitingCard() {
  return `<div class="card"><h3>Proposal</h3><p style="color:var(--muted);margin:0">
    No proposal yet. Triage redacts PII before any model sees the text, classifies the request,
    checks the plan record and recent payroll files, then drafts a reply and a routing decision
    for a reviewer to approve.</p></div>`;
}

function modelCard(p) {
  const counts = Object.entries(p.redaction_counts || {});
  return `<div class="card">
    <h3>What the model sees</h3>
    <div class="model-view">${highlight(p.model_input_preview)}</div>
    <div class="redaction-sum">${counts.length
      ? counts.map(([k, n]) => `<span class="badge">${n} × ${human(k)}</span>`).join("")
      : `<span class="badge">No PII detected</span>`}</div>
  </div>`;
}

function proposalCard(t, p) {
  const teams = state.meta.teams.map((tm) => `<option value="${tm.id}" ${tm.id === p.team ? "selected" : ""}>${esc(tm.label)}</option>`).join("");
  const decided = t.decision && t.status !== "pending_approval";
  const top = `
    <div class="card">
      <h3>Classification</h3>
      <div class="classify">
        <div class="label">${human(p.call_type)}</div>
        <div class="mono">${pct(p.confidence)}</div>
      </div>
      <div class="conf"><span style="width:${Math.round(p.confidence * 100)}%"></span></div>
      <div class="alts">Next: ${p.alternatives.map(([k, v]) => `${human(k)} ${pct(v)}`).join(" · ")}</div>
      <div class="route">
        <div>Route to <span class="team">${esc(decided ? labelFor(t.decision.team) : p.team_label)}</span></div>
        <div class="why">${esc(p.reason)}</div>
      </div>
    </div>
    <div class="card">
      <h3>Record checks</h3>
      <ul class="findings">${p.findings.map((f) => `<li class="finding ${f.tone}"><b>${esc(f.label)}</b><span>${esc(f.detail)}</span></li>`).join("")}</ul>
    </div>`;

  if (t.status === "pending_approval") {
    return top + `
      <div class="card">
        <h3>Reviewer decision</h3>
        <label class="field-label" for="team">Team</label>
        <select id="team">${teams}</select>
        <label class="field-label" for="draft">Draft reply (tokens are restored only after approval)</label>
        <textarea id="draft">${esc(p.draft_reply)}</textarea>
        <div class="actions">
          <button class="btn ok" id="approve">Approve</button>
          <button class="btn secondary" id="edit">Save edits &amp; approve</button>
          <button class="btn danger" id="reject">Reject</button>
        </div>
      </div>`;
  }
  const replyCard = t.decision?.draft_reply ? `
    <div class="card"><h3>Approved reply</h3><div class="body-text" style="margin:0">${esc(t.decision.draft_reply)}</div>
    <div class="alts" style="margin-top:10px">Approved by ${esc(t.decision.reviewer)}</div></div>` : "";
  if (t.status === "routed") {
    return top + replyCard + `<div class="card"><h3>Work the ticket</h3>
      <p style="margin:0;color:var(--muted)">The assigned team works the issue. Mark it resolved when the fix is in place.</p>
      <div class="actions"><button class="btn" id="resolve">Mark resolved</button>${closeTryButton()}</div>
      <div id="close-result"></div></div>`;
  }
  if (t.status === "resolved") {
    return top + replyCard + `<div class="card"><h3>Close ticket</h3>
      <p style="margin:0 0 10px;color:var(--muted)">The close guard runs in code, not in a prompt. Every criterion must be confirmed.</p>
      <div class="checklist">${p.resolution_criteria.map((c, i) => `<label><input type="checkbox" value="${esc(c)}" id="crit-${i}"> <span>${esc(c)}</span></label>`).join("")}</div>
      <label class="field-label" for="note">Resolution note</label>
      <textarea id="note" style="min-height:90px" placeholder="What fixed it?"></textarea>
      <div class="actions"><button class="btn" id="close">Close ticket</button></div>
      <div id="close-result"></div></div>`;
  }
  return top + replyCard + `<div class="card"><h3>Closed</h3><p style="margin:0;color:var(--ok)">All resolution criteria were confirmed.</p></div>`;
}

const closeTryButton = () => `<button class="btn danger" id="close-early">Try to close now</button>`;
const labelFor = (id) => state.meta.teams.find((t) => t.id === id)?.label || id;

function timeline(events) {
  if (!events?.length) return "";
  return `<div class="card"><h3>Audit trail</h3><ul class="timeline">${events.map((e) => `
    <li><span class="ev ${e.event}">${human(e.event)}</span> · ${esc(e.actor)}
    <div class="when">${new Date(e.at).toLocaleString()}</div>
    ${e.detail?.reasons ? `<div class="alts">${e.detail.reasons.map(esc).join("<br>")}</div>` : ""}</li>`).join("")}</ul></div>`;
}

function blocked(reasons) {
  $("#close-result").innerHTML = `<div class="blocked fade-in"><b>Close blocked by guard</b><ul>${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>`;
}

async function act(fn, btn) {
  const label = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>`; }
  try {
    await fn();
  } finally {
    if (btn && document.body.contains(btn)) { btn.disabled = false; btn.innerHTML = label; }
  }
}

async function refresh(id) {
  await Promise.all([openTicket(id), loadTickets(), loadMetrics()]);
}

function bindDetail(t) {
  const id = t.ticket_id;
  $("#run-triage")?.addEventListener("click", (e) => act(async () => {
    await api(`/api/tickets/${id}/triage`, { method: "POST" });
    toast("Proposal ready for review");
    await refresh(id);
  }, e.currentTarget));

  const decide = (action) => (e) => act(async () => {
    const body = { action, reviewer: reviewer() };
    if (action !== "reject") {
      body.draft_reply = $("#draft").value;
      body.team = $("#team").value;
      if (action === "approve" && (body.draft_reply !== t.proposal.draft_reply || body.team !== t.proposal.team)) {
        body.action = "edit";
      }
    } else {
      body.reason = "Reviewer rejected the proposal";
    }
    await api(`/api/tickets/${id}/decision`, { method: "POST", body });
    toast(action === "reject" ? "Proposal rejected" : "Approved and routed");
    await refresh(id);
  }, e.currentTarget);
  $("#approve")?.addEventListener("click", decide("approve"));
  $("#edit")?.addEventListener("click", decide("edit"));
  $("#reject")?.addEventListener("click", decide("reject"));

  $("#resolve")?.addEventListener("click", (e) => act(async () => {
    await api(`/api/tickets/${id}/resolve`, { method: "POST", body: { reviewer: reviewer() } });
    toast("Marked resolved");
    await refresh(id);
  }, e.currentTarget));

  const tryClose = (confirmed, note) => api(`/api/tickets/${id}/close`, {
    method: "POST", body: { reviewer: reviewer(), confirmed, note },
  });
  $("#close-early")?.addEventListener("click", (e) => act(async () => {
    try { await tryClose([], ""); } catch (err) { blocked(err.detail?.reasons || [err.message]); loadMetrics(); }
  }, e.currentTarget));
  $("#close")?.addEventListener("click", (e) => act(async () => {
    const confirmed = [...document.querySelectorAll(".checklist input:checked")].map((i) => i.value);
    try {
      await tryClose(confirmed, $("#note").value);
      toast("Ticket closed");
      await refresh(id);
    } catch (err) {
      blocked(err.detail?.reasons || [err.message]);
      loadMetrics();
    }
  }, e.currentTarget));
}

// --- Quality report -----------------------------------------------------------------

function renderQuality() {
  const q = state.metrics.quality;
  const max = Math.max(...q.confusion.flat());
  const cell = (n, diag) => {
    const a = n ? 0.12 + 0.6 * (n / max) : 0;
    const color = diag ? `rgba(21,128,61,${a})` : `rgba(185,28,28,${a})`;
    return `<td style="background:${n ? color : "transparent"}">${n || ""}</td>`;
  };
  $("#quality-body").innerHTML = `
    <div class="stat-grid">
      <div class="stat"><div class="v">${pct(q.classifier_accuracy)}</div><div class="k">Classifier accuracy (held-out ${q.test_size})</div></div>
      <div class="stat"><div class="v">${pct(q.classifier_macro_f1)}</div><div class="k">Macro F1</div></div>
      <div class="stat"><div class="v">${pct(q.routing_accuracy)}</div><div class="k">Routing accuracy</div></div>
      <div class="stat"><div class="v">${q.pii_leaks}</div><div class="k">Raw PII values reaching the model (of ${q.pii_spans})</div></div>
    </div>
    <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint)">Confusion matrix · TF-IDF + logistic regression baseline</h3>
    <table class="cm"><tr><th></th>${q.labels.map((l) => `<th class="col">${human(l)}</th>`).join("")}</tr>
      ${q.confusion.map((row, i) => `<tr><th class="row">${human(q.labels[i])}</th>${row.map((n, j) => cell(n, i === j)).join("")}</tr>`).join("")}
    </table>
    <p class="muted" style="color:var(--muted);font-size:12px">Rows are true labels, columns are predictions. Trained on redacted text from ${q.train_size} tickets.
    The templated synthetic tickets are easy to separate; harder paraphrased tickets and the Claude comparison are next.</p>`;
}

// --- Boot ---------------------------------------------------------------------------

async function boot() {
  try { $("#reviewer").value = localStorage.getItem("reviewer") || "Reviewer"; } catch { $("#reviewer").value = "Reviewer"; }
  $("#reviewer").addEventListener("change", () => { try { localStorage.setItem("reviewer", reviewer()); } catch {} });
  $("#filters").addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === b));
    state.filter = b.dataset.status;
    loadTickets();
  });
  let timer;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.query = e.target.value.trim(); loadTickets(); }, 180);
  });
  const list = $("#ticket-list");
  list.addEventListener("click", (e) => { const li = e.target.closest(".ticket-item[data-id]"); if (li) openTicket(li.dataset.id); });
  list.addEventListener("keydown", (e) => { if (e.key === "Enter") { const li = e.target.closest(".ticket-item[data-id]"); if (li) openTicket(li.dataset.id); } });
  $("#quality-btn").addEventListener("click", () => { renderQuality(); $("#quality").showModal(); });
  $("#quality-close").addEventListener("click", () => $("#quality").close());

  state.meta = await api("/api/meta");
  await Promise.all([loadMetrics(), loadTickets()]);
}

boot().catch((e) => toast(e.message));
