"use strict";

/* ------------------------------------------------------------------ utils */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let msg = `Fehler ${res.status}`;
    if (data && typeof data.detail === "string") msg = data.detail;
    else if (data && Array.isArray(data.detail)) msg = data.detail.map(d => d.msg).join(", ");
    throw new Error(msg);
  }
  return data;
}

function toast(msg, isError = false) {
  const t = h("div", { class: "toast" + (isError ? " err" : ""), role: isError ? "alert" : "status", text: msg });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), isError ? 7000 : 3500);
}

async function attempt(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message, true);
    return undefined;
  }
}

/* ------------------------------------------------------------------- tabs */

function selectTab(name) {
  $$(".tabs [role=tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab").forEach(s => { s.hidden = s.id !== "tab-" + name; });
  if (name === "catalog") loadCatalog();
  try { localStorage.setItem("tab", name); } catch (_) { /* private mode */ }
}
$$(".tabs [role=tab]").forEach(b => b.addEventListener("click", () => selectTab(b.dataset.tab)));
document.addEventListener("click", e => {
  const a = e.target.closest("[data-goto]");
  if (a) { e.preventDefault(); selectTab(a.dataset.goto); }
});

/* ---------------------------------------------------------------- preview */

const screen = {
  canvas: $("#screen"),
  frames: [],
  speed: 100,
  digest: "",
  index: 0,
  timer: null,
};

function paintFrame(bytes) {
  const c = screen.canvas, ctx = c.getContext("2d");
  const size = c.width, cell = size / 64, r = cell * 0.42;
  const dark = getComputedStyle(document.documentElement).getPropertyValue("--led-off").trim() || "#222";
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--bezel").trim() || "#141414";
  ctx.fillRect(0, 0, size, size);
  for (let y = 0; y < 64; y++) {
    for (let x = 0; x < 64; x++) {
      let fill = dark;
      if (bytes) {
        const i = (y * 64 + x) * 3;
        const R = bytes[i], G = bytes[i + 1], B = bytes[i + 2];
        if (R + G + B > 0) fill = `rgb(${R},${G},${B})`;
      }
      ctx.fillStyle = fill;
      ctx.beginPath();
      ctx.arc(x * cell + cell / 2, y * cell + cell / 2, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function decodeFrame(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function playPreview() {
  clearInterval(screen.timer);
  screen.index = 0;
  paintFrame(screen.frames[0] || null);
  if (screen.frames.length > 1) {
    screen.timer = setInterval(() => {
      screen.index = (screen.index + 1) % screen.frames.length;
      paintFrame(screen.frames[screen.index]);
    }, Math.max(40, screen.speed));
  }
}

async function pollPreview() {
  try {
    const p = await api("GET", "/api/preview?digest=" + encodeURIComponent(screen.digest));
    if (!p.unchanged) {
      screen.digest = p.digest;
      screen.frames = p.frames.map(decodeFrame);
      screen.speed = p.speed || 100;
      playPreview();
    }
  } catch (_) { /* hub restarting; try again */ }
}

/* ------------------------------------------------------------------ state */

let hubState = null;
let plugins = [];
let settingsDirty = false;

const CONN_TEXT = {
  online: ip => `verbunden · ${ip}`,
  offline: ip => `nicht erreichbar · ${ip || "?"}`,
  searching: () => "suche Pixoo …",
  "no-device": () => "kein Pixoo gefunden",
  idle: () => "startet …",
};

function nameOf(id) {
  const p = plugins.find(p => p.id === id);
  return p ? p.name : id;
}

function renderState() {
  const s = hubState;
  const conn = $("#conn");
  conn.dataset.state = s.connection.connection;
  $("#conn-text").textContent = (CONN_TEXT[s.connection.connection] || CONN_TEXT.idle)(s.connection.ip);
  conn.title = s.connection.error || "";

  const sh = s.showing, line = $("#showing");
  line.replaceChildren();
  if (sh.kind === "frames") line.append("Zeigt ", h("b", { text: nameOf(sh.owner) }));
  else if (sh.kind === "clock") line.append("Nichts zu zeigen · Divoom-Uhr");
  else if (sh.reason === "quiet hours") line.append("Ruhezeit · Display aus");
  else line.append("Nichts zu zeigen · Display aus");
  if (s.connection.connection !== "online") line.append(h("br"), h("span", { class: "help", text: "(Vorschau – Gerät nicht verbunden)" }));

  const qb = $("#q-brightness");
  if (document.activeElement !== qb) qb.value = s.device.brightness;
  $("#q-brightness-out").textContent = qb.value + "%";
  $$(".seg [data-mode]").forEach(b => b.setAttribute("aria-checked", String(b.dataset.mode === s.display.mode)));

  $("#order-hint").textContent = s.display.mode === "priority"
    ? "Wollen mehrere Plugins gleichzeitig anzeigen, gewinnt das oberste. Reihenfolge per Ziehen oder Pfeilen ändern."
    : `Rotation: Plugins mit etwas zum Anzeigen wechseln sich alle ${s.display.rotation_seconds} s ab, in dieser Reihenfolge.`;

  if (!settingsDirty) fillSettings();
}

async function pollState() {
  try {
    hubState = await api("GET", "/api/state");
    renderState();
    updatePluginRows();
  } catch (_) {
    $("#conn").dataset.state = "offline";
    $("#conn-text").textContent = "Hub nicht erreichbar";
  }
}

async function saveSettings(partial) {
  const body = {
    device: { ...hubState.device },
    display: JSON.parse(JSON.stringify(hubState.display)),
  };
  if (partial.device) Object.assign(body.device, partial.device);
  if (partial.display) Object.assign(body.display, partial.display);
  await api("PUT", "/api/settings", body);
  await pollState();
}

/* quick controls */
let brightnessTimer = null;
$("#q-brightness").addEventListener("input", e => {
  $("#q-brightness-out").textContent = e.target.value + "%";
  clearTimeout(brightnessTimer);
  brightnessTimer = setTimeout(() => attempt(() => saveSettings({ device: { brightness: +e.target.value } })), 250);
});
$$(".seg [data-mode]").forEach(b => b.addEventListener("click", () =>
  attempt(() => saveSettings({ display: { mode: b.dataset.mode } }))));

/* ---------------------------------------------------------- settings tab */

function fillSettings() {
  const s = hubState;
  $("#s-ip").value = s.device.ip || "";
  $("#s-brightness").value = s.device.brightness;
  $("#s-brightness-out").textContent = s.device.brightness + "%";
  $$("input[name=mode]").forEach(r => { r.checked = r.value === s.display.mode; });
  $$("input[name=idle]").forEach(r => { r.checked = r.value === s.display.idle; });
  $("#s-rotation").value = s.display.rotation_seconds;
  $("#s-quiet").checked = s.display.quiet_hours.enabled;
  $("#s-quiet-start").value = s.display.quiet_hours.start;
  $("#s-quiet-end").value = s.display.quiet_hours.end;
}

$("#settings-form").addEventListener("input", e => {
  settingsDirty = true;
  if (e.target.id === "s-brightness") $("#s-brightness-out").textContent = e.target.value + "%";
});
$("#settings-form").addEventListener("submit", async e => {
  e.preventDefault();
  const ok = await attempt(() => saveSettings({
    device: { ip: $("#s-ip").value.trim(), brightness: +$("#s-brightness").value },
    display: {
      mode: $("input[name=mode]:checked")?.value || "priority",
      idle: $("input[name=idle]:checked")?.value || "off",
      rotation_seconds: Math.max(5, +$("#s-rotation").value || 15),
      quiet_hours: {
        enabled: $("#s-quiet").checked,
        start: $("#s-quiet-start").value || "23:00",
        end: $("#s-quiet-end").value || "07:00",
      },
    },
  }).then(() => true), "Gespeichert");
  if (ok) { settingsDirty = false; fillSettings(); }
});

$("#discover").addEventListener("click", async e => {
  const btn = e.currentTarget, out = $("#discover-result");
  btn.disabled = true;
  out.textContent = "Suche läuft, das dauert ein paar Sekunden …";
  const r = await attempt(() => api("POST", "/api/device/discover"));
  btn.disabled = false;
  out.replaceChildren();
  if (!r) return;
  if (!r.found.length) {
    out.textContent = "Nichts gefunden. Läuft der Hub in Docker ohne host-Netzwerk, bitte die IP von Hand eintragen (steht in der Divoom-App unter Geräteinformationen).";
    return;
  }
  out.append("Gefunden: ");
  r.found.forEach((ip, i) => {
    if (i) out.append(", ");
    out.append(h("a", { href: "#", text: ip, onclick: ev => {
      ev.preventDefault();
      $("#s-ip").value = ip;
      settingsDirty = true;
    } }));
  });
  out.append(" – anklicken zum Übernehmen.");
});

/* --------------------------------------------------------------- plugins */

const PLACEHOLDER_ICON = "plugin-placeholder.svg";
const STATE_TEXT = {
  running: "läuft", starting: "startet", stopped: "aus", crashed: "abgestürzt", failed: "Fehler",
};
const rows = new Map();

async function loadPlugins() {
  try {
    const r = await api("GET", "/api/plugins");
    plugins = r.plugins;
    renderPluginList();
  } catch (_) { /* next poll */ }
}

function renderPluginList() {
  const list = $("#plugin-list");
  $("#plugins-empty").hidden = plugins.length > 0;
  const seen = new Set();
  plugins.forEach((p, i) => {
    seen.add(p.id);
    let row = rows.get(p.id);
    if (!row) {
      row = buildPluginRow(p);
      rows.set(p.id, row);
    }
    if (list.children[i] !== row.el) list.insertBefore(row.el, list.children[i] || null);
    row.data = p;
    $(".pos", row.el).textContent = String(i + 1).padStart(2, "0");
  });
  for (const [id, row] of rows) {
    if (!seen.has(id)) { row.el.remove(); rows.delete(id); }
  }
  updatePluginRows();
}

function updatePluginRows() {
  for (const p of plugins) {
    const row = rows.get(p.id);
    if (!row) continue;
    const el = row.el;
    $(".pname", el).textContent = p.name;
    const st = $(".pstatus", el);
    let status = p.status || p.description || "";
    let level = p.level || "info";
    if (p.outdated_api) { status = "Für eine ältere Hub-Version geschrieben – bitte aktualisieren."; level = "error"; }
    if (p.job && p.job.state === "running") { status = p.job.message + " …"; level = "info"; }
    if (!p.enabled) { status = "Deaktiviert"; level = "info"; }
    st.textContent = status;
    st.dataset.level = level;
    st.title = status;

    const onScreen = hubState && hubState.showing.owner === p.id;
    const stateEl = $(".pstate", el);
    stateEl.textContent = onScreen ? "auf dem Display" : (p.enabled ? STATE_TEXT[p.state] || p.state || "" : "");
    stateEl.toggleAttribute("data-on-screen", onScreen);

    const toggle = $(".penabled", el);
    if (document.activeElement !== toggle) toggle.checked = p.enabled;
    const upd = $("[data-op=update]", el);
    upd.hidden = !p.update_available;
    if (p.update_available) upd.textContent = p.latest_version && p.latest_version !== p.version
      ? `Auf ${p.latest_version} aktualisieren` : "Aktualisieren";
    $(".pmeta", el).textContent = [
      `v${p.version}`, p.author && `von ${p.author}`, p.commit && `Commit ${p.commit}`, p.source,
    ].filter(Boolean).join(" · ");
  }
}

function buildPluginRow(p) {
  const el = $("#tpl-plugin").content.firstElementChild.cloneNode(true);
  el.dataset.id = p.id;
  const row = { el, data: p, formBuiltFor: null };
  const icon = $(".picon", el);
  icon.src = p.has_icon ? `/api/plugins/${encodeURIComponent(p.id)}/icon` : PLACEHOLDER_ICON;
  icon.onerror = () => { icon.onerror = null; icon.src = PLACEHOLDER_ICON; };
  $(".pdesc", el).textContent = p.description;

  const main = $(".pmain", el), body = $(".pbody", el);
  main.addEventListener("click", () => {
    const open = body.hidden;
    body.hidden = !open;
    main.setAttribute("aria-expanded", String(open));
    if (open) buildForm(row);
  });

  $(".penabled", el).addEventListener("change", async e => {
    const on = e.target.checked;
    await attempt(() => api("POST", `/api/plugins/${encodeURIComponent(p.id)}/enabled`, { enabled: on }),
      on ? "Aktiviert" : "Deaktiviert");
    loadPlugins();
  });

  $$("[data-move]", el).forEach(b => b.addEventListener("click", () => moveRow(p.id, +b.dataset.move)));

  $(".pfoot", el).addEventListener("click", async e => {
    const op = e.target.closest("[data-op]")?.dataset.op;
    if (!op) return;
    const id = encodeURIComponent(row.data.id);
    if (op === "save") {
      const ok = await attempt(() => api("PUT", `/api/plugins/${id}/config`, collectForm(row)), "Gespeichert, Plugin startet neu");
      if (ok) { row.formBuiltFor = null; setTimeout(loadPlugins, 400); }
    } else if (op === "restart") {
      await attempt(() => api("POST", `/api/plugins/${id}/restart`), "Startet neu");
    } else if (op === "logs") {
      openLogs(row.data);
    } else if (op === "update") {
      await attempt(() => api("POST", `/api/plugins/${id}/update`), "Update läuft");
    } else if (op === "remove") {
      if (!confirm(`${row.data.name} wirklich entfernen? Einstellungen und gespeicherte Daten des Plugins werden gelöscht.`)) return;
      await attempt(() => api("DELETE", `/api/plugins/${id}`), "Wird entfernt");
    }
    setTimeout(loadPlugins, 300);
  });

  setupDrag(el);
  return row;
}

/* ordering */
async function saveOrder(ids) {
  await attempt(() => api("PUT", "/api/order", ids));
  await loadPlugins();
  pollState();
}

function moveRow(id, delta) {
  const ids = plugins.map(p => p.id);
  const i = ids.indexOf(id), j = i + delta;
  if (j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  saveOrder(ids);
}

let dragId = null;
function setupDrag(el) {
  const grip = $(".grip", el);
  grip.addEventListener("pointerdown", () => { el.draggable = true; });
  el.addEventListener("dragstart", e => {
    dragId = el.dataset.id;
    el.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragId);
  });
  el.addEventListener("dragend", () => {
    el.draggable = false;
    el.classList.remove("dragging");
    $$(".pitem").forEach(x => x.classList.remove("drop-before", "drop-after"));
  });
  el.addEventListener("dragover", e => {
    if (!dragId || dragId === el.dataset.id) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    el.classList.toggle("drop-after", after);
    el.classList.toggle("drop-before", !after);
  });
  el.addEventListener("dragleave", () => el.classList.remove("drop-before", "drop-after"));
  el.addEventListener("drop", e => {
    e.preventDefault();
    const after = el.classList.contains("drop-after");
    el.classList.remove("drop-before", "drop-after");
    const ids = plugins.map(p => p.id).filter(x => x !== dragId);
    let idx = ids.indexOf(el.dataset.id) + (after ? 1 : 0);
    ids.splice(idx, 0, dragId);
    dragId = null;
    saveOrder(ids);
  });
}

/* ------------------------------------------------------ generated forms */

function linkify(text) {
  // plain text with http(s) links made clickable; never innerHTML
  const out = [];
  const re = /(https?:\/\/[^\s)]+)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(h("a", { href: m[1], target: "_blank", rel: "noopener", text: m[1] }));
    last = m.index + m[1].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function buildForm(row) {
  const p = row.data;
  const key = JSON.stringify([p.settings, p.config]);
  if (row.formBuiltFor === key) return;
  row.formBuiltFor = key;
  const form = $(".pform", row.el);
  form.replaceChildren();
  form.onsubmit = e => e.preventDefault();
  if (!p.settings.length) form.append(h("p", { class: "note", text: "Dieses Plugin hat keine Einstellungen." }));

  for (const f of p.settings) {
    const id = `f-${p.id}-${f.name || Math.random().toString(36).slice(2)}`;
    const val = f.name in p.config ? p.config[f.name] : f.default;
    const help = f.info ? h("span", { class: "help" }, linkify(f.info)) : null;
    let control;

    switch (f.type) {
      case "header":
        form.append(h("h4", { text: f.label }));
        continue;
      case "note":
        form.append(h("p", { class: "note" }, linkify(f.label || f.text || "")));
        continue;
      case "switch":
        form.append(h("label", { class: "check" },
          h("span", { class: "switch" }, h("input", { type: "checkbox", id, name: f.name, checked: !!val }), h("span")),
          h("span", { text: f.label })));
        if (help) form.append(help);
        continue;
      case "select":
        control = h("select", { id, name: f.name },
          (f.options || []).map(o => {
            const v = typeof o === "object" ? o.value : o, l = typeof o === "object" ? o.label : o;
            return h("option", { value: v, selected: String(v) === String(val), text: l });
          }));
        break;
      case "multiselect": {
        const sel = new Set(Array.isArray(val) ? val.map(String) : []);
        control = h("div", { class: "checks", "data-multi": f.name, role: "group", "aria-label": f.label },
          (f.options || []).map(o => {
            const v = typeof o === "object" ? o.value : o, l = typeof o === "object" ? o.label : o;
            return h("label", {}, h("input", { type: "checkbox", value: v, checked: sel.has(String(v)) }), l);
          }));
        form.append(h("div", { class: "field" }, h("span", { class: "lbl", text: f.label }), control, help));
        continue;
      }
      case "slider": {
        const out = h("output", { class: "mono", text: val ?? "" });
        const input = h("input", { type: "range", id, name: f.name, min: f.min ?? 0, max: f.max ?? 100, step: f.step ?? 1, value: val ?? 0,
          oninput: e => { out.textContent = e.target.value; } });
        control = h("div", { class: "slider-row" }, input, out);
        break;
      }
      case "textarea":
        control = h("textarea", { id, name: f.name, placeholder: f.placeholder || "" });
        control.value = val ?? "";
        break;
      case "action": {
        const outEl = h("span", { class: "action-out" });
        const btn = h("button", { type: "button", class: "btn ghost", text: f.label || f.name });
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          outEl.textContent = "…";
          outEl.dataset.ok = "true";
          try {
            const r = await api("POST", `/api/plugins/${encodeURIComponent(p.id)}/action`,
              { name: f.name, values: collectForm(row) });
            outEl.replaceChildren(...linkify(r.message || ""));
            outEl.dataset.ok = String(r.ok !== false);
            if (r.open_url) {
              outEl.append(" ", h("a", { href: r.open_url, target: "_blank", rel: "noopener", text: "Link öffnen" }));
              window.open(r.open_url, "_blank", "noopener");
            }
            for (const [k, v] of Object.entries(r.values || {})) {
              const input = form.elements.namedItem(k);
              if (input) input.value = v;
            }
          } catch (e) {
            outEl.textContent = e.message;
            outEl.dataset.ok = "false";
          } finally {
            btn.disabled = false;
          }
        });
        form.append(h("div", { class: "field" }, h("div", { class: "action-row" }, btn, outEl), help));
        continue;
      }
      default: {
        const type = f.type === "password" ? "password" : f.type === "number" ? "number" : "text";
        control = h("input", { type, id, name: f.name, placeholder: f.placeholder || "", autocomplete: "off",
          spellcheck: "false", min: f.min, max: f.max, step: f.step });
        control.value = val ?? "";
        if (f.type === "password" && val) control.placeholder = "gespeichert";
      }
    }
    form.append(h("label", { class: "field", for: id }, h("span", { class: "lbl", text: f.label || f.name }), control, help));
  }
}

function collectForm(row) {
  const form = $(".pform", row.el);
  const out = {};
  for (const f of row.data.settings) {
    if (!f.name || ["header", "note", "action"].includes(f.type)) continue;
    if (f.type === "multiselect") {
      const box = form.querySelector(`[data-multi="${CSS.escape(f.name)}"]`);
      out[f.name] = box ? $$("input:checked", box).map(i => i.value) : [];
      continue;
    }
    const input = form.elements.namedItem(f.name);
    if (!input) continue;
    out[f.name] = f.type === "switch" ? input.checked : input.value;
  }
  return out;
}

/* ------------------------------------------------------------------ logs */

let logTimer = null, logSince = 0;
function openLogs(p) {
  const dlg = $("#log-dialog"), body = $("#log-body");
  $("#log-title").textContent = `Protokoll · ${p.name}`;
  body.replaceChildren();
  logSince = 0;
  const pull = async () => {
    try {
      const r = await api("GET", `/api/plugins/${encodeURIComponent(p.id)}/logs?since=${logSince}`);
      const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
      for (const l of r.lines) {
        logSince = Math.max(logSince, l.t);
        const ts = new Date(l.t * 1000).toLocaleTimeString();
        body.append(h("div", { class: l.src === "hub" ? "hub" : "" }, `${ts}  ${l.line}`));
      }
      if (stick) body.scrollTop = body.scrollHeight;
    } catch (_) { /* ignore */ }
  };
  pull();
  logTimer = setInterval(pull, 1500);
  dlg.showModal();
}
$("#log-dialog").addEventListener("close", () => clearInterval(logTimer));
$("#log-dialog [data-close]").addEventListener("click", () => $("#log-dialog").close());

/* --------------------------------------------------------------- catalog */

async function loadCatalog() {
  const r = await attempt(() => api("GET", "/api/catalog"));
  if (!r) return;
  const list = $("#catalog-list");
  list.replaceChildren();
  if (!r.repositories.length) list.append(h("li", { class: "empty", text: "Keine Repositories eingetragen." }));
  let pending = false;
  for (const repo of r.repositories) {
    const m = repo.manifest;
    const installed = repo.installed ? plugins.find(p => p.id === repo.installed) : null;
    // updates of installed plugins run under the plugin id, installs under the URL
    const running = [repo.job, installed && installed.job].find(j => j && j.state === "running");
    pending = pending || repo.pending || !!running;
    const action = h("div", { class: "cact" });
    if (running) action.append(h("span", { class: "mono", text: running.message + " …" }));
    else if (installed && installed.update_available) {
      const target = installed.latest_version && installed.latest_version !== installed.version
        ? `Auf ${installed.latest_version} aktualisieren` : "Aktualisieren";
      action.append(h("button", { class: "btn", type: "button", text: target, onclick: async () => {
        await attempt(() => api("POST", `/api/plugins/${encodeURIComponent(installed.id)}/update`), "Update läuft");
        await loadPlugins();
        loadCatalog();
      } }));
    } else if (installed) {
      action.append(h("span", { class: "mono", text: `installiert · v${installed.version}` }));
    } else if (m && m.hub_api < 2) {
      action.append(h("span", { class: "mono", title: "Das Plugin wurde für die alte Hub-Version geschrieben und muss erst aktualisiert werden.", text: "nicht kompatibel" }));
    } else if (m) {
      action.append(h("button", { class: "btn", type: "button", text: "Installieren", onclick: async () => {
        await attempt(() => api("POST", "/api/install", { url: repo.url }), "Installation läuft");
        setTimeout(loadCatalog, 500);
      } }));
    }
    action.append(h("button", { class: "icon-btn", type: "button", "aria-label": "Repository entfernen", title: "Aus Liste entfernen", text: "×",
      onclick: async () => { await attempt(() => api("DELETE", "/api/repositories?url=" + encodeURIComponent(repo.url))); loadCatalog(); } }));

    const icon = h("img", { alt: "", src: repo.icon || PLACEHOLDER_ICON });
    icon.onerror = () => { icon.onerror = null; icon.src = PLACEHOLDER_ICON; };
    let info;
    if (m) {
      info = h("div", {},
        h("h3", {}, m.name, h("span", { class: "mono", text: "v" + m.version })),
        h("p", { text: m.description }),
        h("span", { class: "src", text: repo.url }));
    } else {
      info = h("div", {},
        h("h3", { text: repo.url.split("/").slice(-1)[0] }),
        repo.error ? h("p", { class: "cerr", text: repo.error }) : h("p", { text: "Wird gelesen …" }),
        h("span", { class: "src", text: repo.url }));
    }
    if (repo.job && repo.job.state === "error") info.append(h("p", { class: "cerr", text: repo.job.message }));
    list.append(h("li", { class: "citem" }, icon, info, action));
  }
  if (pending && !$("#tab-catalog").hidden) setTimeout(() => loadPlugins().then(loadCatalog), 1500);
}

$("#repo-form").addEventListener("submit", async e => {
  e.preventDefault();
  const url = $("#repo-url").value.trim();
  const ok = await attempt(() => api("POST", "/api/repositories", { url }), "Repository hinzugefügt");
  if (ok) $("#repo-url").value = "";
  loadCatalog();
});
$("#catalog-refresh").addEventListener("click", async () => {
  await attempt(() => api("POST", "/api/catalog/refresh"), "Suche nach Updates …");
  setTimeout(loadCatalog, 2500);
  setTimeout(loadPlugins, 5000);
});

/* ------------------------------------------------------------------ boot */

(async function boot() {
  paintFrame(null);
  let tab = "plugins";
  try { tab = localStorage.getItem("tab") || tab; } catch (_) { /* ignore */ }
  await loadPlugins();
  await pollState();
  selectTab($(`.tabs [data-tab="${tab}"]`) ? tab : "plugins");
  pollPreview();
  setInterval(pollPreview, 1000);
  setInterval(pollState, 2000);
  setInterval(loadPlugins, 4000);
})();
