"use strict";

const $ = (id) => document.getElementById(id);
const sessionKey = "eatventure.dashboard.token";
const adbAddressKey = "eatventure.dashboard.adbAddress";
const numberFormat = new Intl.NumberFormat("pt-BR");
const state = { devices: [], schema: [], templates: { total: 0, categories: [] } };
const selected = new Set();
const busyDevices = new Set();
let token = readToken();
let authenticated = false;
let authRequired = true;
let stateLoading = false;
let runtimeLoading = false;
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
let sessionLoading = false;
const visionReleases = new Map();
const MAX_VISION_FRAME_BYTES = 5 * 1024 * 1024;
const MAX_VISION_HEADER_BYTES = 8 * 1024;
const MAX_LOG_LINES = 500;
const MAX_LOG_BYTES = 512 * 1024;
const MAX_LOG_LINE_BYTES = 4 * 1024;

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
  $("login-error").textContent = expired ? "Sessão expirada ou token inválido. Entre novamente." : "";
  $("token").focus();
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === "/api/actions" ? 120000 : 45000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
      signal: controller.signal,
      cache: "no-store"
    });
    if (response.status === 401) {
      if (authenticated) logout(true);
      throw new Error("Token de acesso inválido.");
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail || body.error || `O servidor retornou ${response.status}.`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("O servidor demorou para responder. Tente atualizar.");
    if (error instanceof TypeError) throw new Error("Não foi possível acessar o servidor. Verifique a conexão.");
    throw error;
  } finally { clearTimeout(timeout); }
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
    if (!session.authenticated) throw new Error("Token de acesso inválido.");
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
  $("session-status").textContent = "Conectando ao servidor…";
  try {
    const session = await api("/api/session");
    if (session.authenticated || session.auth_required === false) await enterApp(session);
    else {
      const invalid = !!token;
      rememberToken("");
      $("session-loading").hidden = true;
      $("login-form").hidden = false;
      $("login-error").textContent = invalid ? "Token de acesso inválido. Entre novamente." : "";
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
  if (device.status === "stopping") return { key: "ready", label: "Encerrando…" };
  if (device.running) return { key: "running", label: "Em execução" };
  if (device.adb_state === "unauthorized") return { key: "error", label: "Autorize no Android" };
  if (device.status === "error") return { key: "error", label: "Encerrado com erro" };
  if (connected(device)) return { key: "ready", label: "Pronto para iniciar" };
  return { key: "offline", label: "Desconectado" };
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

function numeric(value) { return Number.isFinite(Number(value)) && value !== null && value !== "" ? Number(value) : null; }
function count(value) { const valueNumber = numeric(value); return valueNumber === null ? "—" : numberFormat.format(valueNumber); }

function runtime(device) {
  if (!device.running) return device.exit_code === 0 ? "Encerrado" : device.running === false && device.exit_code != null ? `Saída ${device.exit_code}` : "Aguardando";
  let start = device.started_at;
  if (typeof start === "string") start = new Date(start).getTime();
  else if (typeof start === "number" && start < 1e12) start *= 1000;
  if (!start || !Number.isFinite(start)) return device.pid ? `PID ${device.pid}` : "Ativo";
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return hours ? `${hours}h ${minutes}min` : minutes ? `${minutes}min` : `${seconds}s`;
}

function visibleDevices() {
  const search = $("search").value.trim().toLocaleLowerCase("pt-BR");
  const filter = $("status-filter").value;
  return state.devices.filter((device) => {
    const text = [deviceName(device), device.model, device.serial, device.id, ...(device.aliases || [])].join(" ").toLocaleLowerCase("pt-BR");
    const matchesStatus = filter === "all" || (filter === "running" && device.running) || (filter === "ready" && connected(device) && !device.running) || (filter === "offline" && !connected(device) && !device.running);
    return (!search || text.includes(search)) && matchesStatus;
  });
}

function deviceLayout(device) {
  return [device.id, device.serial, device.name, device.model, device.connected, device.running, device.status, device.adb_state, device.exit_code, device.profile];
}

async function refreshRuntime() {
  if (!authenticated || runtimeLoading) return;
  runtimeLoading = true;
  try {
    const result = await api("/api/runtime");
    if (!authenticated || !Array.isArray(result.devices)) return;
    const changed = JSON.stringify(state.devices.map(deviceLayout)) !== JSON.stringify(result.devices.map(deviceLayout));
    state.devices = result.devices;
    for (const id of selected) if (!deviceById(id)) selected.delete(id);
    $("last-update").textContent = `Atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
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
  $("refresh").disabled = true;
  try {
    const result = await api(`/api/state${force ? "?refresh=true" : ""}`);
    if (!authenticated) return;
    state.devices = Array.isArray(result.devices) ? result.devices : [];
    state.schema = Array.isArray(result.schema) ? result.schema : [];
    state.templates = result.templates || state.templates;
    for (const id of selected) if (!deviceById(id)) selected.delete(id);
    $("server-status").textContent = "Servidor online";
    $("server-detail").textContent = result.server?.hostname || result.server?.host || "Conexão autenticada";
    $("server-dot").className = "server-dot green";
    $("last-update").textContent = `Atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
    showError("state-error", result.error || "");
    renderDevices();
    renderDetailStatus();
  } catch (error) {
    if (!authenticated) return;
    $("server-status").textContent = "Servidor indisponível";
    $("server-dot").className = "server-dot amber";
    showError("state-error", error.message);
    if (!state.devices.length) renderEmpty("Não foi possível carregar", "Use Atualizar para tentar novamente.", "!");
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
  $("nav-count").textContent = state.devices.length;
  $("kpi-running").textContent = count(running);
  $("kpi-running-detail").textContent = `${state.devices.length} ${state.devices.length === 1 ? "dispositivo cadastrado" : "dispositivos cadastrados"}`;
  $("kpi-connected").textContent = count(state.devices.filter(connected).length);
  $("kpi-actions").textContent = count(state.devices.reduce((sum, device) => sum + (numeric(metric(device, "acoes")) || 0), 0));
  $("kpi-templates").textContent = count(state.templates.total);
  $("kpi-templates-detail").textContent = `${count(state.templates.categories?.length || 0)} categorias de reconhecimento`;
  $("device-count").textContent = `${visible.length} ${visible.length === 1 ? "dispositivo" : "dispositivos"}`;
}

function renderDevices() {
  const visible = visibleDevices();
  renderSummary(visible);
  if (!visible.length) {
    renderEmpty(state.devices.length ? "Nenhum resultado" : "Seu primeiro dispositivo começa aqui", state.devices.length ? "Ajuste a busca ou o filtro de status." : "Conecte um Android por USB ou pelo botão Conectar Android.");
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
  const open = button("", "card-open-button", () => openDetails(device.id, "vision"), "Abrir visão IA");
  open.dataset.role = "vision";
  open.setAttribute("aria-label", `Abrir visão IA de ${deviceName(device)}`);
  open.setAttribute("aria-haspopup", "dialog");
  card.append(open);
  const header = node("div", "device-card-header");
  const identity = node("div", "device-identity");
  const title = node("h3", "", deviceName(device));
  title.title = deviceName(device);
  const serial = node("p", "mono", device.serial || device.id);
  serial.title = device.serial || device.id;
  identity.append(title, serial, node("span", "card-open-hint", "Ver visão IA ↗"));
  const checkbox = node("input");
  checkbox.type = "checkbox";
  checkbox.checked = selected.has(device.id);
  checkbox.dataset.role = "select";
  checkbox.setAttribute("aria-label", `Selecionar ${deviceName(device)}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selected.add(device.id); else selected.delete(device.id);
    card.classList.toggle("selected", checkbox.checked);
    updateSelection();
  });
  header.append(node("span", "device-icon", "▥"), identity, checkbox);
  const status = node("div", "device-status-row");
  status.append(badge(device), node("span", "device-runtime", runtime(device)));
  const metrics = node("dl", "device-metrics");
  for (const [key, label, value] of metricValues(device)) {
    const item = node("div", "device-metric");
    const output = node("dd", key === "templates" ? "accent" : "", value);
    output.dataset.metric = key;
    item.append(node("dt", "", label), output);
    metrics.append(item);
  }
  const footer = node("div", "device-card-footer");
  const busy = busyDevices.has(device.id) || device.status === "stopping";
  const start = button(busy ? "Aguarde…" : device.running ? "□ Parar" : "▷ Iniciar", `button ${device.running ? "ghost danger-text" : "primary"} start-button`, () => executeAction(device.running ? "stop" : "start", [device.id]));
  start.dataset.role = "start";
  start.disabled = busy || (!device.running && !connected(device));
  const restart = button("↻", "button ghost", () => executeAction("restart", [device.id]), "Reiniciar bot");
  restart.setAttribute("aria-label", `Reiniciar ${deviceName(device)}`);
  restart.dataset.role = "restart";
  restart.disabled = busy || !device.running || !connected(device);
  const settings = button("⚙", "button ghost settings-button", () => openDetails(device.id, "settings"), "Configurações, itens e logs");
  settings.setAttribute("aria-label", `Configurar ${deviceName(device)}`);
  settings.dataset.role = "settings";
  footer.append(start, restart, settings);
  card.append(header, status);
  card.append(node("p", "bot-state"), node("p", "metrics-freshness"));
  card.append(metrics, footer);
  const profile = device.profile;
  if (profile && (Object.keys(profile.settings || {}).length || Object.keys(profile.category_thresholds || {}).length || Object.keys(profile.item_thresholds || {}).length)) card.append(node("div", "profile-indicator", "◉ Perfil personalizado"));
  updateDeviceMetrics(card, device);
  return card;
}

function metricValues(device) {
  const battery = metric(device, "battery");
  const level = numeric(typeof battery === "object" && battery ? battery.level : battery);
  const fps = numeric(metric(device, "fps") ?? metric(device, "capture_fps"));
  return [
    ["voos", "Voou", count(metric(device, "voos"))], ["reformas", "Renovou", count(metric(device, "reformas"))], ["acoes", "Ações", count(metric(device, "acoes"))],
    ["templates", "Templates", count(metric(device, "templates") ?? state.templates.total)],
    ["fps", "FPS", fps === null ? "—" : fps.toLocaleString("pt-BR", { maximumFractionDigits: 1 })],
    ["battery", "Bateria", level === null ? "—" : `${level}%${battery?.charging ? " ↯" : ""}`]
  ];
}

function updateDeviceMetrics(card, device) {
  for (const [key, , value] of metricValues(device)) card.querySelector(`[data-metric="${key}"]`).textContent = value;
  card.querySelector(".device-runtime").textContent = runtime(device);
  const botState = metric(device, "state") || metric(device, "estado");
  const stateLabel = card.querySelector(".bot-state");
  stateLabel.textContent = botState ? `Estado · ${botState}` : "";
  stateLabel.hidden = !botState || !device.running;
  const updatedAt = numeric(metric(device, "updated_at"));
  const age = updatedAt === null ? null : Math.max(0, Math.floor(Date.now() / 1000 - updatedAt));
  const freshness = card.querySelector(".metrics-freshness");
  freshness.textContent = !device.running ? "" : age === null ? "Aguardando métricas do bot" : age > 10 ? `Métricas sem atualização há ${age}s` : "";
  freshness.hidden = !freshness.textContent;
}

function updateSelection() {
  const visible = visibleDevices();
  const checked = visible.filter((device) => selected.has(device.id)).length;
  $("select-all").checked = visible.length > 0 && checked === visible.length;
  $("select-all").indeterminate = checked > 0 && checked < visible.length;
  $("select-all").disabled = visible.length === 0;
  $("selection-label").textContent = selected.size ? `${selected.size} ${selected.size === 1 ? "selecionado" : "selecionados"}` : "Selecionar todos";
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
  if (action === "stop" && eligible.length > 1 && eligible.length === running && !confirm(`Parar todos os ${eligible.length} bots em execução?`)) return;
  if (action !== "stop" && detailId && eligible.includes(detailId) && changeCount()) {
    toast("Salve o perfil antes de iniciar ou reiniciar para aplicar as alterações.", true);
    return;
  }
  eligible.forEach((id) => busyDevices.add(id));
  renderDevices();
  try {
    const result = eligible.length === 1
      ? await api(`/api/devices/${encodeURIComponent(eligible[0])}/${action}`, { method: "POST" })
      : await api("/api/actions", { method: "POST", body: JSON.stringify({ action, device_ids: eligible }) });
    const failures = (result.results || []).filter((item) => item.error || item.ok === false);
    if (failures.length) toast(`${failures.length} dispositivo(s) com falha: ${failures.map((item) => item.error || item.message).join("; ")}`, true);
    else toast(action === "start" ? "Comando de início enviado." : action === "stop" ? "Bot(s) encerrado(s)." : "Comando de reinício enviado.");
    await refreshState();
  } catch (error) { if (authenticated) toast(error.message, true); }
  finally { eligible.forEach((id) => busyDevices.delete(id)); renderDevices(); }
}

function normalizeProfile(profile = {}) {
  return { device_id: profile.device_id || detailId, name: profile.name || "", settings: { ...profile.settings }, category_thresholds: { ...profile.category_thresholds }, item_thresholds: { ...profile.item_thresholds }, run: { ai_collect: !!profile.run?.ai_collect, layout_only: !!profile.run?.layout_only, save_log: !!profile.run?.save_log } };
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
  return changes;
}

function updatePending() {
  const changes = changeCount();
  $("pending-count").textContent = changes ? `${changes} ${changes === 1 ? "ajuste pendente" : "ajustes pendentes"}` : "Sem alterações pendentes";
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
  if (changeCount() && !confirm("Descartar os ajustes que ainda não foram salvos?")) return false;
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
  if (visionWanted && !device.running) stopVision({ mode: "stopped", message: "O bot foi encerrado. Inicie-o para acompanhar as detecções." });
  else renderVisionControls();
  if (detailSession !== (device.started_at || null)) {
    detailSession = device.started_at || null;
    resetLogs();
    pollLogs();
    if (visionWanted && activeTab === "vision" && device.running) { stopVision(); startVision(); }
  }
  const status = metric(device, "state") || metric(device, "estado");
  $("detail-status").replaceChildren(badge(device), node("span", "muted", [device.model, device.running ? status : null, device.running ? runtime(device) : null].filter(Boolean).join(" · ")));
}

function group(title, onReset) {
  const section = node("section", "settings-group");
  const heading = node("div", "group-heading");
  heading.append(node("h3", "", title));
  if (onReset) heading.append(button("Restaurar grupo", "text-button", onReset));
  const content = node("div", "settings-card");
  section.append(heading, content);
  return { section, content };
}

function renderProfile() {
  if (!draft) return;
  const basics = group("Identificação e execução");
  const nameRow = node("div", "setting-row");
  const nameLabel = node("label", "", "Nome do dispositivo");
  nameLabel.htmlFor = "device-name";
  const name = node("input");
  name.id = "device-name";
  name.maxLength = 80;
  name.value = draft.name;
  name.placeholder = deviceById(detailId)?.model || "Ex.: Android principal";
  name.addEventListener("input", () => { draft.name = name.value; updatePending(); });
  const nameInput = node("div", "setting-input-row");
  nameInput.append(name);
  nameRow.append(nameLabel, nameInput);
  basics.content.append(nameRow);
  const options = [
    ["ai_collect", "Coletar dados para IA", "Registra amostras do jogo para o dataset (--ai-collect)."],
    ["layout_only", "Somente layout", "Ativa o modo de análise de layout (--layout-only)."],
    ["save_log", "Salvar arquivo de log", "Além da saída no painel, grava o log completo em disco (--log)."]
  ];
  for (const [key, title, description] of options) {
    const label = node("label", "run-option");
    const input = node("input");
    input.type = "checkbox";
    input.checked = draft.run[key];
    input.addEventListener("change", () => { draft.run[key] = input.checked; updatePending(); });
    const text = node("span");
    text.append(node("strong", "", title), node("small", "", description));
    label.append(input, text);
    basics.content.append(label);
  }
  $("profile-fields").replaceChildren(basics.section);
  const groups = new Map();
  for (const field of state.schema) {
    const key = field.group || "Outras configurações";
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

function settingField(field) {
  const row = node("div", "setting-row");
  const labelRow = node("div", "setting-label-row");
  const label = node("label", "", field.label || field.key);
  const overrideLabel = node("label", "check-label");
  const override = node("input");
  override.type = "checkbox";
  override.checked = own(draft.settings, field.key);
  override.setAttribute("aria-label", `Personalizar ${field.label || field.key}`);
  overrideLabel.append(override, document.createTextNode("Personalizar"));
  const type = field.type;
  const isBoolean = ["boolean", "bool"].includes(type) || typeof field.default === "boolean";
  const isNumber = ["number", "float", "int", "integer"].includes(type) || typeof field.default === "number";
  const input = node(isBoolean || field.options ? "select" : "input");
  input.id = `setting-${field.key}`;
  label.htmlFor = input.id;
  if (isBoolean) {
    for (const [value, text] of [["true", "Ativado"], ["false", "Desativado"]]) {
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
  const defaultValue = isBoolean ? field.default ? "Ativado" : "Desativado" : String(field.default ?? "—");
  inputRow.append(input, node("span", "default-chip", `Padrão: ${defaultValue}`));
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
  $("panel-items").querySelector(".notice").textContent = "A fidelidade é a confiança mínima para reconhecer um item. O ajuste do item tem prioridade sobre a categoria. Sem ajuste, vale o padrão da categoria; quando ela não tem padrão próprio, vale a Fidelidade da forma deste perfil. Valores maiores exigem uma correspondência mais exata.";
  const query = $("item-search").value.trim().toLocaleLowerCase("pt-BR");
  const sections = [];
  for (const category of state.templates.categories || []) {
    const categoryMatch = category.name.toLocaleLowerCase("pt-BR").includes(query);
    const items = (category.items || []).filter((item) => !query || categoryMatch || item.name.toLocaleLowerCase("pt-BR").includes(query));
    if (query && !categoryMatch && !items.length) continue;
    const section = node("details", "category");
    section.open = !!query;
    const heading = node("summary", "", category.name);
    heading.append(node("span", "count", `${count(category.count)} templates`));
    const content = node("div", "category-body");
    const control = node("div", "threshold-control");
    const label = node("label", "check-label");
    const override = node("input");
    override.type = "checkbox";
    override.checked = own(draft.category_thresholds, category.name);
    label.append(override, document.createTextNode("Fidelidade da categoria"));
    const input = thresholdInput(draft.category_thresholds[category.name] ?? defaultCategoryThreshold(category), `Fidelidade de ${category.name}`);
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
    const reset = button("Restaurar categoria e itens", "text-button", () => {
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
      itemOverride.setAttribute("aria-label", `Personalizar fidelidade de ${item.name}`);
      itemLabel.append(itemOverride);
      const identity = node("div");
      identity.append(node("span", "item-name", item.name), node("small", "", `${count(item.count || 1)} ${item.count === 1 ? "arquivo" : "arquivos"} · ${itemOverride.checked ? "Personalizado" : "Herdado"}`));
      const effective = () => draft.category_thresholds[category.name] ?? defaultCategoryThreshold(category);
      const itemInput = thresholdInput(draft.item_thresholds[item.key] ?? effective(), `Fidelidade de ${item.name}`);
      itemInput.disabled = !itemOverride.checked;
      const sync = () => {
        itemInput.disabled = !itemOverride.checked;
        if (!itemOverride.checked) itemInput.value = effective();
        identity.querySelector("small").textContent = `${count(item.count || 1)} ${item.count === 1 ? "arquivo" : "arquivos"} · ${itemOverride.checked ? "Personalizado" : "Herdado"}`;
      };
      inherited.push(sync);
      itemOverride.addEventListener("change", () => {
        if (itemOverride.checked) draft.item_thresholds[item.key] = Number(itemInput.value);
        else delete draft.item_thresholds[item.key];
        sync();
        updatePending();
      });
      itemInput.addEventListener("input", () => { if (itemInput.checkValidity()) draft.item_thresholds[item.key] = Number(itemInput.value); updatePending(); });
      const resetItem = button("↶", "reset-icon", () => { delete draft.item_thresholds[item.key]; itemOverride.checked = false; sync(); updatePending(); }, "Restaurar fidelidade do item");
      resetItem.setAttribute("aria-label", `Restaurar ${item.name}`);
      row.append(itemLabel, identity, itemInput, resetItem);
      content.append(row);
    }
    function refreshInherited() { inherited.forEach((sync) => sync()); }
    section.append(heading, content);
    sections.push(section);
  }
  $("item-fields").replaceChildren(...sections);
  if (!sections.length) $("item-fields").append(node("p", "section-description", query ? "Nenhum item encontrado." : "Nenhum template encontrado no servidor."));
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
    toast("Perfil salvo neste dispositivo. Aplique ao iniciar ou reiniciar o bot.");
    await refreshState();
  } catch (error) { if (authenticated) showError("profile-error", error.message); }
  finally { saving = false; updatePending(); }
}

async function resetProfile() {
  if (!draft || saving || !confirm("Restaurar o nome, as opções de execução e todos os ajustes deste dispositivo para os padrões? Essa alteração será salva imediatamente.")) return;
  saving = true;
  updatePending();
  try {
    await api(`/api/devices/${encodeURIComponent(detailId)}/profile`, { method: "DELETE" });
    if (!authenticated) return;
    savedProfile = normalizeProfile({});
    draft = clone(savedProfile);
    renderProfile();
    renderItems();
    showError("profile-error", "");
    toast("Perfil restaurado. Os padrões serão usados na próxima execução.");
    await refreshState();
  } catch (error) { if (authenticated) showError("profile-error", error.message); }
  finally { saving = false; updatePending(); }
}

function renderVisionControls() {
  const device = detailId ? deviceById(detailId) : null;
  $("vision-toggle").textContent = visionWanted ? "Encerrar visão" : visionMode === "error" ? "Tentar novamente" : "Ver visão";
  $("vision-toggle").disabled = visionMode === "starting" || (!visionWanted && (!device?.running || document.hidden));
}

function renderVision(mode, message = "") {
  visionMode = mode;
  const descriptions = {
    off: ["Transmissão desligada", "Ative a visão para acompanhar as detecções deste dispositivo.", "◎"],
    starting: ["Ativando visão", "Solicitando a transmissão ao bot em execução…", "◎"],
    waiting: ["Aguardando primeiro frame", "O bot está preparando a imagem com as detecções da IA.", "◎"],
    live: ["Ao vivo", "", "◎"],
    paused: ["Transmissão pausada", "A visão será retomada quando esta aba do navegador estiver visível.", "Ⅱ"],
    stopped: ["Bot parado", "Inicie o bot deste dispositivo para acompanhar as detecções.", "□"],
    error: ["Não foi possível exibir a visão", "Tente ativar a transmissão novamente.", "!"]
  };
  const [title, description, symbol] = descriptions[mode] || descriptions.off;
  $("vision-status").textContent = mode === "live" ? `Ao vivo · ${new Date().toLocaleTimeString("pt-BR")}` : title;
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
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
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
  releaseVision(id);
  renderVision(mode, message);
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
    const result = await api(`/api/devices/${encodeURIComponent(id)}/vision`, { method: "POST", body: JSON.stringify({ enabled: true }) });
    if (generation !== visionGeneration) {
      if (!visionWanted || visionDevice !== id) releaseVision(id);
      return;
    }
    if (result.enabled === false) throw new Error("O servidor não ativou a transmissão.");
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
    const timeout = setTimeout(() => finish(new Error("A imagem demorou para carregar. Tente novamente.")), 10000);
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
    const failedFrame = () => finish(new Error("Não foi possível carregar a imagem recebida do bot."));
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) { finish(null); return; }
    image.onload = loadedFrame;
    image.onerror = failedFrame;
    image.src = url;
  });
}

async function* jpegFrames(body, { boundary = "frame", signal, onReader } = {}) {
  if (!body || typeof body.getReader !== "function") throw new Error("O navegador não recebeu uma transmissão válida.");
  if (!/^[a-zA-Z0-9'()+_,./:=?-]{1,70}$/.test(boundary)) throw new Error("Separador da transmissão inválido.");
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
        if (phase !== "boundary" || lineLength) throw new Error("A transmissão foi interrompida durante um frame.");
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
        if (lineLength + length > MAX_VISION_HEADER_BYTES) throw new Error("Cabeçalhos da transmissão excedem 8 KiB.");
        lineBuffer.set(bytes.subarray(offset, end), lineLength);
        lineLength += length;
        offset = end;
        if (newline < 0) continue;
        if (lineLength < 2 || lineBuffer[lineLength - 2] !== 13) throw new Error("Formato da transmissão inválido.");
        const lineBytes = lineLength;
        const line = decoder.decode(lineBuffer.subarray(0, lineLength - 2));
        lineLength = 0;
        if (phase === "boundary") {
          if (line === `--${boundary}--`) return;
          if (line !== `--${boundary}`) throw new Error("Separador de frame inválido.");
          phase = "headers";
          headerBytes = 0;
          headers = new Map();
        } else if (phase === "headers") {
          headerBytes += lineBytes;
          if (headerBytes > MAX_VISION_HEADER_BYTES) throw new Error("Cabeçalhos da transmissão excedem 8 KiB.");
          if (line) {
            const match = line.match(/^([a-zA-Z0-9-]+):[ \t]*(.*)$/);
            if (!match || headers.has(match[1].toLowerCase())) throw new Error("Cabeçalho de frame inválido.");
            headers.set(match[1].toLowerCase(), match[2].trim());
          } else {
            const length = headers.get("content-length") || "";
            if (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)) || Number(length) < 1 || Number(length) > MAX_VISION_FRAME_BYTES) throw new Error("O frame deve ter até 5 MiB e tamanho informado.");
            if (!/^image\/jpeg(?:\s*;.*)?$/i.test(headers.get("content-type") || "")) throw new Error("O servidor não enviou um frame JPEG válido.");
            frame = new Uint8Array(Number(length));
            phase = "body";
          }
        } else {
          if (line !== "") throw new Error("Fim de frame inválido.");
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
    if (visionStream === stream && visionWanted) stopVision({ mode: "error", message: "Nenhum frame recebido há 10 segundos. Tente novamente." });
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
    const response = await fetch(`/api/devices/${encodeURIComponent(visionDevice)}/vision/stream`, {
      headers: { Accept: "multipart/x-mixed-replace", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      signal: controller.signal,
      cache: "no-store"
    });
    if (generation !== visionGeneration) return;
    if (response.status === 401) { logout(true); return; }
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      if (generation !== visionGeneration) return;
      const detail = typeof result.detail === "string" ? result.detail : "O servidor não disponibilizou a imagem.";
      if (response.status === 409 && !deviceById(visionDevice)?.running) { stopVision({ mode: "stopped" }); return; }
      throw new Error(detail);
    }
    const contentType = response.headers.get("Content-Type") || "";
    const boundary = contentType.match(/\bboundary=(?:"([^"]+)"|([^;\s]+))/i);
    if (!/^multipart\/x-mixed-replace\b/i.test(contentType) || !boundary) throw new Error("O servidor não enviou uma transmissão JPEG válida.");
    for await (const frame of jpegFrames(response.body, { boundary: boundary[1] || boundary[2], signal: controller.signal, onReader: (reader) => { stream.reader = reader; } })) {
      if (generation !== visionGeneration || !visionWanted) return;
      renewVisionTimeout(stream);
      queueVisionFrame(frame, stream);
    }
    if (generation === visionGeneration && visionWanted) throw new Error("A transmissão foi encerrada pelo servidor. Tente novamente.");
  } catch (error) {
    if (generation === visionGeneration && visionWanted) stopVision({ mode: "error", message: error.name === "AbortError" ? "A transmissão foi interrompida. Tente novamente." : error.message });
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
  $("log-status").textContent = "Aguardando logs.";
  $("log-pause").textContent = "Pausar";
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
    const size = encoder.encode(text).length + 1;
    logLines.push({ id: numeric(line.id), text, bytes: size });
    logBytes += size;
    while (logLines.length > MAX_LOG_LINES || logBytes > MAX_LOG_BYTES) logBytes -= logLines.shift().bytes;
  }
}

function renderLogLines() {
  const output = $("log-output");
  const scrollTop = output.scrollTop;
  output.replaceChildren(...logLines.map((line) => {
    const style = /(?:\bERROR\b|\b E \[|Traceback|Exception:)/.test(line.text) ? "error-line" : /(?:\bWARNING\b|\b W \[)/.test(line.text) ? "warning-line" : "";
    return node("div", style, line.text);
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
    $("log-status").textContent = logLines.length ? `${count(logLines.length)} linhas retidas · ${(logBytes / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} KiB · atualizado às ${new Date().toLocaleTimeString("pt-BR")}` : "Nenhum log disponível. Inicie o bot para acompanhar a saída.";
  } catch (error) { if (generation === logGeneration && authenticated) $("log-status").textContent = error.message; }
  finally { logLoading = false; }
}

function downloadLogs() {
  if (!logLines.length) { toast("Ainda não há linhas para baixar."); return; }
  const blob = new Blob([logLines.map((line) => line.text).join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = node("a");
  link.href = url;
  link.download = `eatventure-${(detailId || "bot").replace(/[^a-zA-Z0-9_-]/g, "_")}-${new Date().toISOString().slice(0, 10)}.log`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function connectAndroid(event) {
  event.preventDefault();
  if ($("connect-form").inert) return;
  const submit = $("connect-submit");
  submit.disabled = true;
  $("connect-form").inert = true;
  $("connect-result").textContent = "Conectando…";
  try {
    const result = await api("/api/adb/connect", { method: "POST", body: JSON.stringify({ address: $("connect-address").value.trim() }) });
    rememberAdbAddress(result.address);
    $("connect-result").textContent = "";
    $("connect-dialog").close();
    toast(result.message || result.output || "Conexão solicitada. Confira o dispositivo no painel.");
    await refreshState(true);
  } catch (error) { if (authenticated) $("connect-result").textContent = error.message; }
  finally { submit.disabled = false; $("connect-form").inert = false; }
}

async function pairAndroid() {
  if ($("connect-form").inert) return;
  const address = $("pair-address").value.trim();
  const code = $("pair-code").value.trim();
  if (!address || !/^\d{6}$/.test(code)) { $("connect-result").textContent = "Informe o endereço de pareamento e o código de 6 dígitos."; return; }
  $("pair-submit").disabled = true;
  $("connect-form").inert = true;
  $("connect-result").textContent = "Pareando…";
  try {
    const result = await api("/api/adb/pair", { method: "POST", body: JSON.stringify({ address, code }) });
    $("pair-code").value = "";
    $("connect-result").textContent = result.message || result.output || "Pareamento concluído. Informe o IP e a porta atual; o painel tentará mudar para 5555.";
  } catch (error) { if (authenticated) $("connect-result").textContent = error.message; }
  finally { $("pair-submit").disabled = false; $("connect-form").inert = false; }
}

$("login-form").addEventListener("submit", login);
$("session-retry").addEventListener("click", bootstrapSession);
$("logout").addEventListener("click", () => logout());
$("refresh").addEventListener("click", () => refreshState(true));
$("shutdown-server").addEventListener("click", async () => {
  if (!confirm("Encerrar o painel web e parar os bots em execução?")) return;
  $("shutdown-server").disabled = true;
  try {
    const result = await api("/api/server/shutdown", { method: "POST" });
    toast(result.message || "Encerrando o painel…");
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
  if (!draft || !confirm("Restaurar todas as fidelidades deste dispositivo? Salve o perfil para aplicar.")) return;
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
$("log-pause").addEventListener("click", () => { logPaused = !logPaused; $("log-pause").textContent = logPaused ? "Retomar" : "Pausar"; if (!logPaused) pollLogs(); });
$("log-download").addEventListener("click", downloadLogs);
$("vision-toggle").addEventListener("click", () => { if (visionWanted) stopVision(); else startVision(); });
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
window.addEventListener("beforeunload", (event) => { if (changeCount()) { event.preventDefault(); event.returnValue = ""; } });
setInterval(() => { if (!document.hidden) refreshState(); }, 5000);
setInterval(() => { if (!document.hidden) refreshRuntime(); }, 1000);
setInterval(() => { if (!document.hidden) pollLogs(); }, 1500);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (visionWanted) stopVision({ mode: "paused", pause: true });
  } else {
    refreshState();
    pollLogs();
    if (visionResume && activeTab === "vision") startVision();
  }
});
window.addEventListener("pagehide", () => stopVision());
bootstrapSession();
