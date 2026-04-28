const state = {
  events: [],
  storeEvents: [],
  previous: {
    gestureKey: "",
    voiceKey: "",
    difyKey: "",
  },
};
const AUTH_STORAGE_KEY = "swordVoiceAgentAuthToken";

const $ = (id) => document.getElementById(id);

function text(id, value) {
  $(id).textContent = value === undefined || value === null || value === "" ? "-" : String(value);
}

function setPanel(panelId, stateName, labelId, label) {
  const panel = $(panelId);
  panel.dataset.state = stateName;
  text(labelId, label);
}

function short(value, max = 48) {
  const textValue = value === undefined || value === null ? "" : String(value);
  if (textValue.length <= max) return textValue || "-";
  return `${textValue.slice(0, max - 1)}...`;
}

function formatTime(seconds) {
  if (!seconds) return "-";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("ja-JP", { hour12: false });
}

function formatDateTime(seconds) {
  if (!seconds) return "-";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("ja-JP", { hour12: false });
}

function addEvent(key, textValue) {
  if (!key || state.events.some((event) => event.key === key)) return;
  state.events.unshift({
    key,
    time: new Date(),
    text: textValue,
  });
  state.events = state.events.slice(0, 12);
  renderEvents();
}

function renderEvents() {
  const log = $("eventLog");
  log.innerHTML = "";
  const persisted = state.storeEvents
    .slice()
    .reverse()
    .map((event) => ({
      key: event.event_id || `${event.type}:${event.timestamp}:${event.turn_id || ""}`,
      time: new Date(Number(event.timestamp || 0) * 1000),
      text: formatStoredEvent(event),
    }));
  const localEvents = persisted.length
    ? state.events.filter((event) => event.key.startsWith("auth") || event.key.startsWith("error"))
    : state.events;
  const events = [...localEvents, ...persisted].slice(0, 16);
  for (const event of events) {
    const item = document.createElement("li");
    const eventTime = document.createElement("span");
    const eventText = document.createElement("span");
    eventTime.className = "event-time";
    eventText.className = "event-text";
    eventTime.textContent = event.time.toLocaleTimeString("ja-JP", { hour12: false });
    eventText.textContent = event.text;
    item.append(eventTime, eventText);
    log.appendChild(item);
  }
}

function formatStoredEvent(event) {
  const data = event.payload || event.data || {};
  const turn = event.turn_id ? ` [${event.turn_id.slice(0, 8)}]` : "";
  if (event.type === "gesture.received") {
    const response = data.response || {};
    const command = response.voice_control_command || {};
    const decision = response.gate_decision || {};
    return `gesture${turn}: ${command.action || "none"} / ${decision.reason || "stable"}`;
  }
  if (event.type === "dify.response") {
    if (data.skipped) return `dify${turn}: skipped ${data.skip_reason || ""}`;
    return `dify${turn}: ${short(data.response_text || "", 96)}`;
  }
  return `${event.type}${turn}`;
}

function renderStatus(payload) {
  const gesture = payload.gesture || {};
  const voice = payload.voice || {};
  const dify = payload.dify || {};
  const inputGate = payload.input_gate || {};
  const files = payload.files || {};

  text("lastUpdated", `更新 ${formatDateTime(payload.timestamp)}`);
  text("sourceSummary", payload.paths?.status_dir || payload.paths?.cache_dir || "-");
  state.storeEvents = Array.isArray(payload.events) ? payload.events : [];
  renderModules(Array.isArray(payload.modules) ? payload.modules : []);

  const gestureActive = Boolean(gesture.raw_active);
  const gestureReady = Boolean(gesture.available);
  setPanel(
    "gesturePanel",
    gestureReady ? (gestureActive ? "ok" : "warn") : "bad",
    "gestureState",
    gestureReady ? (gestureActive ? "detected" : "idle") : "missing"
  );
  text("gestureConfidence", gesture.confidence === null || gesture.confidence === undefined ? "-" : gesture.confidence.toFixed(3));
  text("gestureMic", gesture.mic_enabled ? "enabled" : "disabled");
  text("gestureAction", gesture.action || "-");
  text("gestureTurn", shortTurn(gesture.turn_id));

  const inputGateState = inputGatePayload(inputGate);
  const inputGateReady = Boolean(inputGate.available);
  const inputGateEnabled = Boolean(inputGateState.input_enabled ?? inputGateState.mic_enabled);
  setPanel(
    "inputGatePanel",
    inputGateReady ? (inputGateEnabled ? "ok" : "warn") : "bad",
    "inputGateState",
    inputGateReady ? (inputGateEnabled ? "enabled" : "disabled") : "missing"
  );
  text("inputGateEnabled", inputGateReady ? String(inputGateEnabled) : "-");
  text("inputGateReason", inputGateState.reason || inputGate.error || "-");
  text("inputGateSource", inputGateState.source || "-");

  const voiceReady = Boolean(voice.available);
  setPanel("voicePanel", voiceReady ? "ok" : "bad", "voiceState", voiceReady ? "ready" : "missing");
  text("voiceTranscriptShort", short(voice.transcript));
  text("voiceCommandShort", short(voice.command));
  text("voiceUpdated", formatTime(voice.updated_at));
  text("voiceTurn", shortTurn(voice.turn_id));
  text("voiceTranscript", voice.transcript);
  text("voiceCommand", voice.command);
  text("handoffPath", files.handoff_json?.path || "-");

  const difyReady = Boolean(dify.available);
  const difySkipped = Boolean(dify.skipped);
  setPanel(
    "difyPanel",
    difyReady ? (difySkipped ? "warn" : "ok") : "bad",
    "difyState",
    difyReady ? (difySkipped ? "skipped" : "ready") : "missing"
  );
  const usage = dify.usage || {};
  text("difyTokens", usage.total_tokens ?? "-");
  text("difyCost", usage.total_price ? `$${usage.total_price}` : "-");
  text("difyLatency", usage.latency ? `${Number(usage.latency).toFixed(2)}s` : "-");
  text("difyTurn", shortTurn(dify.turn_id));
  text("difyAnswer", dify.answer);
  text("conversationId", dify.conversation_id ? `conversation ${dify.conversation_id}` : "-");
  text("messageId", dify.message_id ? `message ${dify.message_id}` : "message: -");
  text("difyUpdated", `updated ${formatTime(dify.updated_at)}`);

  collectEvents(gesture, voice, dify);
  renderEvents();
}

function renderModules(modules) {
  const list = $("moduleList");
  list.innerHTML = "";
  for (const moduleStatus of modules) {
    const item = document.createElement("div");
    const head = document.createElement("div");
    const name = document.createElement("strong");
    const pill = document.createElement("span");
    const meta = document.createElement("span");
    const stateName = moduleStatus.state || "missing";
    item.className = "module-item";
    item.dataset.state = moduleStateForPanel(stateName);
    head.className = "module-head";
    name.textContent = moduleStatus.label || moduleStatus.name || "-";
    pill.className = "state-pill";
    pill.textContent = stateName;
    meta.className = "module-meta";
    meta.textContent = moduleStatusLine(moduleStatus);
    head.append(name, pill);
    item.append(head, meta);
    list.appendChild(item);
  }
}

function moduleStateForPanel(stateName) {
  if (stateName === "running") return "ok";
  if (stateName === "starting" || stateName === "stale") return "warn";
  return "bad";
}

function moduleStatusLine(moduleStatus) {
  const parts = [];
  if (moduleStatus.updated_at) {
    parts.push(`updated ${formatTime(moduleStatus.updated_at)}`);
  }
  if (moduleStatus.age_seconds !== null && moduleStatus.age_seconds !== undefined) {
    parts.push(`${Number(moduleStatus.age_seconds).toFixed(1)}s ago`);
  }
  if (moduleStatus.detail) {
    parts.push(moduleStatus.detail);
  }
  return parts.join(" / ") || "-";
}

function inputGatePayload(inputGate) {
  const payload = inputGate.payload || {};
  return payload.input_gate || payload;
}

function shortTurn(turnId) {
  const value = turnId === undefined || turnId === null ? "" : String(turnId);
  return value ? value.slice(0, 8) : "-";
}

function collectEvents(gesture, voice, dify) {
  const gestureKey = `${gesture.sequence || ""}:${gesture.updated_at || ""}:${gesture.action || ""}`;
  if (gestureKey && gestureKey !== state.previous.gestureKey && gesture.available) {
    state.previous.gestureKey = gestureKey;
    addEvent(gestureKey, `gesture ${gesture.raw_active ? "detected" : "idle"} / ${gesture.action || "none"} / ${gesture.reason || "stable"}`);
  }

  const voiceKey = `${voice.updated_at || ""}:${voice.command || ""}`;
  if (voiceKey && voiceKey !== state.previous.voiceKey && voice.available) {
    state.previous.voiceKey = voiceKey;
    addEvent(voiceKey, `voice command: ${short(voice.command, 92)}`);
  }

  const difyKey = `${dify.updated_at || ""}:${dify.message_id || ""}:${dify.answer || ""}`;
  if (difyKey && difyKey !== state.previous.difyKey && dify.available) {
    state.previous.difyKey = difyKey;
    const label = dify.skipped ? `dify skipped: ${dify.skip_reason || "unknown"}` : `dify response: ${short(dify.answer, 92)}`;
    addEvent(difyKey, label);
  }
}

function authHeaders() {
  const headers = {};
  const token = sessionStorage.getItem(AUTH_STORAGE_KEY) || "";
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store", headers: authHeaders() });
    if (response.status === 401) {
      setPanel("gesturePanel", "bad", "gestureState", "auth");
      setPanel("inputGatePanel", "bad", "inputGateState", "auth");
      setPanel("voicePanel", "bad", "voiceState", "auth");
      setPanel("difyPanel", "bad", "difyState", "auth");
      addEvent("auth-required", "auth token is required for /api/status");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderStatus(await response.json());
  } catch (error) {
    setPanel("gesturePanel", "bad", "gestureState", "error");
    setPanel("inputGatePanel", "bad", "inputGateState", "error");
    setPanel("voicePanel", "bad", "voiceState", "error");
    setPanel("difyPanel", "bad", "difyState", "error");
    addEvent(`error:${Date.now()}`, `console refresh failed: ${error.message}`);
  }
}

async function clearStatus() {
  if (!window.confirm("ローカルの status キャッシュとイベント履歴を削除します。")) return;
  try {
    const response = await fetch("/api/status/clear", {
      method: "POST",
      cache: "no-store",
      headers: authHeaders(),
    });
    if (response.status === 401) {
      addEvent("auth-required-clear", "auth token is required for status clear");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.events = [];
    state.storeEvents = [];
    addEvent(`status-cleared:${Date.now()}`, "local status cache cleared");
    await refresh();
  } catch (error) {
    addEvent(`error:${Date.now()}`, `status clear failed: ${error.message}`);
  }
}

$("authTokenInput").value = sessionStorage.getItem(AUTH_STORAGE_KEY) || "";
$("saveTokenButton").addEventListener("click", () => {
  const token = $("authTokenInput").value.trim();
  if (token) {
    sessionStorage.setItem(AUTH_STORAGE_KEY, token);
    addEvent(`auth-updated:${Date.now()}`, "auth token saved for this browser session");
  } else {
    sessionStorage.removeItem(AUTH_STORAGE_KEY);
    addEvent(`auth-cleared:${Date.now()}`, "auth token cleared");
  }
  refresh();
});
$("refreshButton").addEventListener("click", refresh);
$("clearStatusButton").addEventListener("click", clearStatus);
refresh();
setInterval(refresh, 1000);
