"use strict";

const $ = (id) => document.getElementById(id);
const sessionKey = "eatventure.dashboard.token";
const adbAddressKey = "eatventure.dashboard.adbAddress";
const themeKey = "eatventure.dashboard.theme";
const visionDetailsKey = "eatventure.dashboard.visionDetails";
const localeKey = "eatventure.dashboard.locale";
let locale = readLocale();
let numberFormat = new Intl.NumberFormat(locale);
let messages = JSON.parse($("locale-catalog").textContent);
const state = { devices: [], schema: [], templates: { total: 0, categories: [] }, training: {}, models: [] };
const selected = new Set();
const busyDevices = new Set();
let token = readToken();
let authenticated = false;
let authRequired = true;
let stateLoading = false;
let runtimeLoading = false;
let deviceRefreshGeneration = 0;
let deviceAppliedGeneration = 0;
let profileLoading = false;
let detailId = null;
let draft = null;
let savedProfile = null;
let activeTab = "settings";
let saving = false;
let logPaused = false;
let logCursor = 0;
let logLines = [];
let logBytes = 0;
let logLoading = false;
let logGeneration = 0;
let detailSession = null;
let visionWanted = false;
let visionDevice = null;
let visionGeneration = 0;
let visionController = null;
let visionStream = null;
let visionUrl = null;
let visionMode = "off";
let visionResume = false;
let templateSelection = null;
let templateStart = null;
let templateDevice = null;
let templatePointerId = null;
let templateRetry = null;
let templateSnapshot = null;
let templateMode = "template";
let templateBlockSelections = [];
let templateBlocksLoaded = false;
let templateBlockEdited = false;
let visionShowDetails = true;
let sessionLoading = false;
let datasetSession = null;
let datasetResolution = null;
let datasetOffset = 0;
let datasetTotal = 0;
let datasetGeneration = 0;
let datasetUrls = [];
let datasetPageLoading = false;
let trainingData = null;
let trainingLoading = false;
let trainingSubmitting = false;
let trainingSettingsDirty = false;
let trainingSettingsSnapshot = "";
let trainingGeneration = 0;
const visionReleases = new Map();
const MAX_VISION_FRAME_BYTES = 5 * 1024 * 1024;
const MAX_VISION_HEADER_BYTES = 8 * 1024;
const MAX_LOG_LINES = 500;
const MAX_LOG_BYTES = 512 * 1024;
const MAX_LOG_LINE_BYTES = 4 * 1024;

function t(key, values = {}) {
  const [surface, name] = key.split(".", 2);
  const text = messages[surface]?.[name];
  if (typeof text !== "string") throw new Error(messages.web.missing_locale_key.replace("{key}", key));
  return text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? `{${name}}`);
}

async function loadMessages() {
  const language = locale;
  const response = await fetch(`/locales/${encodeURIComponent(language)}.json`, { cache: "no-store" });
  if (!response.ok) throw new Error(t("web.locale_load_error"));
  const catalog = await response.json();
  if (language !== locale) return false;
  messages = catalog;
  document.documentElement.lang = language;
  numberFormat = new Intl.NumberFormat(language);
  translateStaticContent();
  return true;
}

function translateStaticContent() {
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  for (const attribute of ["aria-label", "title", "placeholder", "alt"]) {
    document.querySelectorAll(`[data-i18n-${attribute}]`).forEach((element) => {
      element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
    });
  }
}

function displayCode(surface, code) {
  return Object.hasOwn(messages[surface] || {}, code) ? t(`${surface}.${code}`) : code;
}

function readLocale() {
  try { const value = localStorage.getItem(localeKey) || document.documentElement.lang; return value === "en-US" ? "en-US" : "pt-BR"; }
  catch { return document.documentElement.lang || "pt-BR"; }
}

function renderLanguageSwitch() {
  for (const button of $("language-switch").querySelectorAll("[data-locale]")) {
    button.setAttribute("aria-pressed", String(button.dataset.locale === locale));
  }
}

async function setLocale(value) {
  const previous = document.documentElement.lang;
  locale = value === "en-US" ? "en-US" : "pt-BR";
  try {
    if (!await loadMessages()) return;
  } catch {
    locale = previous;
    renderLanguageSwitch();
    toast(t("web.locale_load_error"), true);
    return;
  }
  try { localStorage.setItem(localeKey, locale); } catch { /* A escolha permanece nesta página. */ }
  renderLanguageSwitch();
  renderDevices();
  setTheme(document.documentElement.dataset.theme);
  if (detailId) {
    renderProfile();
    renderItems();
    renderBlockRegions();
    renderDetailStatus();
    updatePending();
    renderVision(visionMode);
    renderLogLines();
    $("log-pause").textContent = logPaused ? t("web.resume") : t("web.pause");
  }
  if ($("training-dialog").open && trainingData) renderTraining();
  if (authenticated) await refreshState();
}

function readToken() {
  try { return sessionStorage.getItem(sessionKey) || ""; } catch { return ""; }
}

function rememberToken(value) {
  token = value;
  try {
    if (value) sessionStorage.setItem(sessionKey, value);
    else sessionStorage.removeItem(sessionKey);
  } catch { /* A sessão continua em memória se o armazenamento estiver indisponível. */ }
}

function rememberAdbAddress(value) {
  try {
    if (value) localStorage.setItem(adbAddressKey, value);
  } catch { /* A conexão continua disponível sem armazenamento local. */ }
}

function setTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const toggle = $("theme-toggle");
  toggle.setAttribute("aria-pressed", String(dark));
  toggle.textContent = dark ? "☀" : "☾";
  const label = dark ? t("web.enable_light_theme") : t("web.enable_dark_theme");
  toggle.setAttribute("aria-label", label);
  toggle.title = label;
  try { localStorage.setItem(themeKey, dark ? "dark" : "light"); } catch { /* O tema permanece nesta página. */ }
}

function restoreTheme() {
  try { setTheme(localStorage.getItem(themeKey) || "light"); }
  catch { setTheme("light"); }
}

function restoreVisionDetails() {
  try { visionShowDetails = localStorage.getItem(visionDetailsKey) !== "false"; } catch { visionShowDetails = true; }
  $("vision-details").checked = visionShowDetails;
}

function setVisionDetails(value) {
  visionShowDetails = !!value;
  $("vision-details").checked = visionShowDetails;
  try { localStorage.setItem(visionDetailsKey, String(visionShowDetails)); } catch { /* A opção permanece nesta página. */ }
}

function lastAdbAddress() {
  try {
    const saved = localStorage.getItem(adbAddressKey);
    if (saved) return saved;
  } catch { /* Usa o endereço disponível no estado atual. */ }
  return state.devices.find((device) => device.connected && device.connection === "wifi" && device.serial)?.serial || "";
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}

function button(text, className, action, title) {
  const result = node("button", className, text);
  result.type = "button";
  if (title) result.title = title;
  result.addEventListener("click", action);
  return result;
}

function toast(message, error = false) {
  const item = node("div", `toast${error ? " error" : ""}`, message);
  $("toasts").append(item);
  setTimeout(() => item.remove(), 6000);
}

function showError(id, message) {
  $(id).textContent = message;
  $(id).hidden = !message;
}

function logout(expired = false) {
  if ($("training-dialog").open) closeTraining(true);
  if ($("dataset-dialog").open) closeDatasets();
  closeTemplateSelector();
  stopVision();
  authenticated = false;
  rememberToken("");
  selected.clear();
  detailId = null;
  draft = null;
  resetLogs();
  $("device-dialog").close();
  $("connect-dialog").close();
  $("app").hidden = true;
  $("login").hidden = false;
  $("session-loading").hidden = true;
  $("login-form").hidden = false;
  $("token").value = "";
  $("login-error").textContent = expired ? t("web.session_expired") : "";
  $("token").focus();
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === "/api/actions" ? 120000 : 45000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", "X-Eatventure-Locale": locale, ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
      signal: controller.signal,
      cache: "no-store"
    });
    if (response.status === 401) {
      if (authenticated) logout(true);
      throw new Error(t("web.invalid_token"));
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail || body.error || t("web.server_status", { status: response.status });
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(t("web.server_timeout"));
    if (error instanceof TypeError) throw new Error(t("web.server_unavailable"));
    throw error;
  } finally { clearTimeout(timeout); }
}

async function apiBlob(path) {
  const response = await fetch(path, {
    headers: { "X-Eatventure-Locale": locale, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    cache: "no-store"
  });
  if (response.status === 401) { logout(true); throw new Error(t("web.invalid_token")); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || t("web.server_status", { status: response.status }));
  }
  return response.blob();
}

async function login(event) {
  event?.preventDefault();
  const submit = $("login-form").querySelector("button[type=submit]");
  const value = event ? $("token").value.trim() : token;
  if (!value) return;
  rememberToken(value);
  submit.disabled = true;
  $("token").disabled = true;
  $("login-error").textContent = "";
  try {
    const session = await api("/api/session");
    if (!session.authenticated) throw new Error(t("web.invalid_token"));
    await enterApp(session);
  } catch (error) {
    rememberToken("");
    $("login-error").textContent = error.message;
  } finally { submit.disabled = false; $("token").disabled = false; }
}

async function enterApp(session) {
  authRequired = session.auth_required !== false;
  if (!authRequired) rememberToken("");
  authenticated = true;
  $("token").value = "";
  $("login").hidden = true;
  $("app").hidden = false;
  $("logout").hidden = !authRequired;
  await refreshState(true);
}

async function bootstrapSession() {
  if (sessionLoading) return;
  sessionLoading = true;
  $("login").hidden = false;
  $("login-form").hidden = true;
  $("session-loading").hidden = false;
  $("session-spinner").hidden = false;
  $("session-retry").hidden = true;
  $("session-status").textContent = t("web.connecting");
  try {
    const session = await api("/api/session");
    if (session.authenticated || session.auth_required === false) await enterApp(session);
    else {
      const invalid = !!token;
      rememberToken("");
      $("session-loading").hidden = true;
      $("login-form").hidden = false;
      $("login-error").textContent = invalid ? t("web.invalid_token_retry") : "";
      $("token").focus();
    }
  } catch (error) {
    $("session-status").textContent = error.message;
    $("session-spinner").hidden = true;
    $("session-retry").hidden = false;
  } finally { sessionLoading = false; }
}

function deviceById(id) { return state.devices.find((device) => device.id === id); }
function deviceName(device) { return device.name || device.profile?.name || device.model || device.serial || device.id; }
function connected(device) { return device.connected === true; }
function statusOf(device) {
  if (device.status === "stopping") return { key: "stopping", label: t("web.stopping") };
  if (device.running) return { key: "running", label: t("web.running") };
  if (device.adb_state === "unauthorized") return { key: "error", label: t("web.authorize_android") };
  if (device.status === "error") return { key: "error", label: t("web.stopped_with_error") };
  if (device.status === "stopped" && device.session_id) return { key: "stopped", label: t("web.stopped_status") };
  if (connected(device)) return { key: "ready", label: t("web.ready_to_start") };
  return { key: "offline", label: t("web.disconnected_singular") };
}

function badge(device) {
  const status = statusOf(device);
  const result = node("span", `status-badge ${status.key}`);
  result.append(node("span", "mini-dot"), document.createTextNode(status.label));
  return result;
}

function metric(device, key) {
  const metrics = device.metrics || {};
  const value = metrics[key] ?? metrics.summary?.[key];
  return value === null || value === undefined ? null : value;
}

function datasetResolutionOf(device) {
  const resolution = metric(device, "resolution");
  return typeof resolution === "string" && /^[1-9]\d*x[1-9]\d*$/.test(resolution) ? resolution : null;
}

function numeric(value) { return Number.isFinite(Number(value)) && value !== null && value !== "" ? Number(value) : null; }
function count(value) { const valueNumber = numeric(value); return valueNumber === null ? "—" : numberFormat.format(valueNumber); }

function runtime(device) {
  if (!device.running) return device.exit_code === 0 ? t("web.stopped") : device.running === false && device.exit_code != null ? t("web.exit_code", { value_1: device.exit_code }) : t("web.waiting");
  let start = device.started_at;
  if (typeof start === "string") start = new Date(start).getTime();
  else if (typeof start === "number" && start < 1e12) start *= 1000;
  if (!start || !Number.isFinite(start)) return device.pid ? t("web.pid", { pid: device.pid }) : t("web.active");
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return hours ? t("web.runtime_hours", { hours, minutes }) : minutes ? t("web.runtime_minutes", { minutes }) : t("web.runtime_seconds", { seconds });
}

function visibleDevices() {
  const search = $("search").value.trim().toLocaleLowerCase(locale);
  const filter = $("status-filter").value;
  return state.devices.filter((device) => {
    const text = [deviceName(device), device.model, device.serial, device.id, ...(device.aliases || [])].join(" ").toLocaleLowerCase(locale);
    const matchesStatus = filter === "all" || (filter === "running" && device.running) || (filter === "ready" && connected(device) && !device.running) || (filter === "offline" && !connected(device) && !device.running);
    return (!search || text.includes(search)) && matchesStatus;
  });
}

function deviceLayout(device) {
  return [device.id, device.serial, device.name, device.model, device.connected, device.running, device.session_id, device.status, device.adb_state, device.exit_code, device.profile];
}

async function refreshRuntime() {
  if (!authenticated || runtimeLoading) return;
  runtimeLoading = true;
  const generation = ++deviceRefreshGeneration;
  try {
    const result = await api("/api/runtime");
    if (!authenticated || !Array.isArray(result.devices) || generation < deviceAppliedGeneration) return;
    deviceAppliedGeneration = generation;
    const changed = JSON.stringify(state.devices.map(deviceLayout)) !== JSON.stringify(result.devices.map(deviceLayout));
    state.devices = result.devices;
    for (const id of selected) if (!deviceById(id)) selected.delete(id);
    if (changed) renderDevices();
    else {
      renderSummary(visibleDevices());
      for (const card of $("devices").children) {
        const device = deviceById(card.dataset.deviceId);
        if (device) updateDeviceMetrics(card, device);
      }
    }
    renderDetailStatus();
  } catch (error) { if (authenticated) showError("state-error", error.message); }
  finally { runtimeLoading = false; }
}

async function refreshState(force = false) {
  if (!authenticated || stateLoading) return;
  stateLoading = true;
  const generation = ++deviceRefreshGeneration;
  $("refresh").disabled = true;
  try {
    const result = await api(`/api/state${force ? "?refresh=true" : ""}`);
    if (!authenticated || generation < deviceAppliedGeneration) return;
    deviceAppliedGeneration = generation;
    state.devices = Array.isArray(result.devices) ? result.devices : [];
    state.schema = Array.isArray(result.schema) ? result.schema : [];
    state.templates = result.templates || state.templates;
    state.models = Array.isArray(result.models) ? result.models : [];
    state.training = result.training || {};
    state.collected = result.collected || {};
    renderTemplateCategorySuggestions();
    for (const id of selected) if (!deviceById(id)) selected.delete(id);
    showError("state-error", result.error || "");
    renderDevices();
    renderDetailStatus();
  } catch (error) {
    if (!authenticated) return;
    showError("state-error", error.message);
    if (!state.devices.length) renderEmpty(t("web.load_failed"), t("web.refresh_to_retry"), "!");
  } finally {
    stateLoading = false;
    $("refresh").disabled = false;
  }
}

function renderEmpty(title, message, symbol = "▤") {
  const empty = node("div", "empty-state");
  empty.append(node("span", "empty-symbol", symbol), node("h2", "", title), node("p", "", message));
  $("devices").replaceChildren(empty);
}

function renderSummary(visible) {
  const running = state.devices.filter((device) => device.running).length;
  $("kpi-running").textContent = count(running);
  $("kpi-running-detail").textContent = t("web.registered_devices", { count: count(state.devices.length) });
  $("kpi-connected").textContent = count(state.devices.filter(connected).length);
  $("device-count").textContent = t("web.device_count", { count: count(visible.length) });
}

function renderTemplateCategorySuggestions() {
  const list = $("template-categories");
  list.replaceChildren(...(state.templates.categories || []).map((category) => {
    const option = document.createElement("option");
    option.value = category.name;
    return option;
  }));
}

function renderDevices() {
  const visible = visibleDevices();
  renderSummary(visible);
  if (!visible.length) {
    renderEmpty(state.devices.length ? t("web.no_results") : t("web.first_device_title"), state.devices.length ? t("web.adjust_filters") : t("web.connect_device_hint"));
  } else {
    const focused = document.activeElement;
    const focusedId = focused?.closest(".device-card")?.dataset.deviceId;
    const focusedRole = focused?.dataset.role;
    $("devices").replaceChildren(...visible.map(renderDevice));
    if (focusedId && focusedRole) {
      const card = [...$("devices").children].find((element) => element.dataset.deviceId === focusedId);
      card?.querySelector(`[data-role="${focusedRole}"]`)?.focus({ preventScroll: true });
    }
  }
  updateSelection();
}

function renderDevice(device) {
  const card = node("article", `device-card${device.running ? " running" : ""}${selected.has(device.id) ? " selected" : ""}`);
  card.dataset.deviceId = device.id;
  const open = button("", "card-open-button", () => openDetails(device.id, "vision"), t("web.open_ai_vision"));
  open.dataset.role = "vision";
  open.setAttribute("aria-label", t("web.open_device_vision", { value_1: deviceName(device) }));
  open.setAttribute("aria-haspopup", "dialog");
  card.append(open);
  const header = node("div", "device-card-header");
  const identity = node("div", "device-identity");
  const title = node("h3", "", deviceName(device));
  title.title = deviceName(device);
  const serial = node("p", "mono", device.serial || device.id);
  serial.title = device.serial || device.id;
  identity.append(title, serial, node("span", "card-open-hint", t("web.view_ai_vision")));
  const checkbox = node("input");
  checkbox.type = "checkbox";
  checkbox.checked = selected.has(device.id);
  checkbox.dataset.role = "select";
  checkbox.setAttribute("aria-label", t("web.select_device", { value_1: deviceName(device) }));
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selected.add(device.id); else selected.delete(device.id);
    card.classList.toggle("selected", checkbox.checked);
    updateSelection();
  });
  header.append(node("span", "device-icon", "📱"), identity, checkbox);
  const status = node("div", "device-status-row");
  status.append(badge(device), node("span", "device-runtime", runtime(device)));
  const metrics = node("dl", "device-metrics");
  for (const [key, label, value] of metricValues(device)) {
    const item = node("div", "device-metric");
    const output = node("dd", key === "templates" ? "accent" : "");
    output.dataset.metric = key;
    setMetricValue(output, value);
    item.append(node("dt", "", label), output);
    metrics.append(item);
  }
  const footer = node("div", "device-card-footer");
  const busy = busyDevices.has(device.id) || device.status === "stopping";
  const start = button(busy ? t("web.please_wait") : device.running ? t("web.stop") : t("web.start"), `button ${device.running ? "ghost danger-text" : "primary"} start-button`, () => executeAction(device.running ? "stop" : "start", [device.id]));
  start.dataset.role = "start";
  start.disabled = busy || (!device.running && !connected(device));
  const restart = button("↻", "button warning restart-button", () => executeAction("restart", [device.id]), t("web.restart_bot"));
  restart.setAttribute("aria-label", t("web.restart_device", { value_1: deviceName(device) }));
  restart.dataset.role = "restart";
  restart.disabled = busy || !device.running || !connected(device);
  const settings = button("⚙", "button ghost settings-button", () => openDetails(device.id, "settings"), t("web.device_settings_hint"));
  settings.setAttribute("aria-label", t("web.configure_device", { value_1: deviceName(device) }));
  settings.dataset.role = "settings";
  const resolution = datasetResolutionOf(device);
  const dataset = button(t("web.data_button"), "button ghost dataset-button", () => {
    const current = deviceById(device.id) || device;
    openDatasets(current.session_id, datasetResolutionOf(current));
  }, t("web.view_session_datasets"));
  dataset.setAttribute("aria-label", t("web.view_device_datasets", { value_1: deviceName(device) }));
  dataset.disabled = !resolution && !device.session_id;
  if (!resolution) dataset.title = t("web.last_session_resolution_hint");
  dataset.dataset.role = "dataset";
  footer.append(start, restart, dataset, settings);
  card.append(header, status);
  card.append(node("p", "bot-state"), node("p", "metrics-freshness"));
  card.append(metrics, footer);
  const profile = device.profile;
  if (profile && (Object.keys(profile.settings || {}).length || Object.keys(profile.category_thresholds || {}).length || Object.keys(profile.item_thresholds || {}).length)) card.append(node("div", "profile-indicator", t("web.custom_profile")));
  updateDeviceMetrics(card, device);
  return card;
}

function metricValues(device) {
  const battery = metric(device, "battery");
  const level = numeric(typeof battery === "object" && battery ? battery.level : battery);
  const resolution = metric(device, "resolution");
  const trainings = resolution ? state.training[resolution] : null;
  const collected = metric(device, "dados_coletados") ?? (resolution ? state.collected?.[resolution] : 0);
  return [
    ["dados_coletados", t("web.collected_data"), count(collected || 0)],
    ["dados_sessao", t("web.session_data"), count(metric(device, "dados_sessao"))],
    ["treinamentos", t("web.training_runs"), trainings === null ? "—" : count(trainings)],
    ["voos", t("web.flights"), eventMetric(device, "voos")],
    ["reformas", t("web.renovations"), eventMetric(device, "reformas")],
    ["battery", t("web.battery"), level === null ? "—" : `${level}%${battery?.charging ? " ↯" : ""}`]
  ];
}

function eventMetric(device, key) {
  const total = metric(device, `${key}_total`) ?? metric(device, key);
  const delta = numeric(metric(device, `${key}_delta`));
  if (!(delta > 0)) return count(total);
  return { total: count(total), delta: count(delta) };
}

function setMetricValue(output, value) {
  if (!value || typeof value !== "object") {
    output.textContent = value;
    return;
  }
  output.replaceChildren(document.createTextNode(value.total));
  if (value.delta) output.append(document.createTextNode(" "), node("span", "metric-delta", `(+${value.delta})`));
}

function updateDeviceMetrics(card, device) {
  for (const [key, , value] of metricValues(device)) setMetricValue(card.querySelector(`[data-metric="${key}"]`), value);
  const dataset = card.querySelector(".dataset-button");
  const resolution = datasetResolutionOf(device);
  dataset.disabled = !resolution && !device.session_id;
  dataset.title = resolution ? t("web.view_session_datasets") : t("web.last_session_resolution_hint");
  card.querySelector(".device-runtime").textContent = runtime(device);
  const botState = metric(device, "state") || metric(device, "estado");
  const stateLabel = card.querySelector(".bot-state");
  stateLabel.textContent = botState ? t("web.state_label", { value_1: displayCode("states", botState) }) : "";
  stateLabel.hidden = !botState || !device.running;
  const updatedAt = numeric(metric(device, "updated_at"));
  const age = updatedAt === null ? null : Math.max(0, Math.floor(Date.now() / 1000 - updatedAt));
  const freshness = card.querySelector(".metrics-freshness");
  freshness.textContent = !device.running ? "" : age === null ? t("web.waiting_metrics") : age > 10 ? t("web.stale_metrics", { value_1: age }) : "";
  freshness.hidden = !freshness.textContent;
}

function updateSelection() {
  const visible = visibleDevices();
  const checked = visible.filter((device) => selected.has(device.id)).length;
  $("select-all").checked = visible.length > 0 && checked === visible.length;
  $("select-all").indeterminate = checked > 0 && checked < visible.length;
  $("select-all").disabled = visible.length === 0;
  $("selection-label").textContent = selected.size ? t(selected.size === 1 ? "web.selected_one" : "web.selected_other", { count: count(selected.size) }) : t("web.select_all");
  const eligible = state.devices.filter((device) => selected.has(device.id) && !busyDevices.has(device.id) && device.status !== "stopping");
  $("bulk-start").disabled = !eligible.some((device) => connected(device) && !device.running);
  $("bulk-restart").disabled = !eligible.some((device) => connected(device) && device.running);
  $("bulk-stop").disabled = !eligible.some((device) => device.running);
}

async function executeAction(action, ids) {
  const eligible = ids.filter((id) => {
    const device = deviceById(id);
    return device && !busyDevices.has(id) && device.status !== "stopping" && (action === "stop" ? device.running : connected(device) && (action === "restart" ? device.running : !device.running));
  });
  if (!eligible.length) return;
  const running = state.devices.filter((device) => device.running).length;
  if (action === "stop" && eligible.length > 1 && eligible.length === running && !confirm(t("web.confirm_stop_all", { value_1: eligible.length }))) return;
  if (action !== "stop" && detailId && eligible.includes(detailId) && changeCount()) {
    toast(t("web.save_before_start"), true);
    return;
  }
  if (action === "stop" && visionDevice && eligible.includes(visionDevice)) {
    stopVision({ mode: "stopped", message: t("web.bot_stopping") });
  }
  eligible.forEach((id) => busyDevices.add(id));
  renderDevices();
  try {
    const result = eligible.length === 1
      ? await api(`/api/devices/${encodeURIComponent(eligible[0])}/${action}`, { method: "POST" })
      : await api("/api/actions", { method: "POST", body: JSON.stringify({ action, device_ids: eligible }) });
    const failures = (result.results || []).filter((item) => item.error || item.ok === false);
    if (failures.length) toast(t("web.action_failures", { count: failures.length, errors: failures.map((item) => item.error || item.message).join("; ") }), true);
    else toast(action === "start" ? t("web.start_sent") : action === "stop" ? t("web.stopped_toast") : t("web.restart_sent"));
    await refreshState();
  } catch (error) { if (authenticated) toast(error.message, true); }
  finally { eligible.forEach((id) => busyDevices.delete(id)); renderDevices(); }
}

function normalizeProfile(profile = {}) {
  return {
    device_id: profile.device_id || detailId, name: profile.name || "",
    settings: { ...profile.settings }, category_thresholds: { ...profile.category_thresholds },
    item_thresholds: { ...profile.item_thresholds },
    block_regions: Array.isArray(profile.block_regions) ? profile.block_regions.map((region) => [...region]) : [],
    run: {
      ai_collect: !!profile.run?.ai_collect, layout_only: !!profile.run?.layout_only,
      save_log: !!profile.run?.save_log, detector_mode: profile.run?.detector_mode || "templates",
      ai_model: profile.run?.ai_model || "auto", ai_source: profile.run?.ai_source || "templates",
      ai_min_confidence: profile.run?.ai_min_confidence ?? 0.60
    }
  };
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function own(object, key) { return Object.prototype.hasOwnProperty.call(object, key); }

function changeCount() {
  if (!draft || !savedProfile) return 0;
  let changes = draft.name !== savedProfile.name ? 1 : 0;
  for (const section of ["settings", "category_thresholds", "item_thresholds", "run"]) {
    const keys = new Set([...Object.keys(draft[section]), ...Object.keys(savedProfile[section])]);
    for (const key of keys) if (draft[section][key] !== savedProfile[section][key]) changes += 1;
  }
  if (JSON.stringify(draft.block_regions) !== JSON.stringify(savedProfile.block_regions)) changes += 1;
  return changes;
}

function updatePending() {
  const changes = changeCount();
  $("pending-count").textContent = changes ? t(changes === 1 ? "web.pending_one" : "web.pending_other", { count: count(changes) }) : t("web.no_changes");
  const invalid = [...$("profile-fields").querySelectorAll("input,select"), ...$("item-fields").querySelectorAll("input")].some((input) => !input.disabled && !input.checkValidity());
  $("save-profile").disabled = !changes || invalid || saving;
  $("reset-profile").disabled = !draft || saving;
  $("detail-close").disabled = saving;
  $("profile-fields").inert = saving;
  $("item-fields").inert = saving;
  $("reset-thresholds").disabled = saving;
  $("item-search").disabled = saving;
}

async function openDetails(id, tab = "vision") {
  if (profileLoading || !await closeDetails()) return;
  profileLoading = true;
  detailId = id;
  try {
    const profile = await api(`/api/devices/${encodeURIComponent(id)}/profile`);
    if (!authenticated) return;
    savedProfile = normalizeProfile(profile);
    draft = clone(savedProfile);
    $("detail-title").textContent = deviceName(deviceById(id) || { id });
    $("detail-serial").textContent = deviceById(id)?.serial || id;
    showError("profile-error", "");
    $("item-search").value = "";
    renderProfile();
    renderItems();
    renderBlockRegions();
    resetLogs();
    detailSession = deviceById(id)?.started_at || null;
    updatePending();
    $("device-dialog").showModal();
    setTab(tab);
    renderDetailStatus();
  } catch (error) { detailId = null; if (authenticated) toast(error.message, true); }
  finally { profileLoading = false; }
}

async function closeDetails() {
  if (saving) return false;
  if (changeCount() && !confirm(t("web.confirm_discard_changes"))) return false;
  closeTemplateSelector();
  stopVision();
  $("device-dialog").close();
  draft = null;
  savedProfile = null;
  detailId = null;
  detailSession = null;
  resetLogs();
  return true;
}

function renderDetailStatus() {
  if (!detailId) return;
  const device = deviceById(detailId);
  if (!device) return;
  if (visionWanted && !device.running) stopVision({ mode: "stopped", message: t("web.bot_stopped_hint") });
  else renderVisionControls();
  if (detailSession !== (device.started_at || null)) {
    detailSession = device.started_at || null;
    resetLogs();
    pollLogs();
    if (visionWanted && activeTab === "vision" && device.running) { stopVision(); startVision(); }
  }
  const status = metric(device, "state") || metric(device, "estado");
  $("detail-status").replaceChildren(badge(device), node("span", "muted", [device.model, device.running ? displayCode("states", status) : null, device.running ? runtime(device) : null].filter(Boolean).join(" · ")));
  if (metric(device, "model")) $("detail-status").append(node("span", "muted", t("web.ai_model_label", { value_1: metric(device, "model") })));
  const visionError = metric(device, "vision_error");
  if (visionError) {
    const age = metric(device, "vision_last_success_age");
    $("detail-status").append(node("span", "danger-text", t("web.vision_failure_summary", { value_1: visionError, value_2: metric(device, "vision_failures") || 0, value_3: age == null ? "" : t("web.last_vision_success", { value_1: Math.floor(age) }) })));
  }
}

function group(title, onReset) {
  const section = node("section", "settings-group");
  const heading = node("div", "group-heading");
  heading.append(node("h3", "", title));
  if (onReset) heading.append(button(t("web.reset_group"), "text-button", onReset));
  const content = node("div", "settings-card");
  section.append(heading, content);
  return { section, content };
}

function renderProfile() {
  if (!draft) return;
  const basics = group(t("web.identity_and_run"));
  const nameRow = node("div", "setting-row");
  const nameLabel = node("label", "", t("web.device_name"));
  nameLabel.htmlFor = "device-name";
  const name = node("input");
  name.id = "device-name";
  name.maxLength = 80;
  name.value = draft.name;
  name.placeholder = deviceById(detailId)?.model || t("web.device_name_example");
  name.addEventListener("input", () => { draft.name = name.value; updatePending(); });
  const nameInput = node("div", "setting-input-row");
  nameInput.append(name);
  nameRow.append(nameLabel, nameInput);
  basics.content.append(nameRow);
  const runSelect = (key, title, options, hint) => {
    const row = node("div", "setting-row");
    const label = node("label", "", title);
    const select = node("select");
    select.id = `run-${key}`;
    label.htmlFor = select.id;
    for (const [value, text] of options) {
      const option = node("option", "", text);
      option.value = value;
      select.append(option);
    }
    select.value = draft.run[key];
    select.addEventListener("change", () => {
      draft.run[key] = select.value;
      if (key === "detector_mode") {
        if (select.value === "ai") draft.run.layout_only = false;
        renderProfile();
      }
      updatePending();
    });
    row.append(label, select);
    if (hint) row.append(node("p", "setting-hint", hint));
    basics.content.append(row);
  };
  runSelect("detector_mode", t("web.recognition_mode"), [
    ["templates", t("web.template_bot")], ["ai", t("web.experimental_ai_model")]
  ], t("web.recognition_mode_hint"));
  if (draft.run.detector_mode === "ai") {
    const models = state.models.length ? state.models.map((model) => [model.value, model.label_key ? t(model.label_key) : model.label]) : [["auto", t("web.auto_capture_model")]];
    if (!models.some(([value]) => value === draft.run.ai_model)) models.push([draft.run.ai_model, t("web.unavailable_item", { value_1: draft.run.ai_model })]);
    runSelect("ai_model", t("web.trained_model"), models, t("web.auto_model_hint"));
    runSelect("ai_source", t("web.ai_region_source"), [
      ["templates", t("web.template_ai_source")],
      ["proposer", t("web.experimental_contour_source")]
    ], t("web.contour_source_hint"));
    const row = node("div", "setting-row");
    const label = node("label", "", t("web.ai_min_confidence"));
    label.htmlFor = "run-ai-confidence";
    const input = thresholdInput(draft.run.ai_min_confidence, t("web.ai_min_confidence"));
    input.id = "run-ai-confidence";
    input.addEventListener("input", () => {
      if (input.checkValidity()) draft.run.ai_min_confidence = Number(input.value);
      updatePending();
    });
    row.append(label, input, node("p", "setting-hint", t("web.rare_categories_hint")));
    basics.content.append(row);
  }
  const options = [
    ["ai_collect", t("web.collect_ai_data"), t("web.collect_ai_data_hint")],
    ["layout_only", t("web.layout_only"), t("web.layout_only_hint")],
    ["save_log", t("web.save_log_file"), t("web.save_log_file_hint")]
  ];
  for (const [key, title, description] of options) {
    const label = node("label", "run-option");
    const input = node("input");
    input.type = "checkbox";
    input.checked = draft.run[key];
    input.disabled = key === "layout_only" && draft.run.detector_mode === "ai";
    input.addEventListener("change", () => { draft.run[key] = input.checked; updatePending(); });
    const text = node("span");
    text.append(node("strong", "", title), node("small", "", description));
    label.append(input, text);
    basics.content.append(label);
  }
  $("profile-fields").replaceChildren(basics.section);
  const groups = new Map();
  for (const field of state.schema) {
    const key = field.group_key ? t(field.group_key) : field.group || t("web.other_settings");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(field);
  }
  for (const [title, fields] of groups) {
    const section = group(title, () => {
      fields.forEach((field) => delete draft.settings[field.key]);
      renderProfile();
      updatePending();
    });
    fields.forEach((field) => section.content.append(settingField(field)));
    $("profile-fields").append(section.section);
  }
}

function renderBlockRegions() {
  if (!draft) return;
  const total = (draft.block_regions || []).length;
  const summary = node("div", "block-region block-summary");
  summary.append(
    node("strong", "", total ? t(total === 1 ? "web.ignored_areas_count_one" : "web.ignored_areas_count_other", { count: count(total) }) : t("web.no_ignored_areas")),
    node("span", "", t("web.edit_regions_hint"))
  );
  $("block-regions").replaceChildren(summary);
}

function settingField(field) {
  const row = node("div", "setting-row");
  const labelRow = node("div", "setting-label-row");
  const labelText = field.label_key ? t(field.label_key) : field.label || field.key;
  const label = node("label", "", labelText);
  const overrideLabel = node("label", "check-label");
  const override = node("input");
  override.type = "checkbox";
  override.checked = own(draft.settings, field.key);
  override.setAttribute("aria-label", t("web.customize_field", { value_1: labelText }));
  overrideLabel.append(override, document.createTextNode(t("web.customize")));
  const type = field.type;
  const isBoolean = ["boolean", "bool"].includes(type) || typeof field.default === "boolean";
  const isNumber = ["number", "float", "int", "integer"].includes(type) || typeof field.default === "number";
  const input = node(isBoolean || field.options ? "select" : "input");
  input.id = `setting-${field.key}`;
  label.htmlFor = input.id;
  if (isBoolean) {
    for (const [value, text] of [["true", t("web.enabled")], ["false", t("web.disabled")]]) {
      const option = node("option", "", text);
      option.value = value;
      input.append(option);
    }
  } else if (field.options) {
    for (const value of field.options) {
      const option = node("option", "", value.label ?? value);
      option.value = value.value ?? value;
      input.append(option);
    }
  } else {
    input.type = isNumber ? "number" : "text";
    if (isNumber) {
      if (field.min != null) input.min = field.min;
      if (field.max != null) input.max = field.max;
      input.step = ["int", "integer"].includes(type) ? 1 : field.step ?? "any";
      input.required = true;
    }
  }
  input.value = draft.settings[field.key] ?? field.default ?? "";
  input.disabled = !override.checked;
  const read = () => isBoolean ? input.value === "true" : isNumber ? Number(input.value) : input.value;
  override.addEventListener("change", () => {
    input.disabled = !override.checked;
    if (override.checked) draft.settings[field.key] = read();
    else { delete draft.settings[field.key]; input.value = field.default ?? ""; }
    updatePending();
  });
  input.addEventListener("input", () => { if (input.checkValidity()) draft.settings[field.key] = read(); updatePending(); });
  const inputRow = node("div", "setting-input-row");
  const defaultValue = isBoolean ? field.default ? t("web.enabled") : t("web.disabled") : String(field.default ?? "—");
  inputRow.append(input, node("span", "default-chip", t("web.default_value", { value_1: defaultValue })));
  labelRow.append(label, overrideLabel);
  row.append(labelRow, inputRow);
  if (field.description) row.append(node("p", "setting-hint", field.description));
  return row;
}

function thresholdInput(value, label) {
  const input = node("input");
  input.type = "number";
  input.min = 0;
  input.max = 1;
  input.step = 0.01;
  input.required = true;
  input.value = value;
  input.setAttribute("aria-label", label);
  return input;
}

function defaultCategoryThreshold(category) {
  if (category.uses_global) {
    return draft.settings.shape_threshold ?? state.schema.find((field) => field.key === "shape_threshold")?.default ?? category.threshold ?? 0.8;
  }
  return category.threshold ?? 0.8;
}

function renderItems() {
  if (!draft) return;
  $("panel-items").querySelector(".notice").textContent = t("web.threshold_inheritance_hint");
  const query = $("item-search").value.trim().toLocaleLowerCase(locale);
  const sections = [];
  for (const category of state.templates.categories || []) {
    const categoryMatch = [category.name, displayCode("categories", category.name)].some((name) => name.toLocaleLowerCase(locale).includes(query));
    const items = (category.items || []).filter((item) => !query || categoryMatch || item.name.toLocaleLowerCase(locale).includes(query));
    if (query && !categoryMatch && !items.length) continue;
    const section = node("details", "category");
    section.open = !!query;
    const heading = node("summary", "", displayCode("categories", category.name));
    heading.append(node("span", "count", t("web.template_count", { count: count(category.count) })));
    const content = node("div", "category-body");
    const control = node("div", "threshold-control");
    const label = node("label", "check-label");
    const override = node("input");
    override.type = "checkbox";
    override.checked = own(draft.category_thresholds, category.name);
    label.append(override, document.createTextNode(t("web.category_threshold")));
    const input = thresholdInput(draft.category_thresholds[category.name] ?? defaultCategoryThreshold(category), t("web.threshold_for", { value_1: category.name }));
    input.disabled = !override.checked;
    override.addEventListener("change", () => {
      input.disabled = !override.checked;
      if (override.checked) draft.category_thresholds[category.name] = Number(input.value);
      else { delete draft.category_thresholds[category.name]; input.value = defaultCategoryThreshold(category); }
      refreshInherited();
      updatePending();
    });
    input.addEventListener("input", () => {
      if (input.checkValidity()) draft.category_thresholds[category.name] = Number(input.value);
      refreshInherited();
      updatePending();
    });
    const reset = button(t("web.reset_category"), "text-button", () => {
      delete draft.category_thresholds[category.name];
      (category.items || []).forEach((item) => delete draft.item_thresholds[item.key]);
      renderItems();
      updatePending();
    });
    control.append(label, input, reset);
    content.append(control);
    const inherited = [];
    for (const item of items) {
      const row = node("div", "item-row");
      const itemLabel = node("label", "check-label");
      const itemOverride = node("input");
      itemOverride.type = "checkbox";
      itemOverride.checked = own(draft.item_thresholds, item.key);
      itemOverride.setAttribute("aria-label", t("web.customize_threshold", { value_1: item.name }));
      itemLabel.append(itemOverride);
      const identity = node("div");
      identity.append(node("span", "item-name", item.name), node("small", "", t(item.count === 1 ? "web.file_status_one" : "web.file_status_other", { count: count(item.count || 1), status: itemOverride.checked ? t("web.custom_value") : t("web.inherited_value") })));
      const effective = () => draft.category_thresholds[category.name] ?? defaultCategoryThreshold(category);
      const itemInput = thresholdInput(draft.item_thresholds[item.key] ?? effective(), t("web.threshold_for", { value_1: item.name }));
      itemInput.disabled = !itemOverride.checked;
      const sync = () => {
        itemInput.disabled = !itemOverride.checked;
        if (!itemOverride.checked) itemInput.value = effective();
        identity.querySelector("small").textContent = t(item.count === 1 ? "web.file_status_one" : "web.file_status_other", { count: count(item.count || 1), status: itemOverride.checked ? t("web.custom_value") : t("web.inherited_value") });
      };
      inherited.push(sync);
      itemOverride.addEventListener("change", () => {
        if (itemOverride.checked) draft.item_thresholds[item.key] = Number(itemInput.value);
        else delete draft.item_thresholds[item.key];
        sync();
        updatePending();
      });
      itemInput.addEventListener("input", () => { if (itemInput.checkValidity()) draft.item_thresholds[item.key] = Number(itemInput.value); updatePending(); });
      const resetItem = button("↶", "reset-icon", () => { delete draft.item_thresholds[item.key]; itemOverride.checked = false; sync(); updatePending(); }, t("web.reset_item_threshold"));
      resetItem.setAttribute("aria-label", t("web.reset_item", { value_1: item.name }));
      row.append(itemLabel, identity, itemInput, resetItem);
      content.append(row);
    }
    function refreshInherited() { inherited.forEach((sync) => sync()); }
    section.append(heading, content);
    sections.push(section);
  }
  $("item-fields").replaceChildren(...sections);
  if (!sections.length) $("item-fields").append(node("p", "section-description", query ? t("web.no_items_found") : t("web.no_templates_found")));
}

async function saveProfile() {
  if (!draft || saving || !changeCount()) return;
  const invalid = [...$("profile-fields").querySelectorAll("input,select"), ...$("item-fields").querySelectorAll("input")].find((input) => !input.disabled && !input.checkValidity());
  if (invalid) { invalid.reportValidity(); return; }
  saving = true;
  updatePending();
  showError("profile-error", "");
  const submitted = clone(draft);
  try {
    const result = await api(`/api/devices/${encodeURIComponent(detailId)}/profile`, { method: "PUT", body: JSON.stringify(submitted) });
    if (!authenticated) return;
    savedProfile = normalizeProfile(result.device_id || result.settings ? result : submitted);
    draft = clone(savedProfile);
    renderProfile();
    renderItems();
    renderBlockRegions();
    toast(t("web.profile_saved"));
    await refreshState();
  } catch (error) { if (authenticated) showError("profile-error", error.message); }
  finally { saving = false; updatePending(); }
}

async function resetProfile() {
  if (!draft || saving || !confirm(t("web.confirm_reset_profile"))) return;
  saving = true;
  updatePending();
  try {
    await api(`/api/devices/${encodeURIComponent(detailId)}/profile`, { method: "DELETE" });
    if (!authenticated) return;
    savedProfile = normalizeProfile({});
    draft = clone(savedProfile);
    renderProfile();
    renderItems();
    renderBlockRegions();
    showError("profile-error", "");
    toast(t("web.profile_reset"));
    await refreshState();
  } catch (error) { if (authenticated) showError("profile-error", error.message); }
  finally { saving = false; updatePending(); }
}

function renderVisionControls() {
  const device = detailId ? deviceById(detailId) : null;
  const mode = device?.detector_mode || metric(device || {}, "detector_mode") || "templates";
  $("tab-vision").textContent = t("web.view");
  $("panel-vision").querySelector(".vision-toolbar h3").textContent = mode === "ai" ? t("web.ai_model_vision") : t("web.template_bot_vision");
  $("vision-image").alt = mode === "ai" ? t("web.ai_detections_alt") : t("web.template_detections_alt");
  $("vision-toggle").textContent = visionWanted ? t("web.stop_vision") : visionMode === "error" ? t("web.try_again") : t("web.view_vision");
  $("vision-toggle").disabled = visionMode === "starting" || (!visionWanted && (!device?.running || document.hidden));
}

function renderVision(mode, message = "") {
  visionMode = mode;
  const descriptions = {
    off: [t("web.stream_off"), t("web.enable_vision_hint"), "◎"],
    starting: [t("web.vision_starting"), t("web.vision_stream_requested"), "◎"],
    waiting: [t("web.waiting_first_frame"), t("web.preparing_vision_frame"), "◎"],
    live: [t("web.live"), "", "◎"],
    paused: [t("web.stream_paused"), t("web.vision_resume_hint"), "Ⅱ"],
    stopped: [t("web.view_stopped"), t("web.view_stopped_message"), "□"],
    error: [t("web.view_error"), t("web.view_error_message"), "!"]
  };
  const [title, description, symbol] = descriptions[mode] || descriptions.off;
  $("vision-status").textContent = mode === "live" ? t("web.live_at", { time: new Date().toLocaleTimeString(locale) }) : title;
  $("vision-placeholder-title").textContent = title;
  $("vision-placeholder-message").textContent = message || description;
  $("vision-symbol").textContent = symbol;
  $("vision-stage").classList.toggle("vision-error", mode === "error");
  $("vision-stage").classList.toggle("vision-live", mode === "live");
  $("vision-image").hidden = mode !== "live";
  $("vision-placeholder").hidden = mode === "live";
  renderVisionControls();
}

function releaseVision(id) {
  if (!id) return;
  const headers = { "Content-Type": "application/json", "X-Eatventure-Locale": locale, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  const pending = (visionReleases.get(id) || Promise.resolve()).then(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    return fetch(`/api/devices/${encodeURIComponent(id)}/vision`, {
      method: "POST", headers,
      body: JSON.stringify({ enabled: false }),
      keepalive: true,
      signal: controller.signal
    }).catch(() => {}).finally(() => clearTimeout(timeout));
  });
  visionReleases.set(id, pending);
  pending.finally(() => { if (visionReleases.get(id) === pending) visionReleases.delete(id); });
  return pending;
}

function clearVisionImage() {
  $("vision-image").onload = null;
  $("vision-image").onerror = null;
  $("vision-image").removeAttribute("src");
  if (visionUrl) URL.revokeObjectURL(visionUrl);
  visionUrl = null;
}

function templateSnapshotId() {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function stopVision({ mode = "off", message = "", pause = false } = {}) {
  const id = visionDevice;
  visionResume = pause && (visionWanted || visionResume);
  visionWanted = false;
  visionDevice = null;
  visionGeneration += 1;
  const stream = visionStream;
  visionStream = null;
  if (stream) {
    clearTimeout(stream.idleTimer);
    stream.pending = null;
    try { Promise.resolve(stream.reader?.cancel()).catch(() => {}); } catch {}
  }
  visionController?.abort();
  visionController = null;
  clearVisionImage();
  const released = releaseVision(id);
  renderVision(mode, message);
  return released;
}

async function startTemplateSelector(mode = "template") {
  if (!detailId) return;
  const id = detailId;
  await stopVision();
  renderTemplateCategorySuggestions();
  templateDevice = id;
  templateSelection = null;
  templateStart = null;
  templatePointerId = null;
  templateSnapshot = null;
  templateMode = mode;
  templateBlockSelections = [];
  templateBlocksLoaded = false;
  templateBlockEdited = false;
  $("template-selection-step").hidden = false;
  $("template-category-step").hidden = true;
  $("template-selection-box").hidden = true;
  $("template-live-preview").hidden = true;
  $("template-confirm").disabled = true;
  $("template-undo").hidden = mode !== "block";
  $("template-undo").disabled = true;
  $("template-category").value = "";
  $("template-name").value = "";
  $("template-name-field").hidden = true;
  $("template-kind").textContent = mode === "block" ? t("web.new_ignored_area") : "NOVO TEMPLATE";
  $("template-title").textContent = mode === "block" ? t("web.edit_ignored_areas") : t("web.select_item");
  $("template-instruction").textContent = mode === "block" ? t("web.ignored_area_scope_hint") : t("web.drag_item_hint");
  $("template-selection-status").textContent = t("web.capturing_clean_frame");
  $("template-stage").querySelector(".loading-spinner").hidden = false;
  const dialog = $("template-dialog");
  if (!dialog.open) dialog.showModal();
  await api(`/api/devices/${encodeURIComponent(id)}/vision`, { method: "POST", body: JSON.stringify({ enabled: true, mode: "template" }) });
  if (!dialog.open || templateDevice !== id) return;
  loadTemplateFrame();
}

function loadTemplateFrame() {
  const image = $("template-image");
  const id = templateDevice;
  if (!id || !$("template-dialog").open) return;
  clearTimeout(templateRetry);
  templateSnapshot = templateSnapshotId();
  image.onload = () => {
    $("template-stage").querySelector(".loading-spinner").hidden = true;
    image.hidden = false;
    const box = image.getBoundingClientRect();
    image.style.width = `${box.width}px`;
    image.style.height = `${box.height}px`;
    if (templateMode === "block" && !templateBlocksLoaded) {
      templateBlockSelections = (draft.block_regions || []).map(([x, y, width, height]) => ({
        x: x * image.naturalWidth, y: y * image.naturalHeight,
        width: width * image.naturalWidth, height: height * image.naturalHeight
      }));
      templateBlocksLoaded = true;
    }
    renderTemplateBlockSelections();
    $("template-undo").disabled = templateMode === "block" && !templateBlockSelections.length;
    $("template-confirm").disabled = templateMode === "block" ? !templateBlockSelections.length && !templateBlockEdited : true;
    $("template-selection-status").textContent = templateMode === "block" ? t("web.edit_regions_instructions") : t("web.frozen_frame_hint");
  };
  image.onerror = () => {
    templateSnapshot = null;
    image.hidden = true;
    templateRetry = setTimeout(loadTemplateFrame, 400);
  };
  image.src = `/api/devices/${encodeURIComponent(id)}/vision?mode=template&snapshot_id=${templateSnapshot}&t=${Date.now()}`;
}

function refreshTemplateFrame() {
  if (!templateDevice) return;
  templateSelection = null;
  templateStart = null;
  templatePointerId = null;
  templateSnapshot = null;
  templateBlockSelections = [];
  $("template-selection-box").hidden = true;
  $("template-live-preview").hidden = true;
  $("template-confirm").disabled = templateMode === "block" ? !templateBlockSelections.length : true;
  $("template-undo").disabled = true;
  renderTemplateBlockSelections();
  $("template-image").style.width = "";
  $("template-image").style.height = "";
  $("template-image").hidden = true;
  $("template-stage").querySelector(".loading-spinner").hidden = false;
  $("template-selection-status").textContent = t("web.fetching_latest_frame");
  loadTemplateFrame();
}

function closeTemplateSelector() {
  const id = templateDevice;
  clearTimeout(templateRetry);
  templateRetry = null;
  templateDevice = null;
  templateSelection = null;
  templateStart = null;
  templatePointerId = null;
  templateSnapshot = null;
  templateBlockSelections = [];
  const image = $("template-image");
  image.onload = null;
  image.onerror = null;
  image.removeAttribute("src");
  image.style.width = "";
  image.style.height = "";
  image.hidden = true;
  if ($("template-dialog").open) $("template-dialog").close();
  renderTemplateBlockSelections();
  if (id) releaseVision(id);
}

function templatePoint(event) {
  const image = $("template-image");
  const box = image.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(image.naturalWidth, Math.round((event.clientX - box.left) * image.naturalWidth / box.width))),
    y: Math.max(0, Math.min(image.naturalHeight, Math.round((event.clientY - box.top) * image.naturalHeight / box.height)))
  };
}

function updateTemplateSelection(start, end) {
  const image = $("template-image");
  const stage = $("template-stage").getBoundingClientRect();
  const box = image.getBoundingClientRect();
  const x = Math.min(start.x, end.x), y = Math.min(start.y, end.y);
  templateSelection = { x, y, width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) };
  const selection = $("template-selection-box");
  selection.style.left = `${box.left - stage.left + x * box.width / image.naturalWidth}px`;
  selection.style.top = `${box.top - stage.top + y * box.height / image.naturalHeight}px`;
  selection.style.width = `${templateSelection.width * box.width / image.naturalWidth}px`;
  selection.style.height = `${templateSelection.height * box.height / image.naturalHeight}px`;
  selection.hidden = false;
  updateTemplatePreview();
}

function renderTemplateBlockSelections() {
  const stage = $("template-stage");
  stage.querySelectorAll(".template-block-box").forEach((element) => element.remove());
  const image = $("template-image");
  if (!templateBlockSelections.length || !image.naturalWidth || image.hidden) return;
  const stageBox = stage.getBoundingClientRect();
  const imageBox = image.getBoundingClientRect();
  templateBlockSelections.forEach((region, index) => {
    const box = node("div", "template-block-box");
    box.style.left = `${imageBox.left - stageBox.left + region.x * imageBox.width / image.naturalWidth}px`;
    box.style.top = `${imageBox.top - stageBox.top + region.y * imageBox.height / image.naturalHeight}px`;
    box.style.width = `${region.width * imageBox.width / image.naturalWidth}px`;
    box.style.height = `${region.height * imageBox.height / image.naturalHeight}px`;
    box.textContent = t("web.area_label", { value_1: index + 1 });
    box.tabIndex = 0;
    box.setAttribute("role", "button");
    box.setAttribute("aria-label", t("web.remove_ignored_area", { value_1: index + 1 }));
    const remove = () => {
      templateBlockSelections.splice(index, 1);
      templateBlockEdited = true;
      renderTemplateBlockSelections();
      $("template-undo").disabled = !templateBlockSelections.length;
      $("template-confirm").disabled = !templateBlockSelections.length && !templateBlockEdited;
      $("template-selection-status").textContent = t("web.area_removed");
    };
    box.addEventListener("click", remove);
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); remove(); }
    });
    stage.append(box);
  });
}

function undoTemplateBlockSelection() {
  templateBlockSelections.pop();
  templateBlockEdited = true;
  renderTemplateBlockSelections();
  $("template-undo").disabled = !templateBlockSelections.length;
  $("template-confirm").disabled = !templateBlockSelections.length && !templateBlockEdited;
  $("template-selection-status").textContent = templateBlockSelections.length
    ? t("web.areas_selected_hint", { value_1: templateBlockSelections.length })
    : t("web.select_areas_hint");
}

function updateTemplatePreview() {
  const { x, y, width, height } = templateSelection || {};
  const preview = $("template-live-preview");
  if (!width || !height) { preview.hidden = true; return; }
  const limit = 240;
  const scale = Math.min(limit / width, limit / height);
  const canvas = $("template-live-canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  context.drawImage($("template-image"), x, y, width, height, 0, 0, canvas.width, canvas.height);
  $("template-live-size").textContent = t("web.crop_dimensions", { value_1: width, value_2: height });
  preview.hidden = false;
}

function reviewTemplateSelection() {
  if (templateMode === "block") {
    if (!templateBlockSelections.length && !templateBlockEdited) return;
    if (templateBlockSelections.length > 32) { toast(t("web.ignored_area_limit"), true); return; }
    const image = $("template-image");
    draft.block_regions = templateBlockSelections.map(({ x, y, width, height }) => [
        x / image.naturalWidth,
        y / image.naturalHeight,
        width / image.naturalWidth,
        height / image.naturalHeight
      ]);
    renderBlockRegions();
    updatePending();
    closeTemplateSelector();
    toast(t("web.ignored_areas_updated"));
    return;
  }
  if (!templateSelection || templateSelection.width < 6 || templateSelection.height < 6) return;
  const { x, y, width, height } = templateSelection;
  const canvas = $("template-preview");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage($("template-image"), x, y, width, height, 0, 0, width, height);
  $("template-review-size").textContent = t("web.crop_dimensions", { value_1: width, value_2: height });
  $("template-selection-step").hidden = true;
  $("template-category-step").hidden = false;
  $("template-title").textContent = t("web.confirm_template");
  $("template-instruction").textContent = t("web.review_template_hint");
  $("template-category").focus();
}

function returnToTemplateSelection() {
  $("template-category-step").hidden = true;
  $("template-selection-step").hidden = false;
  $("template-title").textContent = t("web.select_item");
  $("template-instruction").textContent = t("web.drag_item_hint");
}

async function saveTemplateSelection() {
  if (!templateSelection || !templateDevice || !templateSnapshot) return;
  const category = $("template-category").value.trim();
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(category)) { toast(t("web.invalid_category"), true); return; }
  const name = category === "food" ? $("template-name").value.trim() : "";
  const button = $("template-save"); button.disabled = true;
  try {
    const result = await api(`/api/devices/${encodeURIComponent(templateDevice)}/templates`, {
      method: "POST",
      body: JSON.stringify({
        category,
        name,
        snapshot: templateSnapshot,
        ...templateSelection
      })
    });
    toast(t("web.template_saved", { value_1: result.category, value_2: result.width, value_3: result.height }));
    closeTemplateSelector();
    await refreshState();
  } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
}

async function startVision() {
  if (!authenticated || !detailId || !$("device-dialog").open || activeTab !== "vision") return;
  const device = deviceById(detailId);
  if (!device?.running) { stopVision({ mode: "stopped" }); return; }
  if (document.hidden) { visionResume = true; renderVision("paused"); return; }
  if (visionWanted && visionDevice === detailId) return;
  visionResume = false;
  visionWanted = true;
  const id = detailId;
  visionDevice = id;
  const generation = ++visionGeneration;
  renderVision("starting");
  try {
    await visionReleases.get(id);
    if (generation !== visionGeneration) return;
    const mode = visionShowDetails ? "vision" : "clean";
    const result = await api(`/api/devices/${encodeURIComponent(id)}/vision`, { method: "POST", body: JSON.stringify({ enabled: true, mode }) });
    if (generation !== visionGeneration) {
      if (!visionWanted || visionDevice !== id) releaseVision(id);
      return;
    }
    if (result.enabled === false) throw new Error(t("web.stream_not_enabled"));
    renderVision("waiting");
    streamVision(generation);
  } catch (error) {
    if (generation === visionGeneration && authenticated) stopVision({ mode: "error", message: error.message });
  }
}

function loadVisionFrame(blob, generation, signal) {
  const image = $("vision-image");
  const previousUrl = visionUrl;
  const url = URL.createObjectURL(blob);
  visionUrl = url;
  return new Promise((resolve, reject) => {
    let settled = false;
    const timeout = setTimeout(() => finish(new Error(t("web.image_load_timeout"))), 10000);
    const finish = (error, loaded = false) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
      if (image.onload === loadedFrame) image.onload = null;
      if (image.onerror === failedFrame) image.onerror = null;
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      if (!loaded) {
        URL.revokeObjectURL(url);
        if (visionUrl === url) visionUrl = null;
      }
      if (error) reject(error); else resolve(loaded);
    };
    const abort = () => finish(null);
    const loadedFrame = () => {
      if (generation !== visionGeneration || !visionWanted) { finish(null); return; }
      renderVision("live");
      finish(null, true);
    };
    const failedFrame = () => finish(new Error(t("web.image_load_failed")));
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) { finish(null); return; }
    image.onload = loadedFrame;
    image.onerror = failedFrame;
    image.src = url;
  });
}

async function* jpegFrames(body, { boundary = "frame", signal, onReader } = {}) {
  if (!body || typeof body.getReader !== "function") throw new Error(t("web.invalid_stream"));
  if (!/^[a-zA-Z0-9'()+_,./:=?-]{1,70}$/.test(boundary)) throw new Error(t("web.invalid_stream_boundary"));
  const reader = body.getReader();
  onReader?.(reader);
  const decoder = new TextDecoder();
  const lineBuffer = new Uint8Array(MAX_VISION_HEADER_BYTES);
  let lineLength = 0;
  let headerBytes = 0;
  let headers = new Map();
  let phase = "boundary";
  let frame = null;
  let frameOffset = 0;
  try {
    while (!signal?.aborted) {
      const { value, done } = await reader.read();
      if (done) {
        if (phase !== "boundary" || lineLength) throw new Error(t("web.incomplete_stream_frame"));
        return;
      }
      const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
      let offset = 0;
      while (offset < bytes.length) {
        if (signal?.aborted) return;
        if (phase === "body") {
          const length = Math.min(frame.length - frameOffset, bytes.length - offset);
          frame.set(bytes.subarray(offset, offset + length), frameOffset);
          frameOffset += length;
          offset += length;
          if (frameOffset === frame.length) {
            const completed = frame;
            frame = null;
            frameOffset = 0;
            phase = "separator";
            yield completed;
          }
          continue;
        }
        const newline = bytes.indexOf(10, offset);
        const end = newline < 0 ? bytes.length : newline + 1;
        const length = end - offset;
        if (lineLength + length > MAX_VISION_HEADER_BYTES) throw new Error(t("web.stream_headers_limit"));
        lineBuffer.set(bytes.subarray(offset, end), lineLength);
        lineLength += length;
        offset = end;
        if (newline < 0) continue;
        if (lineLength < 2 || lineBuffer[lineLength - 2] !== 13) throw new Error(t("web.invalid_stream_format"));
        const lineBytes = lineLength;
        const line = decoder.decode(lineBuffer.subarray(0, lineLength - 2));
        lineLength = 0;
        if (phase === "boundary") {
          if (line === `--${boundary}--`) return;
          if (line !== `--${boundary}`) throw new Error(t("web.invalid_frame_boundary"));
          phase = "headers";
          headerBytes = 0;
          headers = new Map();
        } else if (phase === "headers") {
          headerBytes += lineBytes;
          if (headerBytes > MAX_VISION_HEADER_BYTES) throw new Error(t("web.stream_headers_limit"));
          if (line) {
            const match = line.match(/^([a-zA-Z0-9-]+):[ \t]*(.*)$/);
            if (!match || headers.has(match[1].toLowerCase())) throw new Error(t("web.invalid_frame_header"));
            headers.set(match[1].toLowerCase(), match[2].trim());
          } else {
            const length = headers.get("content-length") || "";
            if (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)) || Number(length) < 1 || Number(length) > MAX_VISION_FRAME_BYTES) throw new Error(t("web.invalid_frame_size"));
            if (!/^image\/jpeg(?:\s*;.*)?$/i.test(headers.get("content-type") || "")) throw new Error(t("web.invalid_jpeg_frame"));
            frame = new Uint8Array(Number(length));
            phase = "body";
          }
        } else {
          if (line !== "") throw new Error(t("web.invalid_frame_ending"));
          phase = "boundary";
        }
      }
    }
  } finally {
    try { await reader.cancel(); } catch {}
    reader.releaseLock();
  }
}

function queueVisionFrame(frame, stream) {
  stream.pending = frame;
  if (stream.rendering) return;
  stream.rendering = true;
  (async () => {
    try {
      while (stream.pending && visionStream === stream && stream.generation === visionGeneration && visionWanted) {
        const latest = stream.pending;
        stream.pending = null;
        await loadVisionFrame(new Blob([latest], { type: "image/jpeg" }), stream.generation, stream.controller.signal);
      }
    } catch (error) {
      if (visionStream === stream && visionWanted) stopVision({ mode: "error", message: error.message });
    } finally {
      stream.rendering = false;
      stream.pending = null;
    }
  })();
}

function renewVisionTimeout(stream) {
  clearTimeout(stream.idleTimer);
  stream.idleTimer = setTimeout(() => {
    if (visionStream === stream && visionWanted) stopVision({ mode: "error", message: t("web.frame_timeout") });
  }, 10000);
}

async function streamVision(generation) {
  if (generation !== visionGeneration || !visionWanted || !authenticated || document.hidden || activeTab !== "vision" || detailId !== visionDevice || !$("device-dialog").open || visionController) return;
  if (!deviceById(visionDevice)?.running) { stopVision({ mode: "stopped" }); return; }
  const controller = new AbortController();
  visionController = controller;
  const stream = { generation, controller, reader: null, pending: null, rendering: false, idleTimer: null };
  visionStream = stream;
  renewVisionTimeout(stream);
  try {
    const mode = visionShowDetails ? "vision" : "clean";
    const response = await fetch(`/api/devices/${encodeURIComponent(visionDevice)}/vision/stream?mode=${mode}`, {
      headers: { Accept: "multipart/x-mixed-replace", "X-Eatventure-Locale": locale, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      signal: controller.signal,
      cache: "no-store"
    });
    if (generation !== visionGeneration) return;
    if (response.status === 401) { logout(true); return; }
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      if (generation !== visionGeneration) return;
      const detail = typeof result.detail === "string" ? result.detail : t("web.vision_image_unavailable");
      if (response.status === 409 && !deviceById(visionDevice)?.running) { stopVision({ mode: "stopped" }); return; }
      throw new Error(detail);
    }
    const contentType = response.headers.get("Content-Type") || "";
    const boundary = contentType.match(/\bboundary=(?:"([^"]+)"|([^;\s]+))/i);
    if (!/^multipart\/x-mixed-replace\b/i.test(contentType) || !boundary) throw new Error(t("web.invalid_jpeg_stream"));
    for await (const frame of jpegFrames(response.body, { boundary: boundary[1] || boundary[2], signal: controller.signal, onReader: (reader) => { stream.reader = reader; } })) {
      if (generation !== visionGeneration || !visionWanted) return;
      renewVisionTimeout(stream);
      queueVisionFrame(frame, stream);
    }
    if (generation === visionGeneration && visionWanted) throw new Error(t("web.stream_ended_retry"));
  } catch (error) {
    if (generation === visionGeneration && visionWanted) stopVision({ mode: "error", message: error.name === "AbortError" ? t("web.stream_interrupted_retry") : error.message });
  } finally {
    clearTimeout(stream.idleTimer);
    if (visionController === controller) visionController = null;
    if (visionStream === stream) visionStream = null;
  }
}

function setTab(tab) {
  if (activeTab === "vision" && tab !== "vision") stopVision();
  activeTab = tab;
  if (tab === "items") { renderItems(); updatePending(); }
  for (const key of ["settings", "items", "vision", "logs"]) {
    const active = key === tab;
    $(`tab-${key}`).classList.toggle("active", active);
    $(`tab-${key}`).setAttribute("aria-selected", String(active));
    $(`tab-${key}`).tabIndex = active ? 0 : -1;
    $(`panel-${key}`).hidden = !active;
  }
  if (tab === "vision") startVision();
  if (tab === "logs") pollLogs();
}

function resetLogs() {
  logGeneration += 1;
  logCursor = 0;
  logLines = [];
  logBytes = 0;
  logPaused = false;
  $("log-output").replaceChildren();
  $("log-status").textContent = t("web.waiting_logs");
  $("log-pause").textContent = t("web.pause");
  $("log-download").disabled = true;
}

function appendLogLines(lines) {
  const encoder = new TextEncoder();
  for (const line of lines) {
    const original = String(line.text ?? "");
    const clipped = original.slice(0, MAX_LOG_LINE_BYTES);
    const bytes = encoder.encode(clipped);
    const text = bytes.length > MAX_LOG_LINE_BYTES || clipped.length < original.length
      ? new TextDecoder().decode(bytes.subarray(0, MAX_LOG_LINE_BYTES - 3), { stream: true }) + "…"
      : clipped;
    const translations = {};
    for (const language of ["pt-BR", "en-US"]) {
      if (typeof line.translations?.[language] === "string") {
        translations[language] = new TextDecoder().decode(encoder.encode(line.translations[language]).subarray(0, MAX_LOG_LINE_BYTES), { stream: true });
      }
    }
    const size = encoder.encode(text).length + 1 + Object.values(translations).reduce((total, value) => total + encoder.encode(value).length, 0);
    logLines.push({ id: numeric(line.id), text, translations, bytes: size });
    logBytes += size;
    while (logLines.length > MAX_LOG_LINES || logBytes > MAX_LOG_BYTES) logBytes -= logLines.shift().bytes;
  }
}

function renderLogLines() {
  const output = $("log-output");
  const scrollTop = output.scrollTop;
  output.replaceChildren(...logLines.map((line) => {
    const text = line.translations?.[locale] ?? line.text;
    const style = /(?:\bERROR\b|\b E \[|Traceback|Exception:)/.test(text) ? "error-line" : /(?:\bWARNING\b|\b W \[)/.test(text) ? "warning-line" : "";
    return node("div", style, text);
  }));
  output.scrollTop = $("log-autoscroll").checked ? output.scrollHeight : scrollTop;
}

async function pollLogs() {
  if (!authenticated || !detailId || !$("device-dialog").open || activeTab !== "logs" || logPaused || logLoading) return;
  logLoading = true;
  const generation = logGeneration;
  try {
    const result = await api(`/api/devices/${encodeURIComponent(detailId)}/logs?after=${encodeURIComponent(logCursor)}`);
    if (generation !== logGeneration || !authenticated) return;
    const lines = Array.isArray(result.lines) ? result.lines : [];
    const cursor = numeric(result.cursor);
    const reset = cursor !== null && cursor < logCursor;
    if (reset) { logLines = []; logBytes = 0; }
    appendLogLines(lines);
    if (reset || lines.length) renderLogLines();
    $("log-download").disabled = !logLines.length;
    if (cursor !== null) logCursor = cursor;
  $("log-status").textContent = logLines.length ? t("web.log_retention_status", { value_1: count(logLines.length), value_2: (logBytes / 1024).toLocaleString(locale, { maximumFractionDigits: 1 }), value_3: new Date().toLocaleTimeString(locale) }) : t("web.logs_empty");
  } catch (error) { if (generation === logGeneration && authenticated) $("log-status").textContent = error.message; }
  finally { logLoading = false; }
}

const trainingStatusKeys = {
  idle: "web.training_idle", queued: "web.training_queued", preparing: "web.training_preparing",
  running: "web.training_running", publishing: "web.training_publishing", cancelling: "web.training_cancelling",
  cancelled: "web.training_cancelled", success: "web.training_success", failed: "web.training_failed",
  skipped: "web.training_skipped"
};

function trainingStatus(status) {
  return t(trainingStatusKeys[status] || trainingStatusKeys.idle);
}

function trainingPercent(value) {
  const number = numeric(value);
  return number === null ? "—" : number.toLocaleString(locale, { style: "percent", maximumFractionDigits: 1 });
}

function trainingResolutionLabel(value) {
  if (value === "legacy") return t("web.training_legacy");
  if (value === "all") return t("web.training_all_resolutions");
  return value;
}

function trainingEmptyRow(columns, message) {
  const row = node("tr");
  const cell = node("td", "training-empty", message);
  cell.colSpan = columns;
  row.append(cell);
  return row;
}

async function openTraining() {
  trainingGeneration += 1;
  trainingSettingsDirty = false;
  trainingSettingsSnapshot = "";
  $("training-settings-fields").disabled = true;
  $("training-start").disabled = true;
  $("training-dialog").showModal();
  $("training-data-rows").replaceChildren(trainingEmptyRow(7, t("web.training_loading")));
  $("training-history-rows").replaceChildren(trainingEmptyRow(5, t("web.training_loading")));
  showError("training-error", "");
  await refreshTraining();
}

function closeTraining(force = false) {
  if (!force && trainingSettingsDirty && !confirm(t("web.confirm_discard_changes"))) return;
  trainingGeneration += 1;
  trainingSettingsDirty = false;
  $("training-dialog").close();
  $("training-open").focus();
}

async function refreshTraining(force = false) {
  if (!authenticated || !$("training-dialog").open || trainingLoading) return;
  const generation = trainingGeneration;
  trainingLoading = true;
  $("training-refresh").disabled = true;
  try {
    const result = await api(`/api/training${force ? "?refresh=true" : ""}`);
    if (generation !== trainingGeneration || !authenticated) return;
    trainingData = result;
    renderTraining();
    showError("training-error", result.scan_error ? t(result.scan_error) : "");
  } catch (error) {
    if (generation === trainingGeneration && authenticated) showError("training-error", error.message);
  } finally {
    trainingLoading = false;
    $("training-refresh").disabled = false;
  }
}

function renderTrainingSettings() {
  if (!trainingData?.settings) return;
  const settings = trainingData.settings;
  const snapshot = JSON.stringify(settings);
  if (!trainingSettingsDirty && snapshot !== trainingSettingsSnapshot) {
    $("training-enabled").checked = settings.enabled;
    $("training-interval").value = settings.interval_seconds / 60;
    $("training-min-samples").value = settings.min_samples;
    $("training-batch-size").value = settings.batch_size;
    $("training-jobs").value = settings.jobs;
    $("training-trees").value = settings.trees;
    $("training-pause-bots").checked = settings.pause_bots;
    trainingSettingsSnapshot = snapshot;
  }
  $("training-settings-fields").disabled = trainingSubmitting;
  $("training-save-settings").disabled = !trainingSettingsDirty || trainingSubmitting;
  $("training-settings-status").textContent = t(trainingSettingsDirty ? "web.training_settings_pending" : "web.training_settings_hint");
}

function renderTrainingEstimate() {
  const group = trainingData?.resolutions?.find((group) => group.resolution === $("training-resolution").value);
  const limit = Number($("training-limit").value);
  const samples = group ? Math.min(group.available, limit || group.available) : 0;
  $("training-estimate").textContent = group
    ? t("web.training_estimate", { count: count(samples), model: group.model_path })
    : t("web.training_no_data");
  $("training-categories").textContent = group && Object.keys(group.categories).length
    ? t("web.training_category_summary", { categories: Object.entries(group.categories).map(([name, total]) => `${displayCode("categories", name)}: ${count(total)}`).join(" · ") })
    : "";
  $("training-start").disabled = !group || samples < 20 || trainingData?.busy || trainingSettingsDirty || trainingSubmitting;
  $("training-cancel").disabled = !trainingData?.busy || trainingData.active?.status === "cancelling" || trainingSubmitting;
}

function renderTraining() {
  const data = trainingData;
  if (!data) return;
  renderTrainingSettings();
  $("training-schedule-status").textContent = data.settings.enabled
    ? data.busy && data.next_check_in === 0 ? t("web.training_check_after_run") : t("web.training_next_check", { time: new Date(Date.now() + (data.next_check_in || 0) * 1000).toLocaleTimeString(locale) })
    : t("web.training_auto_disabled");
  const groups = data.resolutions || [];
  const rows = groups.map((group) => {
    const row = node("tr");
    row.append(
      node("td", "mono", trainingResolutionLabel(group.resolution)), node("td", "", count(group.available)),
      node("td", "", count(group.pending)), node("td", "", count(group.processed)), node("td", "", count(group.items)),
      node("td", "", count(Object.keys(group.categories).length))
    );
    const model = node("td", "", group.model_exists ? t("web.training_model_ready") : t("web.training_model_missing"));
    if (group.model_version) model.append(node("small", "mono", group.model_version));
    row.append(model);
    return row;
  });
  $("training-data-rows").replaceChildren(...(rows.length ? rows : [trainingEmptyRow(7, t("web.training_no_data"))]));
  const select = $("training-resolution");
  const previous = select.value;
  const values = groups.map((group) => group.resolution);
  if (JSON.stringify([...select.options].map((option) => option.value)) !== JSON.stringify(values)) {
    select.replaceChildren(...groups.map((group) => {
      const option = node("option", "", trainingResolutionLabel(group.resolution));
      option.value = group.resolution;
      return option;
    }));
    select.value = values.includes(previous) ? previous : (groups.find((group) => group.available >= 20) || groups[0])?.resolution || "";
  }
  for (const option of select.options) option.textContent = trainingResolutionLabel(option.value);
  select.disabled = !groups.length || trainingSubmitting;
  renderTrainingEstimate();

  const job = data.active;
  const report = job ? (data.reports || []).find((report) => report.request_id === job.id) : null;
  const status = data.busy && report?.status === "publishing" ? "publishing" : job?.status || "idle";
  $("training-run-status").textContent = trainingStatus(status);
  $("training-run-status").className = `status-badge ${status === "failed" ? "error" : data.busy ? "running" : "ready"}`;
  $("training-run-detail").textContent = job
    ? [trainingResolutionLabel(job.resolution), t(job.automatic ? "web.training_auto_trigger" : "web.training_manual"), job.started_at ? datasetDate(job.started_at) : null].filter(Boolean).join(" · ")
    : t("web.training_idle_hint");
  showError("training-run-error", job?.error || (job?.status === "failed" && job.exit_code != null ? t("web.training_exit_code", { code: job.exit_code }) : ""));
  const metrics = [
    ["web.training_used_frames", count(report?.training?.images_used ?? report?.current_batch?.used_frames)],
    ["web.training_reused_frames", count(report?.training?.reused_images ?? report?.replay?.frames)],
    ["web.training_accuracy", trainingPercent(report?.validation?.accuracy)]
  ];
  $("training-run-metrics").replaceChildren(...metrics.map(([key, value]) => {
    const metric = node("div", "training-metric");
    metric.append(node("span", "", t(key)), node("strong", "", value));
    return metric;
  }));
  const output = $("training-output");
  const following = output.scrollHeight - output.scrollTop - output.clientHeight < 30;
  const logs = (data.logs || []).join("\n") || t("web.training_no_output");
  if (output.textContent !== logs) {
    output.textContent = logs;
    if (following) output.scrollTop = output.scrollHeight;
  }
  const history = (data.reports || []).map((report) => {
    const row = node("tr");
    const date = node("td", "", datasetDate(report.started_at));
    date.append(node("small", "", t(report.trigger === "automatic" ? "web.training_auto_trigger" : "web.training_manual")));
    const resolution = node("td", "mono", trainingResolutionLabel(report.resolution));
    resolution.append(node("small", "mono", report.run_id));
    const samples = node("td", "", count(report.training.images_used ?? report.current_batch.used_frames));
    if (report.training.category_count != null) samples.append(node("small", "", t("web.training_category_count", { count: count(report.training.category_count) })));
    const validation = node("td", "", t("web.training_validation_scores", { accuracy: trainingPercent(report.validation.accuracy), f1: trainingPercent(report.validation.macro_f1) }));
    const result = report.status === "success"
      ? t(report.model_activated === true ? "web.training_model_activated" : report.model_activated === false ? "web.training_candidate_saved" : "web.training_success")
      : trainingStatus(report.status);
    row.append(date, resolution, samples, validation, node("td", "", result));
    return row;
  });
  $("training-history-rows").replaceChildren(...(history.length ? history : [trainingEmptyRow(5, t("web.training_no_history"))]));
}

async function saveTrainingSettings(event) {
  event.preventDefault();
  if (trainingSubmitting || !event.target.reportValidity()) return;
  const batch = Number($("training-batch-size").value);
  if (batch > 0 && batch < 20) { showError("training-error", t("web.training_batch_too_small")); return; }
  trainingSubmitting = true;
  renderTrainingSettings();
  renderTrainingEstimate();
  try {
    const result = await api("/api/training/settings", { method: "PUT", body: JSON.stringify({
      enabled: $("training-enabled").checked,
      interval_seconds: Number($("training-interval").value) * 60,
      min_samples: Number($("training-min-samples").value), batch_size: batch,
      jobs: Number($("training-jobs").value), trees: Number($("training-trees").value),
      pause_bots: $("training-pause-bots").checked
    }) });
    if (!authenticated) return;
    trainingSettingsDirty = false;
    trainingSettingsSnapshot = "";
    trainingData = { ...trainingData, ...result };
    showError("training-error", "");
    toast(t("web.training_settings_saved"));
  } catch (error) { if (authenticated) showError("training-error", error.message); }
  finally { trainingSubmitting = false; if (authenticated && trainingData) renderTraining(); }
}

async function startTraining(event) {
  event.preventDefault();
  if (trainingSubmitting || trainingData?.busy || trainingSettingsDirty || !event.target.reportValidity()) return;
  trainingSubmitting = true;
  renderTrainingEstimate();
  try {
    const result = await api("/api/training/start", { method: "POST", body: JSON.stringify({
      resolution: $("training-resolution").value, limit: Number($("training-limit").value), cleanup: $("training-cleanup").checked
    }) });
    if (!authenticated) return;
    trainingData = { ...trainingData, ...result };
    showError("training-error", "");
    toast(t("web.training_requested"));
  } catch (error) { if (authenticated) showError("training-error", error.message); }
  finally { trainingSubmitting = false; if (authenticated && trainingData) renderTraining(); }
}

async function cancelTraining() {
  if (trainingSubmitting || !trainingData?.busy) return;
  trainingSubmitting = true;
  renderTrainingEstimate();
  try {
    const result = await api("/api/training/cancel", { method: "POST" });
    if (authenticated) trainingData = { ...trainingData, ...result };
  } catch (error) { if (authenticated) showError("training-error", error.message); }
  finally { trainingSubmitting = false; if (authenticated && trainingData) renderTraining(); }
}

function downloadLogs() {
  if (!logLines.length) { toast(t("web.no_log_download")); return; }
  const blob = new Blob([logLines.map((line) => line.translations?.[locale] ?? line.text).join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = node("a");
  link.href = url;
  link.download = `eatventure-${(detailId || "bot").replace(/[^a-zA-Z0-9_-]/g, "_")}-${new Date().toISOString().slice(0, 10)}.log`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function releaseDatasetImages() {
  datasetUrls.forEach((url) => URL.revokeObjectURL(url));
  datasetUrls = [];
}

function datasetDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString(locale) : t("web.unknown_date");
}

async function openDatasets(preferredSession = null, resolution = null) {
  if ((typeof resolution !== "string" || !/^[1-9]\d*x[1-9]\d*$/.test(resolution)) && preferredSession) {
    try {
      const result = await api(`/api/datasets/sessions?session_id=${encodeURIComponent(preferredSession)}`);
      resolution = result.sessions?.[0]?.resolution;
    } catch (error) {
      if (authenticated) toast(error.message, true);
      return;
    }
  }
  if (typeof resolution !== "string" || !/^[1-9]\d*x[1-9]\d*$/.test(resolution)) {
    toast(t("web.no_collected_resolution"), true);
    return;
  }
  datasetSession = preferredSession || null;
  datasetResolution = resolution;
  $("dataset-dialog").showModal();
  await refreshDatasetSessions(preferredSession);
}

async function refreshDatasetSessions(preferredSession = datasetSession) {
  const generation = ++datasetGeneration;
  $("dataset-refresh").disabled = true;
  $("dataset-session-list").replaceChildren(node("p", "muted", t("web.loading_sessions")));
  try {
    const result = await api(`/api/datasets/sessions?resolution=${encodeURIComponent(datasetResolution)}`);
    if (generation !== datasetGeneration) return;
    const sessions = Array.isArray(result.sessions) ? result.sessions : [];
    const buttons = sessions.map((session) => {
      const item = button("", `dataset-session${session.id === datasetSession ? " active" : ""}`, () => selectDatasetSession(session.id));
      item.append(node("strong", "", session.id), node("small", "", t("web.session_images", { count: count(session.count), date: datasetDate(session.created_at) })));
      item.title = session.id;
      return item;
    });
    $("dataset-session-list").replaceChildren(...buttons);
    if (!sessions.length) {
      $("dataset-session-list").replaceChildren(node("p", "muted", t("web.no_collected_images")));
      clearDatasetSelection();
      return;
    }
    const selected = sessions.find((session) => session.id === preferredSession) || sessions[0];
    await selectDatasetSession(selected.id);
  } catch (error) {
    if (authenticated) $("dataset-session-list").replaceChildren(node("p", "form-error", error.message));
  } finally { $("dataset-refresh").disabled = false; }
}

function clearDatasetSelection() {
  datasetSession = null;
  datasetOffset = 0;
  datasetTotal = 0;
  releaseDatasetImages();
  $("dataset-images").replaceChildren();
  $("dataset-selected").hidden = true;
  $("dataset-empty").hidden = false;
}

async function selectDatasetSession(session, append = false) {
  if (append && datasetPageLoading) return;
  const generation = append ? datasetGeneration : ++datasetGeneration;
  datasetPageLoading = true;
  $("dataset-more").disabled = true;
  if (!append) {
    datasetSession = session;
    datasetOffset = 0;
    releaseDatasetImages();
    $("dataset-images").replaceChildren();
    $("dataset-empty").hidden = true;
    $("dataset-selected").hidden = false;
    $("dataset-status").textContent = t("web.loading_images");
    for (const item of $("dataset-session-list").children) item.classList.toggle("active", item.title === session);
  }
  try {
    const result = await api(`/api/datasets/sessions/${encodeURIComponent(session)}?offset=${datasetOffset}&limit=200&resolution=${encodeURIComponent(datasetResolution)}`);
    if (generation !== datasetGeneration || datasetSession !== session) return;
    datasetTotal = result.total;
    $("dataset-session-title").textContent = t("web.session_image_count", { value_1: count(datasetTotal) });
    $("dataset-session-detail").textContent = `${session} · ${datasetResolution}`;
    $("dataset-range-start").max = datasetTotal;
    $("dataset-range-end").max = datasetTotal;
    if (!append) {
      $("dataset-range-start").value = datasetTotal ? 1 : "";
      $("dataset-range-end").value = datasetTotal ? datasetTotal : "";
    }
    const fragment = document.createDocumentFragment();
    for (const item of result.items || []) {
      const figure = node("figure", "dataset-image");
      const image = node("img");
      image.alt = t("web.session_image_alt", { value_1: item.index });
      image.loading = "lazy";
      const caption = node("figcaption");
      caption.append(node("strong", "", displayCode("actions", item.action) || t("web.no_action")), node("small", "", datasetDate(item.created_at)));
      figure.append(image, node("span", "image-index", `#${item.index}`), caption);
      fragment.append(figure);
      apiBlob(`/api/datasets/sessions/${encodeURIComponent(session)}/images/${encodeURIComponent(item.id)}?resolution=${encodeURIComponent(datasetResolution)}`).then((blob) => {
        if (generation !== datasetGeneration || datasetSession !== session) return;
        const url = URL.createObjectURL(blob);
        datasetUrls.push(url);
        image.src = url;
      }).catch(() => { image.alt = t("web.image_unavailable"); });
    }
    $("dataset-images").append(fragment);
    datasetOffset += (result.items || []).length;
    $("dataset-status").textContent = t("web.images_displayed", { shown: count(datasetOffset), total: count(datasetTotal) });
    $("dataset-more").hidden = datasetOffset >= datasetTotal;
  } catch (error) {
    if (generation === datasetGeneration && authenticated) $("dataset-status").textContent = error.message;
  } finally {
    if (generation === datasetGeneration) {
      datasetPageLoading = false;
      $("dataset-more").disabled = false;
    }
  }
}

async function deleteDataset(payload) {
  if (!datasetSession) return;
  const active = state.devices.some((device) => device.running && device.session_id === datasetSession);
  const description = payload.all ? t("web.delete_session_scope", { value_1: datasetTotal }) : t("web.delete_image_scope", { value_1: payload.start, value_2: payload.end });
  const warning = active ? t("web.active_collection_warning") : "";
  if (!confirm(t("web.confirm_delete_dataset", { value_1: description, value_2: warning }))) return;
  $("dataset-selected").inert = true;
  try {
    const result = await api(`/api/datasets/sessions/${encodeURIComponent(datasetSession)}?resolution=${encodeURIComponent(datasetResolution)}`, { method: "DELETE", body: JSON.stringify(payload) });
    toast(t("web.dataset_deleted", { value_1: count(result.deleted), value_2: count(result.images_deleted) }));
    await refreshDatasetSessions(datasetSession);
  } catch (error) { if (authenticated) toast(error.message, true); }
  finally { $("dataset-selected").inert = false; }
}

function closeDatasets() {
  datasetGeneration += 1;
  clearDatasetSelection();
  datasetResolution = null;
  $("dataset-dialog").close();
}

async function connectAndroid(event) {
  event.preventDefault();
  if ($("connect-form").inert) return;
  const submit = $("connect-submit");
  submit.disabled = true;
  $("connect-form").inert = true;
  $("connect-result").textContent = t("web.adb_connecting");
  try {
    const result = await api("/api/adb/connect", { method: "POST", body: JSON.stringify({ address: $("connect-address").value.trim() }) });
    rememberAdbAddress(result.address);
    $("connect-result").textContent = "";
    $("connect-dialog").close();
    toast(result.message || result.output || t("web.adb_connection_requested"));
    await refreshState(true);
  } catch (error) { if (authenticated) $("connect-result").textContent = error.message; }
  finally { submit.disabled = false; $("connect-form").inert = false; }
}

async function pairAndroid() {
  if ($("connect-form").inert) return;
  const address = $("pair-address").value.trim();
  const code = $("pair-code").value.trim();
  if (!address || !/^\d{6}$/.test(code)) { $("connect-result").textContent = t("web.pairing_input_required"); return; }
  $("pair-submit").disabled = true;
  $("connect-form").inert = true;
  $("connect-result").textContent = t("web.pairing");
  try {
    const result = await api("/api/adb/pair", { method: "POST", body: JSON.stringify({ address, code }) });
    $("pair-code").value = "";
    $("connect-result").textContent = result.message || result.output || t("web.pair_success_hint");
  } catch (error) { if (authenticated) $("connect-result").textContent = error.message; }
  finally { $("pair-submit").disabled = false; $("connect-form").inert = false; }
}

$("login-form").addEventListener("submit", login);
$("session-retry").addEventListener("click", bootstrapSession);
$("theme-toggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
$("logout").addEventListener("click", () => logout());
$("refresh").addEventListener("click", () => refreshState(true));
$("shutdown-server").addEventListener("click", async () => {
  if (!confirm(t("web.confirm_shutdown"))) return;
  stopVision({ mode: "stopped", message: t("web.dashboard_stopping") });
  $("shutdown-server").disabled = true;
  try {
    const result = await api("/api/server/shutdown", { method: "POST" });
    toast(result.message || t("web.dashboard_shutdown_progress"));
  } catch (error) {
    $("shutdown-server").disabled = false;
    toast(error.message, true);
  }
});
$("search").addEventListener("input", renderDevices);
$("status-filter").addEventListener("change", renderDevices);
$("select-all").addEventListener("change", (event) => {
  visibleDevices().forEach((device) => { if (event.target.checked) selected.add(device.id); else selected.delete(device.id); });
  renderDevices();
});
for (const action of ["start", "stop", "restart"]) $(`bulk-${action}`).addEventListener("click", () => executeAction(action, [...selected]));
$("detail-close").addEventListener("click", closeDetails);
$("device-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeDetails(); });
$("save-profile").addEventListener("click", saveProfile);
$("reset-profile").addEventListener("click", resetProfile);
$("reset-thresholds").addEventListener("click", () => {
  if (!draft || !confirm(t("web.confirm_reset_thresholds"))) return;
  draft.category_thresholds = {};
  draft.item_thresholds = {};
  renderItems();
  updatePending();
});
$("item-search").addEventListener("input", renderItems);
for (const tab of document.querySelectorAll("[data-tab]")) {
  tab.addEventListener("click", () => setTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    const tabs = ["settings", "items", "vision", "logs"];
    let index = tabs.indexOf(activeTab);
    if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = tabs.length - 1;
    else return;
    event.preventDefault();
    setTab(tabs[index]);
    $(`tab-${tabs[index]}`).focus();
  });
}
$("log-pause").addEventListener("click", () => { logPaused = !logPaused; $("log-pause").textContent = logPaused ? t("web.resume") : t("web.pause"); if (!logPaused) pollLogs(); });
$("log-download").addEventListener("click", downloadLogs);
$("training-open").addEventListener("click", openTraining);
$("training-close").addEventListener("click", () => closeTraining());
$("training-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeTraining(); });
$("training-refresh").addEventListener("click", () => refreshTraining(true));
$("training-resolution").addEventListener("change", renderTrainingEstimate);
$("training-limit").addEventListener("input", renderTrainingEstimate);
$("training-start-form").addEventListener("submit", startTraining);
$("training-cancel").addEventListener("click", cancelTraining);
$("training-settings-form").addEventListener("submit", saveTrainingSettings);
$("training-settings-form").addEventListener("input", () => {
  trainingSettingsDirty = true;
  renderTrainingSettings();
  renderTrainingEstimate();
});
$("language-switch").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-locale]");
  if (button && button.dataset.locale !== locale) setLocale(button.dataset.locale);
});
$("vision-toggle").addEventListener("click", () => { if (visionWanted) stopVision(); else startVision(); });
$("vision-details").addEventListener("change", () => {
  setVisionDetails($("vision-details").checked);
  if (visionWanted) { stopVision(); startVision(); }
});
$("template-select").addEventListener("click", () => startTemplateSelector().catch((error) => { closeTemplateSelector(); toast(error.message, true); }));
$("block-manage").addEventListener("click", () => { renderBlockRegions(); $("block-dialog").showModal(); });
$("block-close").addEventListener("click", () => $("block-dialog").close());
$("block-cancel").addEventListener("click", () => $("block-dialog").close());
$("block-dialog").addEventListener("cancel", () => $("block-dialog").close());
$("block-select").addEventListener("click", () => {
  $("block-dialog").close();
  startTemplateSelector("block").catch((error) => { closeTemplateSelector(); toast(error.message, true); });
});
$("template-save").addEventListener("click", saveTemplateSelection);
$("template-category").addEventListener("input", () => {
  const isFood = $("template-category").value.trim() === "food";
  $("template-name-field").hidden = !isFood;
  if (!isFood) $("template-name").value = "";
});
$("template-close").addEventListener("click", closeTemplateSelector);
$("template-cancel").addEventListener("click", closeTemplateSelector);
$("template-category-cancel").addEventListener("click", closeTemplateSelector);
$("template-refresh").addEventListener("click", refreshTemplateFrame);
$("template-undo").addEventListener("click", undoTemplateBlockSelection);
$("template-confirm").addEventListener("click", reviewTemplateSelection);
$("template-back").addEventListener("click", returnToTemplateSelection);
$("template-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeTemplateSelector(); });
$("template-image").addEventListener("pointerdown", (event) => {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  event.preventDefault();
  templateStart = templatePoint(event);
  templatePointerId = event.pointerId;
  templateSelection = null;
  $("template-live-preview").hidden = true;
  $("template-confirm").disabled = templateMode === "block" ? !templateBlockSelections.length : true;
  $("template-image").setPointerCapture(event.pointerId);
});
$("template-image").addEventListener("pointermove", (event) => {
  if (!templateStart || event.pointerId !== templatePointerId) return;
  updateTemplateSelection(templateStart, templatePoint(event));
});
$("template-image").addEventListener("pointerup", (event) => {
  if (!templateStart || event.pointerId !== templatePointerId) return;
  updateTemplateSelection(templateStart, templatePoint(event));
  templateStart = null;
  templatePointerId = null;
  if (templateMode === "block") {
    if (templateSelection.width >= 6 && templateSelection.height >= 6) {
      templateBlockSelections.push({ ...templateSelection });
      templateBlockEdited = true;
    }
    templateSelection = null;
    $("template-selection-box").hidden = true;
    $("template-live-preview").hidden = true;
    renderTemplateBlockSelections();
    $("template-undo").disabled = !templateBlockSelections.length;
    $("template-confirm").disabled = !templateBlockSelections.length && !templateBlockEdited;
    $("template-selection-status").textContent = templateBlockSelections.length
      ? t("web.areas_selected_hint", { value_1: templateBlockSelections.length })
      : t("web.selection_min_size");
    return;
  }
  $("template-confirm").disabled = templateSelection.width < 6 || templateSelection.height < 6;
  $("template-selection-status").textContent = t("web.selection_review_hint", { value_1: templateSelection.width, value_2: templateSelection.height });
});
$("template-image").addEventListener("pointercancel", () => { templateStart = null; templatePointerId = null; });
$("connect-open").addEventListener("click", () => { $("connect-result").textContent = ""; $("connect-dialog").showModal(); });
$("reconnect-open").addEventListener("click", () => {
  $("connect-result").textContent = "";
  $("connect-address").value = lastAdbAddress();
  $("connect-dialog").showModal();
  $("connect-address").focus();
});
$("connect-close").addEventListener("click", () => $("connect-dialog").close());
$("connect-form").addEventListener("submit", connectAndroid);
$("pair-submit").addEventListener("click", pairAndroid);
$("dataset-close").addEventListener("click", closeDatasets);
$("dataset-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeDatasets(); });
$("dataset-refresh").addEventListener("click", () => refreshDatasetSessions());
$("dataset-more").addEventListener("click", () => selectDatasetSession(datasetSession, true));
$("dataset-delete-session").addEventListener("click", () => deleteDataset({ all: true }));
$("dataset-range-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const start = Number($("dataset-range-start").value);
  const end = Number($("dataset-range-end").value);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > datasetTotal) {
    toast(t("web.invalid_image_range", { value_1: datasetTotal }), true);
    return;
  }
  deleteDataset({ start, end });
});
window.addEventListener("beforeunload", (event) => { if (changeCount()) { event.preventDefault(); event.returnValue = ""; } });
setInterval(() => { if (!document.hidden) refreshState(); }, 5000);
setInterval(() => { if (!document.hidden) refreshRuntime(); }, 1000);
setInterval(() => { if (!document.hidden) pollLogs(); }, 1500);
setInterval(() => { if (!document.hidden) refreshTraining(); }, 3000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (visionWanted) stopVision({ mode: "paused", pause: true });
  } else {
    refreshState();
    pollLogs();
    refreshTraining();
    if (visionResume && activeTab === "vision") startVision();
  }
});
window.addEventListener("pagehide", () => stopVision());
loadMessages().catch(() => {
  locale = document.documentElement.lang;
  numberFormat = new Intl.NumberFormat(locale);
}).then(() => {
  renderLanguageSwitch();
  restoreTheme();
  restoreVisionDetails();
  bootstrapSession();
});
