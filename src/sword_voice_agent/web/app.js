const AUTH_STORAGE_KEY = "swordVoiceAgentAuthToken";
const AVATAR_BACKGROUND_COLOR = "#070b12";
const AVATAR_VIEW_STORAGE_KEY = "swordVoiceAgentAvatarViewSettings";
const DEFAULT_AVATAR_VIEW_SETTINGS = {
  projection: "perspective",
  cameraDistance: 3.1,
  cameraFov: 28,
  orthographicWidth: 2.1,
  avatarHeight: 1.7,
  lightHeight: 1.25,
};

const state = {
  events: [],
  storeEvents: [],
  previous: {
    gestureKey: "",
    voiceKey: "",
    difyKey: "",
    ttsKey: "",
  },
  avatar: {
    url: "",
    lastKey: "",
    lastConfigKey: "",
    lastSentAt: null,
    popup: null,
    viewSettings: loadAvatarViewSettings(),
  },
  ttsVolume: {
    dirty: false,
  },
};

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
  if (event.type === "gesture.diagnostic") {
    const kind = data.diagnostic_type || "diagnostic";
    const status = data.status || "-";
    const fps = data.fps === null || data.fps === undefined ? "" : ` fps=${Number(data.fps).toFixed(1)}`;
    return `gesture ${kind}: ${status}${fps}`;
  }
  if (event.type === "dify.response") {
    if (data.skipped) return `dify${turn}: skipped ${data.skip_reason || ""}`;
    return `dify${turn}: ${short(data.response_text || "", 96)}`;
  }
  if (event.type === "dify.first_token") {
    const elapsed = data.elapsed_s === null || data.elapsed_s === undefined ? "" : ` ${Number(data.elapsed_s).toFixed(2)}s`;
    return `dify${turn}: first token${elapsed}`;
  }
  if (event.type === "dify.done") {
    const elapsed = data.elapsed_s === null || data.elapsed_s === undefined ? "" : ` ${Number(data.elapsed_s).toFixed(2)}s`;
    return `dify${turn}: done${elapsed}`;
  }
  if (event.type === "tts.state") {
    return `tts${turn}: ${data.phase || "-"}`;
  }
  if (event.type && event.type.startsWith("ai_core.")) {
    return `${event.type}${turn}`;
  }
  return `${event.type}${turn}`;
}

function renderStatus(payload) {
  const gesture = payload.gesture || {};
  const gestureDiagnostic = payload.gesture_diagnostic || {};
  const voice = payload.voice || {};
  const dify = payload.dify || {};
  const tts = payload.tts || {};
  const avatar = payload.avatar || {};
  const inputGate = payload.input_gate || {};
  const files = payload.files || {};

  text("lastUpdated", `更新 ${formatDateTime(payload.timestamp)}`);
  text("sourceSummary", payload.paths?.status_dir || payload.paths?.cache_dir || "-");
  state.storeEvents = Array.isArray(payload.events) ? payload.events : [];
  renderModules(Array.isArray(payload.modules) ? payload.modules : []);

  const gestureActive = Boolean(gesture.raw_active || gestureDiagnostic.raw_active);
  const gestureReady = Boolean(gesture.available || gestureDiagnostic.available);
  const gestureConfidence = gesture.confidence ?? gestureDiagnostic.confidence;
  setPanel(
    "gesturePanel",
    gestureReady ? (gestureActive ? "ok" : "warn") : "bad",
    "gestureState",
    gestureReady ? (gestureActive ? "detected" : "idle") : "missing"
  );
  text("gestureConfidence", gestureConfidence === null || gestureConfidence === undefined ? "-" : Number(gestureConfidence).toFixed(3));
  text("gestureHand", gestureDiagnostic.hand_detected === null || gestureDiagnostic.hand_detected === undefined ? "-" : String(Boolean(gestureDiagnostic.hand_detected)));
  text("gesturePrimary", gestureDiagnostic.primary_gesture || "-");
  text("gestureFps", gestureDiagnostic.fps === null || gestureDiagnostic.fps === undefined ? "-" : Number(gestureDiagnostic.fps).toFixed(1));
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
  const firstTokenLatency = usage.first_token_latency ? `first ${Number(usage.first_token_latency).toFixed(2)}s` : "";
  const totalLatency = usage.latency ? `total ${Number(usage.latency).toFixed(2)}s` : "";
  text("difyLatency", [firstTokenLatency, totalLatency].filter(Boolean).join(" / ") || "-");
  text("difyTurn", shortTurn(dify.turn_id));
  text("difyAnswer", dify.answer);
  text("conversationId", dify.conversation_id ? `conversation ${dify.conversation_id}` : "-");
  text("messageId", dify.message_id ? `message ${dify.message_id}` : "message: -");
  text("difyUpdated", `updated ${formatTime(dify.updated_at)}`);

  const ttsReady = Boolean(tts.available);
  const ttsPhase = tts.phase || "";
  setPanel(
    "ttsPanel",
    ttsReady ? ttsPanelState(ttsPhase) : "bad",
    "ttsState",
    ttsReady ? ttsPhase || "ready" : "missing"
  );
  text("ttsEngine", tts.engine || "-");
  text("ttsPlayer", tts.player || "-");
  text("ttsAppVolume", formatAppVolume(tts.app_volume));
  text("ttsVolume", tts.volume === null || tts.volume === undefined ? "-" : tts.volume);
  text("ttsRate", tts.rate === null || tts.rate === undefined ? "-" : tts.rate);
  text("ttsVoice", tts.voice_name || "(default)");
  text("ttsUpdated", formatTime(tts.updated_at));
  text("ttsRequest", short(tts.request_id || tts.message_id || tts.conversation_id || tts.turn_id || ""));
  text("ttsError", short(tts.error || "", 72));
  updateTtsVolumeControl(tts);

  const avatarEvent = buildAvatarState({ gesture, voice, dify, tts, inputGate });
  updateAvatarBridge(avatar, avatarEvent);

  collectEvents(gesture, voice, dify, tts);
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

function ttsPanelState(phase) {
  if (phase === "error") return "bad";
  if (phase === "speaking" || phase === "completed" || phase === "skipped") return "ok";
  return "warn";
}

function formatAppVolume(value) {
  if (value === null || value === undefined || value === "") return "-";
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "-";
  return `${Math.round(numberValue * 100)}%`;
}

function updateTtsVolumeControl(tts) {
  const slider = $("ttsAppVolumeSlider");
  const button = $("ttsVolumeApplyButton");
  const previewButton = $("ttsVolumePreviewButton");
  const available = Boolean(tts.app_volume_available || tts.volume_url || tts.app_volume_file);
  const previewAvailable = Boolean(tts.volume_preview_url);
  slider.disabled = !available;
  button.disabled = !available || !state.ttsVolume.dirty;
  previewButton.disabled = !previewAvailable;
  if (!state.ttsVolume.dirty) {
    const appVolume = Number(tts.app_volume);
    slider.value = Number.isFinite(appVolume) ? String(Math.round(appVolume * 100)) : "100";
    text("ttsVolumeApplyState", available ? `${slider.value}%` : "-");
  }
}

function updateAvatarBridge(avatar, avatarEvent) {
  const url = avatarRuntimeUrl(avatar.url || "", avatar.model_url || "");
  const ready = Boolean(avatar.available);
  const frame = $("avatarFrame");
  const bridgeEnabled = Boolean(url);
  setPanel(
    "avatarPanel",
    ready ? "ok" : bridgeEnabled ? "warn" : "bad",
    "avatarState",
    ready ? "ready" : bridgeEnabled ? "offline" : "missing"
  );
  text("avatarUrl", url);
  text("avatarPhase", avatarEvent.phase);
  text("avatarBridge", bridgeEnabled ? "enabled" : "disabled");

  if (url && state.avatar.url !== url) {
    state.avatar.url = url;
    state.avatar.lastKey = "";
    state.avatar.lastConfigKey = "";
    frame.src = url;
  } else if (!url && state.avatar.url) {
    state.avatar.url = "";
    state.avatar.lastKey = "";
    state.avatar.lastConfigKey = "";
    frame.removeAttribute("src");
  }

  if (!url) {
    text("avatarLastSent", "-");
    text("avatarLastTurn", "-");
    return;
  }

  postAvatarConfig(url);
  postAvatarState(url, avatarEvent);
  text("avatarLastSent", state.avatar.lastSentAt ? state.avatar.lastSentAt.toLocaleTimeString("ja-JP", { hour12: false }) : "-");
  text("avatarLastTurn", shortTurn(avatarEvent.turn_id));
}

function buildAvatarState({ gesture, voice, dify, tts, inputGate }) {
  const now = Date.now() / 1000;
  const ttsPhase = tts.phase || "";
  const difyAge = dify.updated_at ? now - Number(dify.updated_at) : Number.POSITIVE_INFINITY;
  const voiceAge = voice.updated_at ? now - Number(voice.updated_at) : Number.POSITIVE_INFINITY;
  const inputGateState = inputGatePayload(inputGate);
  let phase = "idle";
  let emotion = "neutral";

  if (ttsPhase === "error" || (dify.available && dify.skipped)) {
    phase = "error";
    emotion = "troubled";
  } else if (ttsPhase === "speaking" || (dify.available && dify.answer && difyAge < 12)) {
    phase = "speaking";
    emotion = "happy";
  } else if (voice.available && voice.command && voiceAge < 12) {
    phase = "thinking";
    emotion = "serious";
  } else if (gesture.raw_active || gesture.mic_enabled || inputGateState.input_enabled || inputGateState.mic_enabled) {
    phase = "listening";
    emotion = "serious";
  }

  return {
    type: "avatar_state",
    phase,
    emotion,
    posture: avatarPostureForPhase(phase, { gesture, tts, inputGate }),
    gesture: gesture.raw_active ? "sword_sign" : "none",
    speech: {
      state: ttsPhase || phase,
      source: "console_status",
      volume: speechVolume(tts),
    },
    turn_id: dify.turn_id || tts.turn_id || voice.turn_id || gesture.turn_id || undefined,
    text: dify.answer || voice.command || undefined,
    timestamp: Date.now(),
  };
}

function avatarPostureForPhase(phase, { gesture, tts, inputGate }) {
  const ttsPhase = tts.phase || "";
  const inputGateState = inputGatePayload(inputGate);
  const activeListening = Boolean(gesture.raw_active || gesture.mic_enabled || inputGateState.input_enabled || inputGateState.mic_enabled);
  if (phase === "speaking" || ttsPhase === "speaking") {
    return { preset: "speaking", intensity: 0.42, source: "console_status" };
  }
  if (phase === "thinking") {
    return { preset: "thinking", intensity: 0.38, source: "console_status" };
  }
  if (phase === "listening") {
    return { preset: activeListening ? "attentive" : "lean_forward", intensity: 0.36, source: "console_status" };
  }
  if (phase === "error") {
    return { preset: "lean_back", intensity: 0.46, source: "console_status" };
  }
  return { preset: "neutral", intensity: 0.22, source: "console_status" };
}

function speechVolume(tts) {
  const appVolume = Number(tts.app_volume);
  if (Number.isFinite(appVolume)) return Math.max(0, Math.min(1, appVolume));
  const sapiVolume = Number(tts.volume);
  if (Number.isFinite(sapiVolume)) return Math.max(0, Math.min(1, sapiVolume / 100));
  return 0;
}

function postAvatarState(url, event) {
  const eventKey = JSON.stringify({
    url,
    phase: event.phase,
    emotion: event.emotion,
    posture: event.posture || {},
    gesture: event.gesture,
    speech: event.speech || {},
    turn_id: event.turn_id || "",
    text: event.text || "",
  });
  if (eventKey === state.avatar.lastKey) return;
  state.avatar.lastKey = eventKey;
  state.avatar.lastSentAt = new Date();

  const targetOrigin = originFromUrl(url);
  const frameWindow = $("avatarFrame").contentWindow;
  if (frameWindow) {
    frameWindow.postMessage(event, targetOrigin);
  }
  if (state.avatar.popup && !state.avatar.popup.closed) {
    state.avatar.popup.postMessage(event, targetOrigin);
  }
}

function postAvatarConfig(url) {
  const viewSettings = state.avatar.viewSettings || DEFAULT_AVATAR_VIEW_SETTINGS;
  const config = {
    type: "avatar_config",
    background: AVATAR_BACKGROUND_COLOR,
    projection: viewSettings.projection,
    camera_distance: viewSettings.cameraDistance,
    camera_fov: viewSettings.cameraFov,
    orthographic_width: viewSettings.orthographicWidth,
    ortho_width: viewSettings.orthographicWidth,
    avatar_height: viewSettings.avatarHeight,
    light_height: viewSettings.lightHeight,
  };
  const configKey = JSON.stringify({ url, ...config });
  if (configKey === state.avatar.lastConfigKey) return;
  state.avatar.lastConfigKey = configKey;

  const targetOrigin = originFromUrl(url);
  const frameWindow = $("avatarFrame").contentWindow;
  if (frameWindow) {
    frameWindow.postMessage(config, targetOrigin);
  }
  if (state.avatar.popup && !state.avatar.popup.closed) {
    state.avatar.popup.postMessage(config, targetOrigin);
  }
}

function originFromUrl(url) {
  try {
    return new URL(url, window.location.href).origin;
  } catch {
    return "*";
  }
}

function avatarRuntimeUrl(url, modelUrl = "") {
  if (!url) return "";
  try {
    const runtimeUrl = new URL(url, window.location.href);
    if (modelUrl && !runtimeUrl.searchParams.has("model")) {
      runtimeUrl.searchParams.set("model", modelUrl);
    }
    if (!runtimeUrl.searchParams.has("events")) {
      runtimeUrl.searchParams.set("events", new URL("/api/events", window.location.href).toString());
    }
    if (!runtimeUrl.searchParams.has("background") && !runtimeUrl.searchParams.has("bg")) {
      runtimeUrl.searchParams.set("background", AVATAR_BACKGROUND_COLOR);
    }
    applyAvatarViewSettingsToUrl(runtimeUrl);
    return runtimeUrl.toString();
  } catch {
    return url;
  }
}

function loadAvatarViewSettings() {
  try {
    const raw = localStorage.getItem(AVATAR_VIEW_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_AVATAR_VIEW_SETTINGS };
    return normalizeAvatarViewSettings(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_AVATAR_VIEW_SETTINGS };
  }
}

function saveAvatarViewSettings(settings) {
  try {
    localStorage.setItem(AVATAR_VIEW_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Local storage is best-effort; live controls still work without persistence.
  }
}

function normalizeAvatarViewSettings(settings) {
  const source = settings || {};
  return {
    projection: source.projection === "orthographic" ? "orthographic" : "perspective",
    cameraDistance: clampNumber(source.cameraDistance, 0.8, 9.5, DEFAULT_AVATAR_VIEW_SETTINGS.cameraDistance),
    cameraFov: clampNumber(source.cameraFov, 12, 70, DEFAULT_AVATAR_VIEW_SETTINGS.cameraFov),
    orthographicWidth: clampNumber(source.orthographicWidth, 0.8, 4.8, DEFAULT_AVATAR_VIEW_SETTINGS.orthographicWidth),
    avatarHeight: clampNumber(source.avatarHeight, 0.6, 2.6, DEFAULT_AVATAR_VIEW_SETTINGS.avatarHeight),
    lightHeight: clampNumber(source.lightHeight, 0.2, 2.6, DEFAULT_AVATAR_VIEW_SETTINGS.lightHeight),
  };
}

function clampNumber(value, min, max, fallback) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return fallback;
  return Math.max(min, Math.min(max, numberValue));
}

function applyAvatarViewSettingsToUrl(runtimeUrl) {
  const settings = state.avatar.viewSettings || DEFAULT_AVATAR_VIEW_SETTINGS;
  runtimeUrl.searchParams.set("projection", settings.projection);
  runtimeUrl.searchParams.set("camera_distance", settings.cameraDistance.toFixed(2));
  runtimeUrl.searchParams.set("camera_fov", String(Math.round(settings.cameraFov)));
  runtimeUrl.searchParams.set("ortho_width", settings.orthographicWidth.toFixed(2));
  runtimeUrl.searchParams.set("avatar_height", settings.avatarHeight.toFixed(2));
  runtimeUrl.searchParams.set("light_height", settings.lightHeight.toFixed(2));
}

function initAvatarViewControls() {
  syncAvatarViewControls(state.avatar.viewSettings);
  $("avatarProjectionSelect").addEventListener("change", applyAvatarViewSettingsFromControls);
  for (const id of [
    "avatarCameraDistanceSlider",
    "avatarCameraFovSlider",
    "avatarOrthoWidthSlider",
    "avatarHeightSlider",
    "avatarLightHeightSlider",
  ]) {
    $(id).addEventListener("input", applyAvatarViewSettingsFromControls);
  }
  $("avatarViewResetButton").addEventListener("click", () => {
    state.avatar.viewSettings = { ...DEFAULT_AVATAR_VIEW_SETTINGS };
    saveAvatarViewSettings(state.avatar.viewSettings);
    syncAvatarViewControls(state.avatar.viewSettings);
    sendAvatarViewConfig();
  });
}

function applyAvatarViewSettingsFromControls() {
  state.avatar.viewSettings = normalizeAvatarViewSettings({
    projection: $("avatarProjectionSelect").value,
    cameraDistance: $("avatarCameraDistanceSlider").value,
    cameraFov: $("avatarCameraFovSlider").value,
    orthographicWidth: $("avatarOrthoWidthSlider").value,
    avatarHeight: $("avatarHeightSlider").value,
    lightHeight: $("avatarLightHeightSlider").value,
  });
  saveAvatarViewSettings(state.avatar.viewSettings);
  syncAvatarViewControls(state.avatar.viewSettings);
  sendAvatarViewConfig();
}

function syncAvatarViewControls(settings) {
  const viewSettings = normalizeAvatarViewSettings(settings);
  $("avatarProjectionSelect").value = viewSettings.projection;
  $("avatarCameraDistanceSlider").value = viewSettings.cameraDistance.toFixed(2);
  $("avatarCameraFovSlider").value = String(Math.round(viewSettings.cameraFov));
  $("avatarOrthoWidthSlider").value = viewSettings.orthographicWidth.toFixed(2);
  $("avatarHeightSlider").value = viewSettings.avatarHeight.toFixed(2);
  $("avatarLightHeightSlider").value = viewSettings.lightHeight.toFixed(2);
  text("avatarCameraDistanceReadout", viewSettings.cameraDistance.toFixed(2));
  text("avatarCameraFovReadout", `${Math.round(viewSettings.cameraFov)} deg`);
  text("avatarOrthoWidthReadout", viewSettings.orthographicWidth.toFixed(2));
  text("avatarHeightReadout", viewSettings.avatarHeight.toFixed(2));
  text("avatarLightHeightReadout", viewSettings.lightHeight.toFixed(2));
  $("avatarCameraFovRow").classList.toggle("is-hidden", viewSettings.projection !== "perspective");
  $("avatarOrthoWidthRow").classList.toggle("is-hidden", viewSettings.projection !== "orthographic");
}

function sendAvatarViewConfig() {
  state.avatar.lastConfigKey = "";
  if (!state.avatar.url) return;
  const nextUrl = avatarRuntimeUrl(state.avatar.url, "");
  state.avatar.url = nextUrl;
  text("avatarUrl", nextUrl);
  postAvatarConfig(nextUrl);
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

function collectEvents(gesture, voice, dify, tts) {
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

  const ttsKey = `${tts.updated_at || ""}:${tts.phase || ""}:${tts.request_id || ""}:${tts.error || ""}`;
  if (ttsKey && ttsKey !== state.previous.ttsKey && tts.available) {
    state.previous.ttsKey = ttsKey;
    const label = tts.phase === "error" ? `tts error: ${short(tts.error, 92)}` : `tts ${tts.phase || "updated"}`;
    addEvent(ttsKey, label);
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
      setPanel("ttsPanel", "bad", "ttsState", "auth");
      setPanel("avatarPanel", "bad", "avatarState", "auth");
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
    setPanel("ttsPanel", "bad", "ttsState", "error");
    setPanel("avatarPanel", "bad", "avatarState", "error");
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

async function applyTtsVolume() {
  const slider = $("ttsAppVolumeSlider");
  const appVolume = currentTtsSliderVolume();
  $("ttsVolumeApplyButton").disabled = true;
  text("ttsVolumeApplyState", "反映中...");
  try {
    const response = await fetch("/api/tts/volume", {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify({ app_volume: appVolume }),
    });
    if (response.status === 401) {
      text("ttsVolumeApplyState", "auth required");
      addEvent("auth-required-tts-volume", "auth token is required for TTS volume");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.ttsVolume.dirty = false;
    text("ttsVolumeApplyState", formatAppVolume(payload.app_volume));
    if (!$("ttsVolumePreviewButton").disabled) {
      await previewTtsVolume();
    }
    await refresh();
  } catch (error) {
    text("ttsVolumeApplyState", `error: ${error.message}`);
    state.ttsVolume.dirty = true;
    $("ttsVolumeApplyButton").disabled = false;
  }
}

function currentTtsSliderVolume() {
  const slider = $("ttsAppVolumeSlider");
  return Math.max(0, Math.min(100, Number(slider.value))) / 100;
}

async function previewTtsVolume() {
  const previewButton = $("ttsVolumePreviewButton");
  const appVolume = currentTtsSliderVolume();
  const wasDisabled = previewButton.disabled;
  if (wasDisabled) return;
  previewButton.disabled = true;
  text("ttsVolumeApplyState", "試聴中...");
  try {
    const response = await fetch("/api/tts/volume/preview", {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify({ app_volume: appVolume }),
    });
    if (response.status === 401) {
      text("ttsVolumeApplyState", "auth required");
      addEvent("auth-required-tts-preview", "auth token is required for TTS volume preview");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const previewVolume = payload.preview_volume ?? appVolume;
    text("ttsVolumeApplyState", `試聴 ${formatAppVolume(previewVolume)}`);
  } catch (error) {
    text("ttsVolumeApplyState", `preview error: ${error.message}`);
  } finally {
    previewButton.disabled = wasDisabled;
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
$("avatarFrame").addEventListener("load", () => {
  state.avatar.lastKey = "";
  state.avatar.lastConfigKey = "";
});
$("avatarOpenButton").addEventListener("click", () => {
  if (!state.avatar.url) return;
  state.avatar.popup = window.open(state.avatar.url, "swordVoiceAvatar");
  state.avatar.lastKey = "";
  state.avatar.lastConfigKey = "";
});
$("ttsAppVolumeSlider").addEventListener("input", () => {
  state.ttsVolume.dirty = true;
  text("ttsVolumeApplyState", `${$("ttsAppVolumeSlider").value}%`);
  $("ttsVolumeApplyButton").disabled = false;
});
$("ttsVolumeApplyButton").addEventListener("click", applyTtsVolume);
$("ttsVolumePreviewButton").addEventListener("click", previewTtsVolume);
initAvatarViewControls();
refresh();
setInterval(refresh, 1000);
