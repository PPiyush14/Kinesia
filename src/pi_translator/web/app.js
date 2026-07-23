const statePollIntervalMs = 2500;

const appState = {
  snapshot: null,
  bootstrap: {
    devices: { inputs: [], outputs: [], error: null, refreshed_at: null },
    languages: [],
  },
  draftSettings: {},
  busy: false,
  statePollInFlight: false,
  bootstrapPollInFlight: false,
  conversationPollInFlight: false,
  channelViews: {},
  lastConversationToken: "",
  lastConversationText: "",
  eventSource: null,
};

const channelsRoot = document.getElementById("channels-root");
const channelTemplate = document.getElementById("channel-template");
const sessionSummaryEl = document.getElementById("session-summary");
const sessionStatusEl = document.getElementById("session-status");
const sessionIdEl = document.getElementById("session-id");
const sessionTurnCountEl = document.getElementById("session-turn-count");
const conversationJsonEl = document.getElementById("conversation-json");
const systemLogsEl = document.getElementById("system-logs");

document.getElementById("refresh-devices-btn").addEventListener("click", () => {
  refreshDevices();
});
document.getElementById("new-session-btn").addEventListener("click", () => {
  postJson("/api/session/new");
});
document.getElementById("end-session-btn").addEventListener("click", () => {
  postJson("/api/session/end");
});
document.getElementById("start-all-btn").addEventListener("click", () => {
  postJson("/api/channels/start-all");
});
document.getElementById("stop-all-btn").addEventListener("click", () => {
  postJson("/api/channels/stop-all");
});

async function postJson(url, payload = {}, options = {}) {
  setBusy(true);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Request failed");
    }
    if (Array.isArray(options.clearDirtyKeys)) {
      for (const channelKey of options.clearDirtyKeys) {
        const draft = appState.draftSettings[channelKey];
        if (draft) {
          draft.dirty = false;
        }
      }
    }
    updateBootstrapFromSnapshot(data);
    applySnapshot(data);
    await refreshConversationIfNeeded(true);
  } catch (error) {
    window.alert(error.message);
  } finally {
    setBusy(false);
  }
}

async function fetchBootstrap() {
  if (appState.bootstrapPollInFlight) {
    return;
  }
  appState.bootstrapPollInFlight = true;
  try {
    const response = await fetch("/api/bootstrap", { cache: "no-store" });
    const data = await response.json();
    applyBootstrap(data);
  } catch (error) {
    console.error("Failed to fetch bootstrap", error);
  } finally {
    appState.bootstrapPollInFlight = false;
  }
}

async function fetchState() {
  if (appState.statePollInFlight) {
    return;
  }
  if (document.hidden) {
    return;
  }
  appState.statePollInFlight = true;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const data = await response.json();
    updateBootstrapFromSnapshot(data);
    applySnapshot(data);
    await refreshConversationIfNeeded(false);
  } catch (error) {
    console.error("Failed to fetch state", error);
  } finally {
    appState.statePollInFlight = false;
  }
}

async function refreshDevices() {
  setBusy(true);
  try {
    const response = await fetch("/api/devices/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Request failed");
    }
    applyBootstrap(data);
  } catch (error) {
    window.alert(error.message);
  } finally {
    setBusy(false);
  }
}

async function fetchConversation(force = false) {
  if (appState.conversationPollInFlight) {
    return;
  }
  const session = appState.snapshot?.session;
  if (!session?.id) {
    renderConversation({
      session_id: null,
      started_at: null,
      ended_at: null,
      updated_at: null,
      turns: [],
    });
    return;
  }

  const nextToken = `${session.id}:${session.conversation_updated_at || ""}`;
  if (!force && nextToken === appState.lastConversationToken) {
    return;
  }

  appState.conversationPollInFlight = true;
  try {
    const response = await fetch("/api/session/conversation", { cache: "no-store" });
    const data = await response.json();
    renderConversation(data);
    appState.lastConversationToken = nextToken;
  } catch (error) {
    console.error("Failed to fetch conversation", error);
  } finally {
    appState.conversationPollInFlight = false;
  }
}

function applySnapshot(snapshot) {
  mergeDraftSettings(snapshot);
  appState.snapshot = snapshot;
  renderSession(snapshot.session);
  ensureChannelCards(snapshot);
  renderChannels(snapshot);
  renderSystemLogs(snapshot.system_logs || []);
}

function applyBootstrap(data) {
  if (data?.devices) {
    appState.bootstrap.devices = data.devices;
  }
  if (Array.isArray(data?.languages)) {
    appState.bootstrap.languages = data.languages;
  }
  if (appState.snapshot) {
    renderChannels(appState.snapshot);
  }
}

function updateBootstrapFromSnapshot(snapshot) {
  if (snapshot?.devices) {
    appState.bootstrap.devices = snapshot.devices;
  }
  if (Array.isArray(snapshot?.languages)) {
    appState.bootstrap.languages = snapshot.languages;
  }
}

function mergeDraftSettings(snapshot) {
  if (!snapshot || !snapshot.channels) {
    return;
  }

  for (const [channelKey, channel] of Object.entries(snapshot.channels)) {
    const existing = appState.draftSettings[channelKey];
    const nextValues = {
      input_device: normalizeSelectValue(channel.input_device),
      output_device: normalizeSelectValue(channel.output_device),
      language_mode: channel.language_mode || "auto",
      language_code: channel.language_code || "en",
    };

    if (!existing) {
      appState.draftSettings[channelKey] = {
        ...nextValues,
        dirty: false,
      };
      continue;
    }

    if (!existing.dirty) {
      existing.input_device = nextValues.input_device;
      existing.output_device = nextValues.output_device;
      existing.language_mode = nextValues.language_mode;
      existing.language_code = nextValues.language_code;
    }
  }
}

function ensureChannelCards(snapshot) {
  for (const channelKey of Object.keys(snapshot.channels || {})) {
    if (appState.channelViews[channelKey]) {
      continue;
    }

    const fragment = channelTemplate.content.cloneNode(true);
    const card = fragment.querySelector(".channel-card");
    card.dataset.channelKey = channelKey;

    const view = {
      card,
      kicker: fragment.querySelector(".channel-kicker"),
      title: fragment.querySelector(".channel-title"),
      badge: fragment.querySelector(".status-badge"),
      meta: fragment.querySelector(".channel-meta"),
      inputSelect: fragment.querySelector(".input-device"),
      outputSelect: fragment.querySelector(".output-device"),
      modeSelect: fragment.querySelector(".language-mode"),
      languageSelect: fragment.querySelector(".language-code"),
      saveButton: fragment.querySelector(".save-channel-btn"),
      startButton: fragment.querySelector(".start-channel-btn"),
      stopButton: fragment.querySelector(".stop-channel-btn"),
      liveStatus: fragment.querySelector(".live-status"),
      liveTranscript: fragment.querySelector(".live-transcript-text"),
      lastResult: fragment.querySelector(".last-result"),
      logs: fragment.querySelector(".channel-logs"),
      deviceSignature: "",
      languageSignature: "",
      logSignature: "",
      resultSignature: "",
      metaSignature: "",
      liveSignature: "",
    };

    view.kicker.textContent = `Channel ${channelKey}`;
    view.title.textContent = snapshot.channels[channelKey].name;

    view.inputSelect.addEventListener("change", (event) => {
      updateDraft(channelKey, "input_device", event.target.value);
    });
    view.outputSelect.addEventListener("change", (event) => {
      updateDraft(channelKey, "output_device", event.target.value);
    });
    view.modeSelect.addEventListener("change", (event) => {
      updateDraft(channelKey, "language_mode", event.target.value);
    });
    view.languageSelect.addEventListener("change", (event) => {
      updateDraft(channelKey, "language_code", event.target.value);
    });

    view.saveButton.addEventListener("click", () => saveChannel(channelKey));
    view.startButton.addEventListener("click", () => {
      postJson(`/api/channels/${channelKey}/start`);
    });
    view.stopButton.addEventListener("click", () => {
      postJson(`/api/channels/${channelKey}/stop`);
    });

    appState.channelViews[channelKey] = view;
    channelsRoot.appendChild(fragment);
  }
}

function renderSession(session) {
  const turnCount = session?.conversation_turn_count ?? 0;
  sessionStatusEl.textContent = session?.is_active ? "Active" : "Ended / idle";
  sessionIdEl.textContent = session?.id || "-";
  sessionTurnCountEl.textContent = String(turnCount);

  sessionSummaryEl.innerHTML = `
    <p><strong>Session:</strong> ${escapeHtml(session?.id || "-")}</p>
    <p><strong>Status:</strong> ${session?.is_active ? "Active" : "Ended / idle"}</p>
    <p><strong>Started:</strong> ${formatTimestamp(session?.started_at)}</p>
    <p><strong>Last activity:</strong> ${formatTimestamp(session?.last_activity_at)}</p>
    <p><strong>Turns:</strong> ${turnCount}</p>
  `;
}

function renderChannels(snapshot) {
  const deviceSignature = buildDeviceSignature(appState.bootstrap.devices);
  const languageSignature = buildLanguageSignature(appState.bootstrap.languages);

  for (const [channelKey, channel] of Object.entries(snapshot.channels || {})) {
    const view = appState.channelViews[channelKey];
    const draft = appState.draftSettings[channelKey];
    if (!view || !draft) {
      continue;
    }

    updateChannelStatus(view, channel);
    updateChannelMeta(view, channel);
    updateChannelOptions(view, draft, deviceSignature, languageSignature);
    updateLiveTranscript(view, channel);
    updateLastResult(view, channel.last_result);
    renderLogs(view.logs, channel.logs || [], view);
  }
}

function updateChannelStatus(view, channel) {
  view.title.textContent = channel.name;
  view.badge.textContent = channel.running ? "Running" : "Stopped";
  view.badge.classList.toggle("running", channel.running);
  view.badge.classList.toggle("stopped", !channel.running);
}

function updateChannelMeta(view, channel) {
  const metaSignature = JSON.stringify({
    current_language: channel.current_language || "",
    has_learned_language: Boolean(channel.has_learned_language),
    last_error: channel.last_error || "",
    output_device: channel.output_device ?? "",
  });
  if (view.metaSignature === metaSignature) {
    return;
  }

  view.meta.innerHTML = `
    <div>
      <span class="meta-label">Current Output Language</span>
      <strong>${escapeHtml(channel.current_language || "-")}</strong>
    </div>
    <div>
      <span class="meta-label">Learned From Speech</span>
      <strong>${channel.has_learned_language ? "Yes" : "No"}</strong>
    </div>
    <div>
      <span class="meta-label">Last Error</span>
      <strong>${escapeHtml(channel.last_error || "None")}</strong>
    </div>
    <div>
      <span class="meta-label">TTS Output Route</span>
      <strong>${escapeHtml(String(channel.output_device ?? "Default device"))}</strong>
    </div>
  `;
  view.metaSignature = metaSignature;
}

function updateChannelOptions(
  view,
  draft,
  deviceSignature,
  languageSignature
) {
  const controlsFocused = isChannelControlFocused(view);

  if (!controlsFocused && view.deviceSignature !== deviceSignature) {
    populateDeviceSelect(view.inputSelect, appState.bootstrap.devices?.inputs || []);
    populateDeviceSelect(view.outputSelect, appState.bootstrap.devices?.outputs || []);
    view.deviceSignature = deviceSignature;
  }

  if (!controlsFocused && view.languageSignature !== languageSignature) {
    populateLanguageSelect(view.languageSelect, appState.bootstrap.languages || []);
    view.languageSignature = languageSignature;
  }

  if (!controlsFocused) {
    syncSelectValue(view.inputSelect, draft.input_device);
    syncSelectValue(view.outputSelect, draft.output_device);
    syncSelectValue(view.modeSelect, draft.language_mode || "auto");
    syncSelectValue(view.languageSelect, draft.language_code || "en");
  }
}

function updateLastResult(view, lastResult) {
  const resultSignature = JSON.stringify(lastResult || {});
  if (view.resultSignature === resultSignature) {
    return;
  }

  if (lastResult) {
    view.lastResult.innerHTML = `
      <p><strong>Last transcript:</strong> ${escapeHtml(lastResult.transcript)}</p>
      <p><strong>Last translation:</strong> ${escapeHtml(lastResult.translation)}</p>
      <p><strong>Latency:</strong> Gemma ${lastResult.gemma_latency_ms} ms, TTS ${lastResult.tts_latency_ms} ms, playback ${lastResult.playback_latency_ms} ms, total ${lastResult.turn_latency_ms} ms</p>
    `;
  } else {
    view.lastResult.innerHTML =
      '<p class="empty-state">No turns captured for this channel yet.</p>';
  }
  view.resultSignature = resultSignature;
}

function updateLiveTranscript(view, channel) {
  const liveStatus = channel.live_status || "stopped";
  const liveTranscript = channel.live_transcript || "";
  const liveSignature = `${liveStatus}:${liveTranscript}:${channel.live_updated_at || ""}`;
  if (view.liveSignature === liveSignature) {
    return;
  }

  view.liveStatus.textContent = formatLiveStatus(liveStatus);
  view.liveStatus.className = `live-status ${liveStatus}`;

  if (liveTranscript) {
    view.liveTranscript.textContent = liveTranscript;
    view.liveTranscript.classList.remove("empty-state");
  } else {
    view.liveTranscript.textContent = emptyTranscriptMessage(liveStatus);
    view.liveTranscript.classList.add("empty-state");
  }

  view.liveSignature = liveSignature;
}

function populateDeviceSelect(select, devices) {
  const previousValue = select.value;
  select.innerHTML = "";

  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Use current default device";
  select.appendChild(defaultOption);

  for (const device of devices) {
    const option = document.createElement("option");
    option.value = String(device.value);
    option.textContent = device.label;
    select.appendChild(option);
  }
  select.value = previousValue;
}

function populateLanguageSelect(select, languages) {
  const previousValue = select.value;
  select.innerHTML = "";
  for (const language of languages) {
    const option = document.createElement("option");
    option.value = language.code;
    option.textContent = language.label;
    select.appendChild(option);
  }
  select.value = previousValue;
}

function renderLogs(container, entries, view = null) {
  const signature = buildLogSignature(entries);
  if (view && view.logSignature === signature) {
    return;
  }
  if (container.dataset.logSignature === signature) {
    return;
  }

  container.innerHTML = "";
  if (!entries.length) {
    container.innerHTML = '<div class="empty-state">Waiting for live logs...</div>';
  } else {
    for (const entry of entries.slice().reverse()) {
      const item = document.createElement("article");
      item.className = `log-item ${entry.level || "info"}`;
      const details =
        entry.data && Object.keys(entry.data).length
          ? `<pre>${escapeHtml(JSON.stringify(entry.data, null, 2))}</pre>`
          : "";
      item.innerHTML = `
        <div class="log-item-header">
          <span>${escapeHtml(entry.time_label || "-")}</span>
          <span>${escapeHtml((entry.level || "info").toUpperCase())}</span>
        </div>
        <div>${escapeHtml(entry.message || "")}</div>
        ${details}
      `;
      container.appendChild(item);
    }
  }

  container.dataset.logSignature = signature;
  if (view) {
    view.logSignature = signature;
  }
}

function renderSystemLogs(entries) {
  renderLogs(systemLogsEl, entries || []);
}

function renderConversation(conversation) {
  const nextText = JSON.stringify(conversation, null, 2);
  if (nextText === appState.lastConversationText) {
    return;
  }
  conversationJsonEl.textContent = nextText;
  appState.lastConversationText = nextText;
}

async function refreshConversationIfNeeded(force) {
  const session = appState.snapshot?.session;
  if (!session?.id) {
    renderConversation({
      session_id: null,
      started_at: null,
      ended_at: null,
      updated_at: null,
      turns: [],
    });
    appState.lastConversationToken = "";
    return;
  }
  await fetchConversation(force);
}

function saveChannel(channelKey) {
  const draft = appState.draftSettings[channelKey];
  postJson(
    "/api/settings",
    {
      channels: {
        [channelKey]: {
          input_device: normalizeOutgoingValue(draft.input_device),
          output_device: normalizeOutgoingValue(draft.output_device),
          language_mode: draft.language_mode,
          language_code: draft.language_code,
        },
      },
    },
    { clearDirtyKeys: [channelKey] }
  );
}

function updateDraft(channelKey, field, value) {
  const draft = appState.draftSettings[channelKey];
  if (!draft) {
    return;
  }
  draft[field] = value;
  draft.dirty = true;
}

function setBusy(value) {
  appState.busy = value;
  for (const button of document.querySelectorAll("button")) {
    button.disabled = value;
  }
}

function connectEventStream() {
  if (appState.eventSource) {
    return;
  }

  const source = new EventSource("/api/events");
  appState.eventSource = source;

  source.addEventListener("channel-live", (event) => {
    try {
      const data = JSON.parse(event.data);
      const channelKey = data.channel_key;
      if (!channelKey) {
        return;
      }
      const snapshotChannel = appState.snapshot?.channels?.[channelKey];
      if (snapshotChannel) {
        snapshotChannel.live_status = data.live_status || "stopped";
        snapshotChannel.live_transcript = data.live_transcript || "";
        snapshotChannel.live_updated_at = data.live_updated_at || null;
      }
      const view = appState.channelViews[channelKey];
      if (view) {
        updateLiveTranscript(view, snapshotChannel || data);
      }
    } catch (error) {
      console.error("Failed to parse live transcript event", error);
    }
  });

  source.addEventListener("refresh", () => {
    fetchState();
  });

  source.onerror = () => {
    if (appState.eventSource !== source) {
      return;
    }
    source.close();
    appState.eventSource = null;
    window.setTimeout(connectEventStream, 1500);
  };
}

function isChannelControlFocused(view) {
  const active = document.activeElement;
  return (
    active === view.inputSelect ||
    active === view.outputSelect ||
    active === view.modeSelect ||
    active === view.languageSelect
  );
}

function syncSelectValue(select, value) {
  const nextValue = normalizeSelectValue(value);
  if (select.value !== nextValue) {
    select.value = nextValue;
  }
}

function buildDeviceSignature(devices) {
  const inputs = (devices?.inputs || []).map((item) => item.value).join(",");
  const outputs = (devices?.outputs || []).map((item) => item.value).join(",");
  return `${inputs}|${outputs}|${devices?.refreshed_at || ""}`;
}

function buildLanguageSignature(languages) {
  return (languages || []).map((item) => item.code).join(",");
}

function buildLogSignature(entries) {
  if (!entries.length) {
    return "0";
  }
  const last = entries[entries.length - 1];
  return `${entries.length}:${last.timestamp || ""}:${last.message || ""}`;
}

function normalizeSelectValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function normalizeOutgoingValue(value) {
  const text = normalizeSelectValue(value).trim();
  if (!text) {
    return "";
  }
  return text;
}

function formatTimestamp(value) {
  if (!value) {
    return "-";
  }
  return new Date(value * 1000).toLocaleString();
}

function formatLiveStatus(value) {
  const normalized = String(value || "stopped");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function emptyTranscriptMessage(status) {
  switch (status) {
    case "listening":
      return "Listening for speech...";
    case "processing":
      return "Processing the latest phrase...";
    case "error":
      return "Live transcript paused because the channel hit an error.";
    case "speaking":
      return "Speech detected. Waiting for interim words...";
    case "stopped":
    default:
      return "Start the channel to see live speech here.";
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

fetchBootstrap();
fetchState();
connectEventStream();
window.setInterval(fetchState, statePollIntervalMs);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    return;
  }
  fetchState();
  connectEventStream();
});
window.addEventListener("beforeunload", () => {
  if (appState.eventSource) {
    appState.eventSource.close();
    appState.eventSource = null;
  }
});
