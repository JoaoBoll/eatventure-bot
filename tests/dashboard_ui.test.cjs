const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.classList = { toggle: (name, enabled) => {
      const names = new Set(this.className.split(" ").filter(Boolean));
      if (enabled) names.add(name); else names.delete(name);
      this.className = [...names].join(" ");
    } };
  }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  click() { this.listeners.click?.(); }
  matches(selector) {
    if (selector.startsWith(".")) return this.className.split(" ").includes(selector.slice(1));
    const data = selector.match(/^\[data-([\w-]+)="([^"]+)"\]$/);
    if (data) return this.dataset[data[1].replace(/-([a-z])/g, (_, value) => value.toUpperCase())] === data[2];
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(",");
    return this.children.flatMap((child) => [
      ...(selectors.some((part) => child.matches(part)) ? [child] : []),
      ...child.querySelectorAll(selector)
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function environment() {
  const elements = new Map();
  const timers = new Map();
  const revoked = [];
  const blobs = [];
  let sequence = 0;
  const document = {
    hidden: false,
    activeElement: null,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => Object.assign(new Element("#text"), { textContent: text })
  };
  document.getElementById("status-filter").value = "all";
  const context = vm.createContext({
    document, AbortController, Uint8Array, TextEncoder, TextDecoder,
    Blob: class { constructor(parts, options = {}) { this.parts = parts; this.type = options.type; } },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    URL: { createObjectURL: (blob) => { blobs.push(blob); return `blob:frame-${++sequence}`; }, revokeObjectURL: (url) => revoked.push(url) },
    setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async () => { throw new Error("Unexpected request"); }
  });
  const source = fs.readFileSync(path.join(__dirname, "../src/dashboard/static/app.js"), "utf8");
  vm.runInContext(source.split('\n$("login-form").addEventListener("submit", login);')[0], context);
  return { context, elements, document, timers, revoked, blobs, run: (code) => vm.runInContext(code, context) };
}

function device(metrics = {}) {
  return { id: "android-1", serial: "serial-1", name: "Android", connected: true, running: true, status: "running", metrics };
}

test("card abre visão e engrenagem continua abrindo configurações", () => {
  const env = environment();
  env.context.device = device();
  env.run("var opened = []; openDetails = (id, tab) => opened.push([id, tab]); var card = renderDevice(device);");
  const card = env.context.card;
  const open = card.querySelector('[data-role="vision"]');
  assert.equal(open.type, "button");
  assert.equal(open.attributes["aria-haspopup"], "dialog");
  open.listeners.click();
  card.querySelector('[data-role="settings"]').listeners.click();
  assert.equal(JSON.stringify(env.context.opened), JSON.stringify([["android-1", "vision"], ["android-1", "settings"]]));
});

test("detalhes abrem modal antes de ativar automaticamente a visão", async () => {
  const env = environment();
  env.context.device = device();
  env.run(`
    authenticated = true; state.devices = [device];
    closeDetails = async () => true; api = async () => ({});
    renderProfile = renderItems = resetLogs = updatePending = renderDetailStatus = () => {};
    var openedTab;
    setTab = (tab) => { if (!$("device-dialog").open) throw new Error("Modal fechado"); openedTab = tab; };
  `);
  await env.run('openDetails("android-1")');
  assert.equal(env.context.openedTab, "vision");
  assert.equal(env.document.getElementById("device-dialog").open, true);
});

test("runtime atualiza métricas sem recriar cards nem esperar descoberta ADB", async () => {
  const env = environment();
  env.context.initial = device({ acoes: 3, updated_at: Date.now() / 1000 });
  env.context.next = device({ acoes: 8, voos: 2, reformas: 1, updated_at: Date.now() / 1000 });
  env.run(`
    authenticated = true; stateLoading = true; state.devices = [initial];
    var requested = []; api = async (url) => { requested.push(url); return { devices: [next] }; };
    var card = renderDevice(initial); $("devices").append(card);
  `);
  const card = env.context.card;
  await env.run("refreshRuntime()");
  assert.equal(env.document.getElementById("devices").children[0], card);
  assert.equal(card.querySelector('[data-metric="acoes"]').textContent, "8");
  assert.equal(card.querySelector('[data-metric="voos"]').textContent, "2");
  assert.equal(env.document.getElementById("kpi-actions").textContent, "8");
  assert.equal(JSON.stringify(env.context.requested), '["/api/runtime"]');
});

test("frame anterior só é liberado após carregar o próximo", async () => {
  const env = environment();
  env.context.controller = new AbortController();
  env.run('visionWanted = true; visionGeneration = 3; visionUrl = "blob:previous";');
  const pending = env.run("loadVisionFrame({ size: 10 }, 3, controller.signal)");
  assert.equal(env.revoked.includes("blob:previous"), false);
  env.document.getElementById("vision-image").onload();
  assert.equal(await pending, true);
  assert.equal(env.revoked.includes("blob:previous"), true);
  assert.equal(env.document.getElementById("vision-image").hidden, false);
});

test("cancelar carregamento resolve frame e libera URLs sem exibir imagem antiga", async () => {
  const env = environment();
  const controller = new AbortController();
  env.context.controller = controller;
  env.run('visionWanted = true; visionGeneration = 4; visionUrl = "blob:previous";');
  const pending = env.run("loadVisionFrame({ size: 10 }, 4, controller.signal)");
  controller.abort();
  assert.equal(await pending, false);
  assert.equal(env.revoked.length, 2);
  assert.equal(env.document.getElementById("vision-image").onload, null);
});

function multipartFrame(payload) {
  const bytes = Buffer.from(payload);
  return Buffer.concat([Buffer.from(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${bytes.length}\r\n\r\n`), bytes, Buffer.from("\r\n")]);
}

function byteStream(chunks = [], open = false) {
  const queue = [...chunks];
  let closed = !open;
  let pending;
  const result = { cancelled: 0, released: false };
  const reader = {
    read() {
      if (queue.length) return Promise.resolve({ value: queue.shift(), done: false });
      if (closed) return Promise.resolve({ done: true });
      return new Promise((resolve) => { pending = resolve; });
    },
    cancel() { result.cancelled += 1; closed = true; pending?.({ done: true }); pending = null; return Promise.resolve(); },
    releaseLock() { result.released = true; }
  };
  result.body = { getReader: () => reader };
  result.push = (bytes) => {
    if (pending) { pending({ value: bytes, done: false }); pending = null; }
    else queue.push(bytes);
  };
  return result;
}

function prepareVision(env, generation = 5) {
  env.context.device = device();
  env.run(`authenticated = true; state.devices = [device]; detailId = visionDevice = "android-1"; activeTab = "vision"; visionWanted = true; visionGeneration = ${generation}; $("device-dialog").open = true; renderVision("waiting");`);
}

test("parser suporta chunks de um byte e vários frames no mesmo chunk", async () => {
  for (const fragmented of [false, true]) {
    const env = environment();
    const bytes = Buffer.concat([multipartFrame([1, 2, 3]), multipartFrame([4, 5]), Buffer.from("--frame--\r\n")]);
    const stream = byteStream(fragmented ? [...bytes].map((value) => Uint8Array.of(value)) : [bytes]);
    env.context.body = stream.body;
    const frames = [];
    for await (const frame of env.run("jpegFrames(body)")) frames.push([...frame]);
    assert.deepEqual(frames, [[1, 2, 3], [4, 5]]);
    assert.equal(stream.released, true);
  }
});

test("parser usa Content-Length mesmo quando os bytes parecem um separador", async () => {
  const env = environment();
  const payload = Buffer.from("\r\n--frame\r\nbytes");
  env.context.body = byteStream([multipartFrame(payload)]).body;
  const frames = [];
  for await (const frame of env.run("jpegFrames(body)")) frames.push(Buffer.from(frame));
  assert.deepEqual(frames, [payload]);
});

test("parser rejeita frame acima de 5 MiB, cabeçalhos acima de 8 KiB e corpo truncado", async () => {
  const inputs = [
    [Buffer.from("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 5242881\r\n\r\n"), /5 MiB/],
    [Buffer.from(`--frame\r\nX-Long: ${"x".repeat(8200)}\r\n`), /8 KiB/],
    [Buffer.from("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 10\r\n\r\nx"), /interrompida/]
  ];
  for (const [input, message] of inputs) {
    const env = environment();
    const stream = byteStream([input]);
    env.context.body = stream.body;
    await assert.rejects(async () => { for await (const frame of env.run("jpegFrames(body)")) void frame; }, message);
    assert.equal(stream.cancelled, 1);
    assert.equal(stream.released, true);
  }
});

test("decoder lento mantém apenas o frame mais recente pendente", async () => {
  const env = environment();
  env.run(`
    visionWanted = true; visionGeneration = 6;
    var seen = [], finished = [];
    loadVisionFrame = (blob) => { seen.push([...blob.parts[0]]); return new Promise((resolve) => finished.push(resolve)); };
    var stream = { generation: 6, controller: new AbortController(), pending: null, rendering: false };
    visionStream = stream;
    queueVisionFrame(Uint8Array.of(1), stream);
    queueVisionFrame(Uint8Array.of(2), stream);
    queueVisionFrame(Uint8Array.of(3), stream);
  `);
  assert.equal(JSON.stringify(env.context.seen), "[[1]]");
  env.context.finished[0](true);
  await new Promise(setImmediate);
  assert.equal(JSON.stringify(env.context.seen), "[[1],[3]]");
  env.context.finished[1](true);
  await new Promise(setImmediate);
  assert.equal(env.context.stream.pending, null);
  assert.equal(env.context.stream.rendering, false);
});

test("stream usa uma conexão autenticada e stop cancela reader e carga da imagem", async () => {
  const env = environment();
  const stream = byteStream([multipartFrame([1, 2, 3])], true);
  const requests = [];
  env.context.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, headers: { get: () => "multipart/x-mixed-replace; boundary=frame" }, body: stream.body };
  };
  prepareVision(env, 7);
  env.run('token = "secret";');
  const pending = env.run("streamVision(7)");
  await new Promise(setImmediate);
  await env.run("streamVision(7)");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/devices/android-1/vision/stream");
  assert.equal(requests[0].options.headers.Authorization, "Bearer secret");
  assert.equal(requests[0].options.headers.Accept, "multipart/x-mixed-replace");
  assert.equal(typeof env.document.getElementById("vision-image").onload, "function");
  env.run("stopVision()");
  await pending;
  await new Promise(setImmediate);
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(stream.cancelled > 0, true);
  assert.equal(stream.released, true);
  assert.equal(env.document.getElementById("vision-image").onload, null);
  assert.equal(env.document.getElementById("vision-image").hidden, true);
  assert.equal(requests.filter((request) => request.options.method !== "POST").length, 1);
});

test("timeout de 10s renova a cada frame e exige retomada manual", async () => {
  const env = environment();
  const stream = byteStream([multipartFrame([1])], true);
  let requests = 0;
  env.context.fetch = async () => {
    requests += 1;
    return { ok: true, headers: { get: () => "multipart/x-mixed-replace; boundary=frame" }, body: stream.body };
  };
  prepareVision(env, 8);
  const pending = env.run("streamVision(8)");
  await new Promise(setImmediate);
  env.document.getElementById("vision-image").onload();
  await new Promise(setImmediate);
  const first = [...env.timers.keys()][0];
  stream.push(multipartFrame([2]));
  await new Promise(setImmediate);
  env.document.getElementById("vision-image").onload();
  await new Promise(setImmediate);
  assert.equal(env.timers.has(first), false);
  assert.equal(env.timers.size, 1);
  const timeout = [...env.timers.values()][0];
  assert.equal(timeout.delay, 10000);
  timeout.callback();
  await pending;
  await new Promise(setImmediate);
  assert.equal(env.run("visionMode"), "error");
  assert.equal(env.run("visionWanted"), false);
  assert.equal(requests, 2); // Uma conexão e a liberação da lease.
});

test("logs mantêm no máximo 500 linhas e download contém apenas as retidas", () => {
  const env = environment();
  env.context.lines = Array.from({ length: 700 }, (_, id) => ({ id, text: `line-${id}` }));
  env.run("appendLogLines(lines); renderLogLines(); downloadLogs();");
  assert.equal(env.run("logLines.length"), 500);
  assert.equal(env.run("logLines[0].id"), 200);
  assert.equal(env.document.getElementById("log-output").children.length, 500);
  const download = env.blobs[0].parts[0];
  assert.equal(download.startsWith("line-200\n"), true);
  assert.equal(download.includes("line-199\n"), false);
  assert.equal(download.endsWith("line-699\n"), true);
});

test("logs limitam bytes totais e texto UTF-8 por linha", () => {
  const env = environment();
  env.context.lines = Array.from({ length: 700 }, (_, id) => ({ id, text: "😀".repeat(10000) }));
  env.run("appendLogLines(lines);");
  assert.equal(env.run("logBytes <= 512 * 1024"), true);
  assert.equal(env.run("logLines.length < 500"), true);
  assert.equal(env.run("logLines.every((line) => new TextEncoder().encode(line.text).length <= 4096)"), true);
  assert.equal(env.run('logLines.every((line) => !line.text.includes("�") && line.text.endsWith("…"))'), true);
});

test("fechar detalhes ou sair libera logs e elementos retidos", async () => {
  const env = environment();
  env.run('appendLogLines([{ id: 1, text: "log" }]); renderLogLines();');
  await env.run("closeDetails()");
  assert.equal(env.run("logBytes"), 0);
  assert.equal(env.run("logLines.length"), 0);
  assert.equal(env.document.getElementById("log-output").children.length, 0);
  env.run('appendLogLines([{ id: 2, text: "outro log" }]); renderLogLines(); logout();');
  assert.equal(env.run("logBytes"), 0);
  assert.equal(env.run("logLines.length"), 0);
  assert.equal(env.document.getElementById("log-output").children.length, 0);
});
