const state = { telemetry: null, models: null, pending: { pending: [] }, selectedAgentId: null };

const fmtBytes = (value) => {
  if (!value) return "0 GiB";
  const gib = value / (1024 ** 3);
  return `${gib >= 10 ? gib.toFixed(0) : gib.toFixed(1)} GiB`;
};

const pct = (value) => `${Math.round(value || 0)}%`;
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;",
})[char]);
const js = (value) => JSON.stringify(String(value ?? ""));
const row = (title, sub, action = "", icon = "") => icon
  ? `<div class="row with-icon">${icon}<div><div class="title">${esc(title)}</div><div class="sub">${esc(sub)}</div></div>${action}</div>`
  : `<div class="row"><div><div class="title">${esc(title)}</div><div class="sub">${esc(sub)}</div></div>${action}</div>`;

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tabs button, .tab-panel").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.add("active");
  });
});

async function getJson(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || response.statusText);
  return body;
}

async function refresh() {
  try {
    state.telemetry = await getJson("/api/telemetry");
    state.models = await getJson("/api/models");
    state.pending = await getJson("/api/manifest/pending");
    document.getElementById("health").textContent = "Online";
    document.getElementById("healthDot").classList.add("online");
    renderTelemetry();
    renderModels();
  } catch (error) {
    document.getElementById("health").textContent = `Offline: ${error.message}`;
    document.getElementById("healthDot").classList.remove("online");
  }
}

function renderTelemetry() {
  const data = state.telemetry;
  const mem = data.memory || {};
  document.getElementById("ramPercent").textContent = pct(mem.ram_used_percent);
  document.getElementById("vramPercent").textContent = pct(mem.vram_used_percent);
  document.getElementById("ramMeter").style.width = pct(mem.ram_used_percent);
  document.getElementById("vramMeter").style.width = pct(mem.vram_used_percent);
  document.getElementById("ramBytes").textContent = `${fmtBytes(mem.ram_used_bytes)} / ${fmtBytes(mem.ram_total_bytes)}`;
  document.getElementById("vramBytes").textContent = `${fmtBytes(mem.vram_used_bytes)} / ${fmtBytes(mem.vram_total_bytes)}`;
  document.getElementById("modelCount").textContent = String(data.models.length);
  document.getElementById("queueCount").textContent = String(data.queue.length);
  document.getElementById("resourceModelRows").innerHTML = data.models.length
    ? data.models.map(loadedModelRow).join("")
    : row("No loaded models", "Warm model backends will appear here.");
  document.getElementById("queueRows").innerHTML = data.queue.length
    ? data.queue.map((item) => row(item.job_id || "queued job", `${item.model_name || ""} ${item.reason || ""}`)).join("")
    : row("Queue clear", "No waiting model jobs.");
  document.getElementById("agentList").innerHTML = data.agents.length
    ? data.agents.map(agentListRow).join("")
    : row("No agents", "Run an agent to edit its manifest.");
  document.getElementById("pendingRequests").innerHTML = state.pending.pending.length
    ? state.pending.pending.map(pendingRow).join("")
    : row("No pending access", "Sandbox access requests that need review will appear here.");
}

function agentListRow(agent) {
  const selected = agent.agent_id === state.selectedAgentId ? " selected" : "";
  const label = agent.display_name || agent.name || agent.agent_id;
  const running = Boolean(agent.running);
  const status = running ? "running" : "stopped";
  const sub = `${agent.agent_id} · ${agent.binary || "persistent sandbox"} · ${agent.specialty || "default"} · ${status}`;
  const stop = running ? `<button class="action" onclick='event.stopPropagation(); stopAgent(${js(agent.agent_id)})'>Stop</button>` : "";
  const actions = `<div class="agent-actions">
    ${stop}
    <button class="action danger" onclick='event.stopPropagation(); deleteAgent(${js(agent.agent_id)})'>Delete</button>
  </div>`;
  return `<div class="row clickable agent-row${running ? " running" : " stopped"}${selected}" onclick='loadManifest(${js(agent.agent_id)})'>
    <span class="status-led ${running ? "green" : "red"}" title="${status}"></span>
    <div>
      <div class="title">${esc(label)}</div>
      <div class="sub">${esc(sub)}</div>
    </div>
    ${actions}
  </div>`;
}

function loadedModelRow(model) {
  const action = `<button class="action" onclick='evictModel(${js(model.name)})'>Stop</button>`;
  const logo = modelLogo(model.name);
  return row(model.name, `${model.backend || ""} · reserved ${model.ram_gb || 0}G RAM · ${model.vram_gb || 0}G VRAM`, action, logo);
}

function modelLogo(name) {
  const lower = String(name || "").toLowerCase();
  if (lower.includes("nemotron")) return `<span class="model-logo"><img src="/icons/models/nvidia.svg" alt="NVIDIA"></span>`;
  if (lower.includes("mistral")) return `<span class="model-logo"><img src="/icons/models/mistral.svg" alt="Mistral AI"></span>`;
  if (lower.includes("qwen")) return `<span class="model-logo"><img src="/icons/models/qwen.svg" alt="Qwen"></span>`;
  return `<span class="model-logo generic">AI</span>`;
}

function pendingRow(item) {
  const title = `${item.agent_id || "agent"} · ${item.kind || "access"} ${item.subject || ""}`;
  const sub = `${item.method || "*"} ${item.path || "*"}`;
  const policy = pendingPolicy(item);
  const buttons = ["always", "ask", "never"].map((choice) =>
    `<button class="action" onclick='setPolicy(${js(item.agent_id)}, ${JSON.stringify(policy)}, "${choice}")'>${choice}</button>`
  ).join("");
  return `<div class="row policy"><div><div class="title">${esc(title)}</div><div class="sub">${esc(sub)}</div></div>${buttons}</div>`;
}

function pendingPolicy(item) {
  const isHttp = item.kind === "http";
  return {
    kind: item.kind || "",
    subject: item.subject || "",
    method: isHttp ? "*" : (item.method || ""),
    path: isHttp ? "*" : (item.path || ""),
  };
}

function renderModels() {
  const data = state.models;
  document.getElementById("selectedModel").textContent = data.selected_model ? `selected ${data.selected_model}` : "selected model unknown";
  document.getElementById("modelCatalog").innerHTML = data.models.map(modelCard).join("");
}

function modelCard(model) {
  const selected = model.selected ? " selected" : "";
  const action = model.selected ? `<span class="chip">Selected</span>` : `<button class="action primary" onclick='selectModel(${js(model.name)})'>Use Model</button>`;
  return `<div class="model-card${selected}">
    <div class="model-title">${modelLogo(model.name)}<div><div class="title">${esc(model.name)}</div><div class="sub">${esc(model.repo_id || "")}</div></div></div>
    <div class="model-meta">
      <span class="chip">${esc(model.backend)}</span>
      <span class="chip">${esc(model.tier)}</span>
      <span class="chip">RAM ${esc(model.ram_gb)}G</span>
      <span class="chip">VRAM ${esc(model.vram_gb)}G</span>
      <span class="chip">${esc(model.context_tokens)} ctx</span>
    </div>
    ${action}
  </div>`;
}

async function loadManifest(agentId) {
  state.selectedAgentId = agentId;
  renderTelemetry();
  const data = await getJson(`/api/agents/${encodeURIComponent(agentId)}/manifest`);
  const policies = data.manifest.policies || [];
  const agent = (state.telemetry?.agents || []).find((item) => item.agent_id === agentId);
  document.getElementById("manifestTitle").textContent = agent?.display_name || agentId;
  document.getElementById("manifestSubtitle").textContent = `${policies.length} persisted policy${policies.length === 1 ? "" : "ies"}`;
  document.getElementById("manifestDetail").innerHTML = policies.length
    ? policies.map((policy) => policyRow(agentId, policy)).join("")
    : row("No policies", "Pending requests will appear after sandboxed network access is attempted.");
}

function policyRow(agentId, policy) {
  const title = `${policy.kind} ${policy.subject}`;
  const sub = `${policy.method || "*"} ${policy.path || "*"} · current ${policy.policy}`;
  const buttons = ["always", "ask", "never"].map((choice) =>
    `<button class="action" onclick='setPolicy(${js(agentId)}, ${JSON.stringify(policy)}, "${choice}")'>${choice}</button>`
  ).join("");
  return `<div class="row policy"><div><div class="title">${esc(title)}</div><div class="sub">${esc(sub)}</div></div>${buttons}</div>`;
}

async function setPolicy(agentId, policy, choice) {
  await getJson(`/api/agents/${encodeURIComponent(agentId)}/manifest/policies`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...policy, policy: choice }),
  });
  await loadManifest(agentId);
  await refresh();
}

async function selectModel(name) {
  await getJson("/api/models/select", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ speciality: "default-instruct", model_name: name }),
  });
  await refresh();
}

async function evictModel(name) {
  await getJson("/api/models/evict", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  await refresh();
}

async function stopAgent(agentId) {
  await getJson(`/api/agents/${encodeURIComponent(agentId)}/stop`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  if (state.selectedAgentId === agentId) state.selectedAgentId = null;
  await refresh();
}

async function deleteAgent(agentId) {
  await getJson(`/api/agents/${encodeURIComponent(agentId)}/delete`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  if (state.selectedAgentId === agentId) {
    state.selectedAgentId = null;
    document.getElementById("manifestTitle").textContent = "Manifest";
    document.getElementById("manifestSubtitle").textContent = "select an agent";
    document.getElementById("manifestDetail").innerHTML = "Select an agent to review its sandbox manifest.";
  }
  await refresh();
}

refresh();
setInterval(refresh, 2000);
