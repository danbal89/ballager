/* BALLager – Weboberfläche (ohne Build-Schritt, reines JavaScript) */
"use strict";

// ---------------------------------------------------------------------------
// Grundlagen
// ---------------------------------------------------------------------------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("ballager." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ballager." + k, JSON.stringify(v)); } catch { /* egal */ } },
};
const view = $("#view");
const S = {
  meta: null,
  items: [],
  scanMode: store.get("scanMode", "in"),
  lastLocation: store.get("lastLocation", null),
  refresh: null, // aktuelle Seite neu zeichnen
};
const UNITS = ["Stk", "Packung", "Dose", "Glas", "Flasche", "Tube", "Rolle", "Beutel", "Karton", "kg", "g", "l", "ml"];
const KINDS = {
  einlagern: ["📥", "eingelagert"], verbrauch: ["✅", "verbraucht"], entsorgt: ["🗑️", "entsorgt"],
  korrektur: ["✏️", "korrigiert"], umlagern: ["🔀", "umgelagert"],
};

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  const r = await fetch("api/" + path, o);
  let data = null;
  try { data = await r.json(); } catch { /* kein JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `Fehler ${r.status}`);
  return data;
}

async function run(fn) {
  try { return await fn(); } catch (e) { console.error(e); toast(e.message || String(e), { error: true }); return undefined; }
}

// ---------------------------------------------------------------------------
// Datum
// ---------------------------------------------------------------------------
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseISO = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const today = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
const daysLeft = (s) => Math.round((parseISO(s) - today()) / 86400000);
const fmtDate = (s) => (s ? parseISO(s).toLocaleDateString("de-AT", { day: "2-digit", month: "2-digit", year: "numeric" }) : "");
const fmtStamp = (s) => new Date(s).toLocaleString("de-AT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

function expStatus(s) {
  if (!s) return "none";
  const d = daysLeft(s);
  if (d < 0) return "expired";
  if (d <= (S.meta?.ui?.soon_days ?? 30)) return "soon";
  return "ok";
}
function expiryBadge(s, long = false) {
  if (!s) return `<span class="badge">∞ ohne Datum</span>`;
  const d = daysLeft(s);
  const st = expStatus(s);
  if (st === "expired") return `<span class="badge expired">⛔ ${long ? "abgelaufen seit " + fmtDate(s) : "abgelaufen"}</span>`;
  if (st === "soon") {
    const txt = d === 0 ? "heute!" : d === 1 ? "morgen" : `in ${d} Tagen`;
    return `<span class="badge soon">⏰ ${txt}${long ? " · " + fmtDate(s) : ""}</span>`;
  }
  return `<span class="badge ok">✓ ${fmtDate(s)}</span>`;
}

/** Freie Datumseingabe: 31.12.27 · 31122027 · 12/27 · 12.2027 · 1227 · 2027-12-31 */
function parseExpiry(t) {
  t = (t || "").trim();
  if (!t) return null;
  const y2 = (y) => (y < 100 ? 2000 + y : y);
  const mk = (y, m, d) => { const x = new Date(y, m - 1, d); return x.getMonth() === m - 1 && x.getDate() === d && y > 1999 && y < 2200 ? iso(x) : undefined; };
  const eom = (y, m) => (m >= 1 && m <= 12 ? iso(new Date(y, m, 0)) : undefined);
  let m;
  if ((m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) return mk(+m[1], +m[2], +m[3]);
  if ((m = t.match(/^(\d{1,2})[.\/\- ](\d{1,2})[.\/\- ](\d{2}|\d{4})$/))) return mk(y2(+m[3]), +m[2], +m[1]);
  if ((m = t.match(/^(\d{1,2})[.\/\- ](\d{2}|\d{4})$/))) return eom(y2(+m[2]), +m[1]);
  if ((m = t.match(/^(\d{2})(\d{2})(\d{4})$/))) return mk(+m[3], +m[2], +m[1]);
  if ((m = t.match(/^(\d{2})(\d{2})(\d{2})$/))) return mk(2000 + +m[3], +m[2], +m[1]);
  if ((m = t.match(/^(\d{2})(\d{2})$/))) return eom(2000 + +m[2], +m[1]);
  return undefined; // nicht erkannt
}

// ---------------------------------------------------------------------------
// Toasts, Sheets, Rückfragen
// ---------------------------------------------------------------------------
function toast(msg, { error = false, action, onAction, ms = 3500 } = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " err" : "");
  el.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action;
    b.onclick = () => { el.remove(); onAction && onAction(); };
    el.appendChild(b);
    ms = Math.max(ms, 6500);
  }
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), error ? Math.max(ms, 5000) : ms);
}

let sheetOnClose = null;
function openSheet(html, onClose) {
  const wrap = $("#sheet");
  $("#sheet-body").innerHTML = html;
  wrap.hidden = false;
  document.body.style.overflow = "hidden";
  sheetOnClose = onClose || null;
  $(".sheet", wrap).scrollTop = 0;
  return $("#sheet-body");
}
function closeSheet(silent = false) {
  const wrap = $("#sheet");
  if (wrap.hidden) return;
  wrap.hidden = true;
  $("#sheet-body").innerHTML = "";
  $("#sheet-body").onclick = null;
  document.body.style.overflow = "";
  const cb = sheetOnClose;
  sheetOnClose = null;
  if (cb && !silent) cb();
}
$("#sheet").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

function ask(text, { ok = "Ja", danger = false } = {}) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "sheet-wrap";
    wrap.style.zIndex = 70;
    wrap.innerHTML = `<div class="sheet-backdrop"></div>
      <div class="sheet" role="alertdialog" style="max-width:420px">
        <h2 style="padding:0">${esc(text)}</h2>
        <div class="row" style="margin-top:18px">
          <button class="btn ghost block" data-a="0">Abbrechen</button>
          <button class="btn ${danger ? "danger" : "primary"} block" data-a="1">${esc(ok)}</button>
        </div></div>`;
    wrap.onclick = (e) => {
      const a = e.target.closest("[data-a]");
      if (a || e.target.classList.contains("sheet-backdrop")) { wrap.remove(); resolve(a?.dataset.a === "1"); }
    };
    document.body.appendChild(wrap);
  });
}

// ---------------------------------------------------------------------------
// Daten laden
// ---------------------------------------------------------------------------
async function loadMeta() { S.meta = await api("meta"); return S.meta; }
async function loadItems() { S.items = (await api("inventory")).items; return S.items; }
const cat = (id) => S.meta.categories.find((c) => c.id === id);
const loc = (id) => S.meta.locations.find((l) => l.id === id);
const findItem = (pid) => S.items.find((p) => p.id === pid);

function avatar(p, big = false) {
  const color = p.category_color || "#8D99AE";
  if (p.image_url && big) return `<div class="avatar" style="background:${esc(color)}22"><img src="${esc(p.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></div>`;
  return `<div class="avatar" style="background:${esc(color)}26">${esc(p.category_icon || "📦")}</div>`;
}
const locOptions = (sel, empty = "– kein Ort –") =>
  `<option value="">${esc(empty)}</option>` + S.meta.locations.map((l) => `<option value="${l.id}" ${l.id === sel ? "selected" : ""}>${esc(l.icon)} ${esc(l.name)}</option>`).join("");
const catOptions = (sel, empty = "– keine –") =>
  `<option value="">${esc(empty)}</option>` + S.meta.categories.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)}</option>`).join("");

// ---------------------------------------------------------------------------
// Bausteine: Mengen-Stepper und Ablaufdatum
// ---------------------------------------------------------------------------
function stepper(name, value = 1, min = 1) {
  return `<div class="stepper" data-min="${min}">
    <button type="button" class="icon-btn minus" data-step="-1" aria-label="weniger">−</button>
    <input type="number" inputmode="numeric" name="${name}" value="${value}" min="${min}">
    <button type="button" class="icon-btn plus" data-step="1" aria-label="mehr">+</button></div>`;
}

function expiryField(value) {
  const shown = value ? fmtDate(value) : "";
  return `<div class="expiry" data-expiry>
    <label class="field"><span>Ablaufdatum / MHD</span></label>
    <div class="row" style="position:relative">
      <input class="exp-text" inputmode="numeric" autocomplete="off" placeholder="z.B. 31.12.27 oder 12/27" value="${esc(shown)}">
      <button type="button" class="icon-btn" data-cal aria-label="Kalender öffnen" style="width:46px;height:46px">📅</button>
      <input type="date" class="exp-date" tabindex="-1" aria-hidden="true" value="${esc(value || "")}"
        style="position:absolute;right:0;bottom:0;width:46px;height:46px;opacity:0;pointer-events:none">
    </div>
    <div class="tiny muted exp-out" style="margin:4px 2px 8px;min-height:16px"></div>
    <div class="chips">
      <button type="button" class="chip" data-addm="1">+1 Monat</button>
      <button type="button" class="chip" data-addm="3">+3 Monate</button>
      <button type="button" class="chip" data-addm="6">+6 Monate</button>
      <button type="button" class="chip" data-addm="12">+1 Jahr</button>
      <button type="button" class="chip" data-addm="24">+2 Jahre</button>
      <button type="button" class="chip" data-addm="0">∞ kein Datum</button>
    </div></div>`;
}
function updateExpiryOut(box) {
  const t = $(".exp-text", box).value;
  const v = parseExpiry(t);
  const out = $(".exp-out", box);
  if (!t.trim()) out.textContent = "Kein Ablaufdatum (z.B. bei Haushaltsartikeln)";
  else if (v === undefined) out.innerHTML = `<span style="color:var(--danger)">Datum nicht erkannt – Beispiele: 31.12.2027 · 12/27</span>`;
  else out.innerHTML = `→ <b>${parseISO(v).toLocaleDateString("de-AT", { weekday: "short", day: "numeric", month: "long", year: "numeric" })}</b>`;
}
function readExpiry(box) {
  const v = parseExpiry($(".exp-text", box).value);
  if (v === undefined) throw new Error("Ablaufdatum nicht erkannt – z.B. 31.12.2027 oder 12/27");
  return v;
}

// Globale Delegation für Stepper & Datum
document.addEventListener("click", (e) => {
  const st = e.target.closest("[data-step]");
  if (st) {
    const wrap = st.closest(".stepper");
    const inp = $("input", wrap);
    const min = Number(wrap.dataset.min || 1);
    const max = inp.max ? Number(inp.max) : Infinity;
    inp.value = Math.min(max, Math.max(min, (parseInt(inp.value, 10) || 0) + Number(st.dataset.step)));
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  const chip = e.target.closest("[data-addm]");
  if (chip) {
    const box = chip.closest("[data-expiry]");
    const m = Number(chip.dataset.addm);
    if (!m) $(".exp-text", box).value = "";
    else { const d = today(); d.setMonth(d.getMonth() + m); $(".exp-text", box).value = fmtDate(iso(d)); }
    updateExpiryOut(box);
    return;
  }
  const cal = e.target.closest("[data-cal]");
  if (cal) {
    const box = cal.closest("[data-expiry]");
    const di = $(".exp-date", box);
    const cur = parseExpiry($(".exp-text", box).value);
    if (cur) di.value = cur;
    try { di.showPicker(); } catch { di.style.pointerEvents = "auto"; di.style.opacity = "1"; di.style.position = "static"; di.style.width = "170px"; di.focus(); }
  }
});
document.addEventListener("input", (e) => {
  const box = e.target.closest("[data-expiry]");
  if (!box) return;
  if (e.target.classList.contains("exp-text")) updateExpiryOut(box);
  if (e.target.classList.contains("exp-date") && e.target.value) {
    $(".exp-text", box).value = fmtDate(e.target.value);
    updateExpiryOut(box);
  }
});
document.addEventListener("change", (e) => {
  if (e.target.classList?.contains("exp-date") && e.target.value) {
    const box = e.target.closest("[data-expiry]");
    $(".exp-text", box).value = fmtDate(e.target.value);
    updateExpiryOut(box);
  }
});
const initExpiry = (root) => $$("[data-expiry]", root).forEach(updateExpiryOut);

// ---------------------------------------------------------------------------
// Produkt-Formular (neu / bearbeiten) und Einlagern
// ---------------------------------------------------------------------------
function catGrid(selected) {
  return `<input type="hidden" name="category_id" value="${selected ?? ""}">
    <div class="cat-grid">${S.meta.categories.map((c) =>
      `<button type="button" class="cat-opt ${c.id === selected ? "on" : ""}" data-cat="${c.id}"><span class="e">${esc(c.icon)}</span>${esc(c.name)}</button>`).join("")}</div>`;
}

function productFields(p = {}) {
  return `
    <label class="field full"><span>Bezeichnung *</span><input name="name" required value="${esc(p.name)}" placeholder="z.B. Weizenmehl glatt"></label>
    <label class="field"><span>Marke</span><input name="brand" value="${esc(p.brand)}" placeholder="optional"></label>
    <label class="field"><span>Packungsgröße</span><input name="package_size" value="${esc(p.package_size)}" placeholder="z.B. 1 kg"></label>
    <div class="full"><label class="field"><span>Kategorie</span></label>${catGrid(p.category_id ?? null)}</div>
    <label class="field"><span>Einheit</span><select name="unit">${UNITS.map((u) => `<option ${u === (p.unit || "Stk") ? "selected" : ""}>${u}</option>`).join("")}</select></label>
    <label class="field"><span>Mindestbestand 🛒</span><input name="min_stock" type="number" min="0" inputmode="numeric" value="${p.min_stock ?? 0}"></label>
    <label class="field"><span>Standard-Lagerort</span><select name="default_location_id">${locOptions(p.default_location_id ?? null)}</select></label>
    <label class="field"><span>Barcode</span><input name="barcode" inputmode="numeric" value="${esc(p.barcode)}" placeholder="optional"></label>
    <label class="field full"><span>Notiz</span><input name="notes" value="${esc(p.notes)}" placeholder="optional"></label>
    <input type="hidden" name="image_url" value="${esc(p.image_url)}">`;
}

function stockFields(p, qty = 1) {
  const def = p.default_location_id ?? S.lastLocation ?? S.meta.locations[0]?.id ?? null;
  return `
    <div class="full"><label class="field"><span>Menge</span></label>${stepper("quantity", qty)}</div>
    <label class="field full"><span>Lagerort</span><select name="location_id">${locOptions(def)}</select></label>
    <div class="full">${expiryField(null)}</div>`;
}

function readForm(form) {
  const d = {};
  $$("input[name], select[name], textarea[name]", form).forEach((el) => { d[el.name] = el.value; });
  return d;
}

// Kategorie-Auswahl: genau EIN globaler Handler (nicht pro Dialog anhängen,
// sonst heben sich mehrere Handler gegenseitig auf).
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-cat]");
  if (!b) return;
  const grid = b.closest(".cat-grid");
  const hidden = grid.previousElementSibling;
  const on = b.classList.contains("on");
  $$(".cat-opt", grid).forEach((x) => x.classList.remove("on"));
  if (!on) b.classList.add("on");
  hidden.value = on ? "" : b.dataset.cat;
});

function newProductSheet(prefill = {}, onDone) {
  const sug = prefill.source ? `<div class="hint info" style="margin-bottom:12px">✨ Gefunden bei ${esc(prefill.source)} – bitte kurz prüfen.</div>` : (prefill.barcode ? `<div class="hint" style="margin-bottom:12px">🤔 Barcode ${esc(prefill.barcode)} ist noch unbekannt. Einmal anlegen – beim nächsten Scan wird es automatisch erkannt.</div>` : "");
  const body = openSheet(`
    <div class="prod-head">${prefill.image_url ? avatar({ ...prefill, category_color: "#FFC145" }, true) : `<div class="avatar" style="background:var(--sun-l)">✨</div>`}
      <div><h2>Neues Produkt</h2><div class="muted small">einmal anlegen, dann nur noch scannen</div></div></div>
    ${sug}
    <form class="form-grid" id="pform" autocomplete="off">
      ${productFields(prefill)}
      <div class="full section-title" style="margin:10px 0 0"><h3>📥 Jetzt einlagern</h3></div>
      ${stockFields(prefill)}
      <button class="btn primary big full" type="submit">Speichern & einlagern</button>
    </form>`, () => onDone && onDone(false));
  initExpiry(body);
  $("#pform").onsubmit = (e) => {
    e.preventDefault();
    run(async () => {
      const d = readForm(e.target);
      const stock = { quantity: Number(d.quantity), location_id: d.location_id, expiry_date: readExpiry(body) };
      const product = { ...d };
      delete product.quantity; delete product.location_id;
      if (!product.default_location_id && stock.location_id) product.default_location_id = stock.location_id;
      const res = await api("products", { method: "POST", body: { ...product, stock } });
      rememberLocation(stock.location_id);
      toast(`🎉 ${stock.quantity}× ${res.product.name} eingelagert`);
      sheetOnClose = null; closeSheet();
      await loadItems();
      onDone && onDone(true);
    });
  };
  setTimeout(() => { const n = $("[name=name]", body); if (n && !n.value) n.focus(); }, 250);
}

function rememberLocation(id) { if (id) { S.lastLocation = Number(id); store.set("lastLocation", S.lastLocation); } }

function addStockSheet(p, onDone) {
  const body = openSheet(`
    <div class="prod-head">${avatar(p, true)}<div><h2>${esc(p.name)}</h2>
      <div class="muted small">${esc(p.brand)} ${p.package_size ? "· " + esc(p.package_size) : ""}</div>
      <div class="item-meta"><span class="badge">Im Vorrat: ${p.total} ${esc(p.unit)}</span></div></div></div>
    <form class="form-grid" id="sform" autocomplete="off">
      ${stockFields(p)}
      <button class="btn mint big full" type="submit">📥 Einlagern</button>
      <button class="btn ghost full" type="button" data-edit>✏️ Produktdaten bearbeiten</button>
    </form>`, () => onDone && onDone(false));
  initExpiry(body);
  $("[data-edit]", body).onclick = () => editProductSheet(p, () => onDone && onDone(true));
  $("#sform").onsubmit = (e) => {
    e.preventDefault();
    run(async () => {
      const d = readForm(e.target);
      const payload = { product_id: p.id, quantity: Number(d.quantity), location_id: d.location_id, expiry_date: readExpiry(body) };
      await api("stock", { method: "POST", body: payload });
      rememberLocation(d.location_id);
      toast(`📥 ${payload.quantity}× ${p.name} eingelagert`);
      sheetOnClose = null; closeSheet();
      await loadItems();
      onDone && onDone(true);
    });
  };
  setTimeout(() => $("[name=quantity]", body)?.select(), 250);
}

function editProductSheet(p, onDone) {
  const body = openSheet(`
    <div class="prod-head">${avatar(p, true)}<div><h2>Produkt bearbeiten</h2><div class="muted small">${esc(p.name)}</div></div></div>
    <form class="form-grid" id="eform" autocomplete="off">
      ${productFields(p)}
      <button class="btn primary big full" type="submit">💾 Speichern</button>
      <button class="btn danger full" type="button" data-del>🗑️ Produkt ganz löschen</button>
    </form>`, () => onDone && onDone(false));
  $("#eform").onsubmit = (e) => {
    e.preventDefault();
    run(async () => {
      await api(`products/${p.id}`, { method: "PUT", body: readForm(e.target) });
      toast("💾 Gespeichert");
      await loadItems();
      openProduct(p.id, onDone);
    });
  };
  $("[data-del]", body).onclick = () => run(async () => {
    if (!(await ask(`„${p.name}“ mit allem Bestand löschen?`, { ok: "Löschen", danger: true }))) return;
    await api(`products/${p.id}`, { method: "DELETE" });
    toast("🗑️ Produkt gelöscht");
    sheetOnClose = null; closeSheet();
    await loadItems();
    onDone ? onDone(true) : S.refresh && S.refresh();
  });
}

// ---------------------------------------------------------------------------
// Entnehmen
// ---------------------------------------------------------------------------
function undoToast(msg, taken) {
  toast(msg, {
    action: "Rückgängig",
    onAction: () => run(async () => {
      await api("stock/undo", { method: "POST", body: { items: taken } });
      toast("↩️ Rückgängig gemacht");
      await loadItems();
      S.refresh && S.refresh();
    }),
  });
}

async function consumeQuick(p, locationId) {
  const res = await api(`products/${p.id}/consume`, { method: "POST", body: { amount: 1, location_id: locationId || null } });
  await loadItems();
  const left = res.product.total;
  undoToast(`✅ 1× ${p.name} entnommen · noch ${left}${left === 0 ? " – leer!" : ""}`, res.taken);
}

function consumeSheet(p, onDone) {
  if (!p.total) {
    const body = openSheet(`
      <div class="prod-head">${avatar(p, true)}<div><h2>${esc(p.name)}</h2><div class="muted small">${esc(p.brand)}</div></div></div>
      <div class="empty-state"><div class="big">🫙</div><h3>Nichts mehr im Vorrat</h3><p>Dieses Produkt ist bereits aufgebraucht.</p></div>
      <button class="btn mint block big" data-in>📥 Stattdessen einlagern</button>`, () => onDone && onDone(false));
    $("[data-in]", body).onclick = () => addStockSheet(p, onDone);
    return;
  }
  const body = openSheet(`
    <div class="prod-head">${avatar(p, true)}<div><h2>${esc(p.name)}</h2>
      <div class="muted small">${esc(p.brand)}</div>
      <div class="item-meta"><span class="badge">Im Vorrat: ${p.total} ${esc(p.unit)}</span></div></div></div>
    <label class="field"><span>Woraus nehmen?</span></label>
    <div class="chips" id="srcsel" style="margin-bottom:12px">
      <button type="button" class="chip on" data-src="">🤖 Automatisch (Ältestes zuerst)</button>
      ${p.batches.map((b) => `<button type="button" class="chip" data-src="${b.id}" data-max="${b.quantity}">${b.quantity}× ${esc(b.location_icon || "")} ${esc(b.location_name || "–")} · ${b.expiry_date ? fmtDate(b.expiry_date) : "∞"}</button>`).join("")}
    </div>
    <label class="field"><span>Menge</span></label>
    ${stepper("amount", 1).replace('min="1"', `min="1" max="${p.total}"`)}
    <div class="row" style="margin-top:18px">
      <button class="btn primary big block" data-kind="verbrauch">✅ Verbraucht</button>
      <button class="btn danger big" data-kind="entsorgt" title="Weggeworfen">🗑️ Entsorgt</button>
    </div>`, () => onDone && onDone(false));
  let src = "";
  body.onclick = (e) => {
    const s = e.target.closest("[data-src]");
    if (s) {
      $$("[data-src]", body).forEach((x) => x.classList.remove("on"));
      s.classList.add("on");
      src = s.dataset.src;
      $("[name=amount]", body).max = s.dataset.max || p.total;
      return;
    }
    const k = e.target.closest("[data-kind]");
    if (!k) return;
    run(async () => {
      const amount = Number($("[name=amount]", body).value) || 1;
      const kind = k.dataset.kind;
      const res = src
        ? await api(`stock/${src}/take`, { method: "POST", body: { amount, kind } })
        : await api(`products/${p.id}/consume`, { method: "POST", body: { amount, kind } });
      const n = res.taken.reduce((a, t) => a + t.quantity, 0);
      sheetOnClose = null; closeSheet();
      await loadItems();
      undoToast(`${kind === "entsorgt" ? "🗑️" : "✅"} ${n}× ${p.name} ${kind === "entsorgt" ? "entsorgt" : "verbraucht"} · noch ${res.product.total}`, res.taken);
      onDone && onDone(true);
    });
  };
}

// ---------------------------------------------------------------------------
// Produkt-Detail mit Chargen
// ---------------------------------------------------------------------------
async function openProduct(pid, onDone) {
  let p = findItem(pid);
  if (!p) { await loadItems(); p = findItem(pid); }
  if (!p) return toast("Produkt nicht gefunden", { error: true });
  const c = cat(p.category_id);
  const done = () => { S.refresh && S.refresh(); onDone && onDone(true); };
  const low = p.min_stock > 0 && p.total < p.min_stock;
  const body = openSheet(`
    <div class="prod-head">${avatar(p, true)}<div style="min-width:0">
      <h2>${esc(p.name)}</h2>
      <div class="muted small">${esc(p.brand)} ${p.package_size ? "· " + esc(p.package_size) : ""}</div>
      <div class="item-meta">${c ? `<span class="badge">${esc(c.icon)} ${esc(c.name)}</span>` : ""}
        ${p.barcode ? `<span class="badge">▮▯▮ ${esc(p.barcode)}</span>` : ""}
        ${low ? `<span class="badge low">🛒 nachkaufen</span>` : ""}</div></div></div>
    <div class="card" style="padding:14px">
      <div class="row"><div class="grow"><div class="muted tiny" style="font-weight:800">IM VORRAT</div>
        <div style="font-size:28px;font-weight:900">${p.total} <span class="small muted">${esc(p.unit)}</span></div></div>
        <div class="spacer"></div>
        ${p.min_stock ? `<div class="muted small" style="text-align:right">Mindestbestand<br><b>${p.min_stock}</b></div>` : ""}</div>
      ${p.notes ? `<div class="small muted" style="margin-top:6px">📝 ${esc(p.notes)}</div>` : ""}
    </div>
    <div class="section-title"><h3>Chargen</h3><span class="muted small">nach Ablaufdatum</span></div>
    ${p.batches.length ? p.batches.map((b) => `
      <div class="batch" data-sid="${b.id}">
        <button class="icon-btn minus" data-take="${b.id}" aria-label="1 entnehmen">−</button>
        <div class="qty-num">${b.quantity}</div>
        <button class="icon-btn plus" data-inc="${b.id}" aria-label="1 dazu">+</button>
        <div class="batch-main">
          <div class="small" style="font-weight:800">${esc(b.location_icon || "📍")} ${esc(b.location_name || "kein Ort")}</div>
          <div class="item-meta">${expiryBadge(b.expiry_date, true)}${b.note ? `<span>📝 ${esc(b.note)}</span>` : ""}</div>
        </div>
        <button class="icon-btn" data-editb="${b.id}" aria-label="bearbeiten">✏️</button>
      </div>`).join("") : `<div class="empty-state" style="padding:16px"><div class="big">🫙</div>Gerade nichts davon da.</div>`}
    <div class="duo" style="margin-top:16px">
      <button class="btn mint big" data-add>📥 Einlagern</button>
      <button class="btn big" data-edit>✏️ Produkt bearbeiten</button>
    </div>`, onDone ? () => onDone(false) : null);
  body.onclick = (e) => {
    const t = e.target.closest("[data-take],[data-inc],[data-editb],[data-add],[data-edit]");
    if (!t) return;
    if (t.dataset.add !== undefined) return addStockSheet(p, () => { openProduct(pid, onDone); done(); });
    if (t.dataset.edit !== undefined) return editProductSheet(p, onDone);
    const b = p.batches.find((x) => x.id === Number(t.dataset.take || t.dataset.inc || t.dataset.editb));
    if (t.dataset.editb) return editBatchSheet(p, b, onDone);
    run(async () => {
      if (t.dataset.take) {
        const res = await api(`stock/${b.id}/take`, { method: "POST", body: { amount: 1 } });
        undoToast(`✅ 1× ${p.name} entnommen`, res.taken);
      } else {
        await api(`stock/${b.id}`, { method: "PUT", body: { quantity: b.quantity + 1 } });
      }
      await loadItems();
      openProduct(pid, onDone);
      S.refresh && S.refresh();
    });
  };
}

function editBatchSheet(p, b, onDone) {
  const body = openSheet(`
    <h2>Charge bearbeiten</h2><div class="muted small" style="margin-bottom:14px">${esc(p.name)}</div>
    <form class="form-grid" id="bform">
      <div class="full"><label class="field"><span>Menge</span></label>${stepper("quantity", b.quantity, 0)}</div>
      <label class="field full"><span>Lagerort (umlagern)</span><select name="location_id">${locOptions(b.location_id)}</select></label>
      <div class="full">${expiryField(b.expiry_date)}</div>
      <label class="field full"><span>Notiz</span><input name="note" value="${esc(b.note)}"></label>
      <button class="btn primary big full" type="submit">💾 Speichern</button>
      <button class="btn" type="button" data-all="verbrauch">✅ Alles verbraucht</button>
      <button class="btn danger" type="button" data-all="entsorgt">🗑️ Alles entsorgt</button>
    </form>`, onDone ? () => onDone(false) : null);
  initExpiry(body);
  const back = async () => { await loadItems(); openProduct(p.id, onDone); S.refresh && S.refresh(); };
  $("#bform").onsubmit = (e) => {
    e.preventDefault();
    run(async () => {
      const d = readForm(e.target);
      await api(`stock/${b.id}`, { method: "PUT", body: { quantity: Number(d.quantity), location_id: d.location_id, expiry_date: readExpiry(body), note: d.note } });
      toast("💾 Gespeichert");
      await back();
    });
  };
  $$("[data-all]", body).forEach((btn) => (btn.onclick = () => run(async () => {
    const res = await api(`stock/${b.id}/take`, { method: "POST", body: { amount: b.quantity, kind: btn.dataset.all } });
    undoToast(`${b.quantity}× ${p.name} ${btn.dataset.all === "entsorgt" ? "entsorgt" : "verbraucht"}`, res.taken);
    await back();
  })));
}

// ---------------------------------------------------------------------------
// Seite: Übersicht
// ---------------------------------------------------------------------------
const BALLS_SVG = `<svg class="balls" width="170" height="130" viewBox="0 0 170 130" aria-hidden="true"><g fill="#fff"><circle cx="40" cy="40" r="26"/><circle cx="100" cy="30" r="20"/><circle cx="140" cy="70" r="26"/><circle cx="80" cy="90" r="22"/></g></svg>`;

async function renderHome() {
  view.innerHTML = `<div class="skeleton"></div>`;
  const d = await api("dashboard");
  const s = d.stats;
  const h = new Date().getHours();
  const greet = h < 11 ? "Guten Morgen! ☀️" : h < 17 ? "Hallo! 👋" : "Guten Abend! 🌙";
  const mood = s.expired ? `${s.expired} Posten ${s.expired === 1 ? "ist" : "sind"} abgelaufen – kurz nachsehen?`
    : s.soon ? `${s.soon} Posten ${s.soon === 1 ? "sollte" : "sollten"} bald verbraucht werden.`
    : s.units ? "Alles frisch – euer Vorrat ist bestens in Schuss. 💚" : "Los geht’s: Scannt euer erstes Produkt ein!";
  view.innerHTML = `
    <section class="hello">${BALLS_SVG}<h1>${greet}</h1><p>${esc(mood)}</p></section>
    <div class="stats">
      <a class="stat s-mint" href="#/vorrat"><div class="num">${s.units}</div><div class="lbl">Stück im Vorrat · ${s.products} Produkte</div></a>
      <a class="stat s-sun" href="#/vorrat?status=soon"><div class="num">${s.soon}</div><div class="lbl">bald ablaufend (${s.soon_days} T.)</div></a>
      <a class="stat s-coral" href="#/vorrat?status=expired"><div class="num">${s.expired}</div><div class="lbl">abgelaufen</div></a>
      <a class="stat s-sky" href="#/einkauf"><div class="num">${s.low}</div><div class="lbl">auf der Einkaufsliste</div></a>
    </div>
    <div class="duo" style="margin-top:16px">
      <a class="btn mint big" href="#/scan?mode=in">📥 Einlagern</a>
      <a class="btn primary big" href="#/scan?mode=out">📤 Entnehmen</a>
    </div>
    <div class="section-title"><h2>⏰ Bald verbrauchen</h2><span class="spacer"></span><a class="small muted" href="#/vorrat?sort=expiry">alle</a></div>
    ${d.expiring.length ? `<div class="list">${d.expiring.map((e) => `
      <div class="item" data-pid="${e.product_id}">${avatar(e)}
        <div class="item-main"><div class="item-title">${esc(e.name)} <span class="brand-s">${esc(e.brand)}</span></div>
          <div class="item-meta">${expiryBadge(e.expiry_date, true)}<span>${esc(e.location_icon || "")} ${esc(e.location_name || "")}</span></div></div>
        <div class="qty"><div class="qty-num">${e.quantity}<small>${esc(e.unit)}</small></div></div></div>`).join("")}</div>`
      : `<div class="card empty-state"><div class="big">🌿</div><h3>Nichts läuft bald ab</h3><p>In den nächsten ${s.soon_days} Tagen ist alles im grünen Bereich.</p></div>`}
    ${d.shopping.length ? `<div class="section-title"><h2>🛒 Nachkaufen</h2><span class="spacer"></span><a class="small muted" href="#/einkauf">Liste</a></div>
      <div class="chips">${d.shopping.map((x) => `<span class="chip" data-pid="${x.id}">${esc(x.category_icon || "📦")} ${esc(x.name)} <span class="muted">${x.total}/${x.min_stock}</span></span>`).join("")}</div>` : ""}
    ${d.recent.length ? `<div class="section-title"><h2>🕒 Zuletzt</h2><span class="spacer"></span><a class="small muted" href="#/verlauf">Verlauf</a></div>
      <div class="card" style="padding:6px 16px">${d.recent.map(historyRow).join("")}</div>` : ""}
    ${footer()}`;
  view.onclick = (e) => {
    const it = e.target.closest("[data-pid]");
    if (it) run(() => openProduct(Number(it.dataset.pid), () => renderHome()));
  };
  S.refresh = () => renderHome();
}

function footer() {
  return `<div class="footer-logo"><img src="static/icon.svg" alt=""><div style="font-weight:900;font-size:18px"><span class="c-coral">BALLa</span>ger</div><div class="tiny">Unser Vorrat. Gut sortiert.</div></div>`;
}

function historyRow(m) {
  const [ico, lbl] = KINDS[m.kind] || ["•", m.kind];
  const delta = m.delta > 0 ? `<span class="delta pos">+${m.delta}</span>` : m.delta < 0 ? `<span class="delta neg">${m.delta}</span>` : `<span class="delta">↔</span>`;
  return `<div class="history-item"><span style="font-size:20px">${ico}</span>
    <div class="grow" style="flex:1;min-width:0"><div style="font-weight:800" class="item-title">${esc(m.product_name)}</div>
    <div class="tiny muted">${lbl} · ${fmtStamp(m.created_at)}${m.location_name ? " · " + esc(m.location_name) : ""}</div></div>${delta}</div>`;
}

// ---------------------------------------------------------------------------
// Seite: Vorrat (Liste mit Filtern)
// ---------------------------------------------------------------------------
const F = Object.assign({ q: "", cat: "", loc: "", status: "all", sort: "expiry", empty: false }, store.get("filters", {}));
F.q = "";
const STATUS = [["all", "Alle"], ["expired", "⛔ Abgelaufen"], ["soon", "⏰ Bald"], ["ok", "✓ Frisch"], ["none", "∞ Ohne Datum"], ["low", "🛒 Nachkaufen"]];
const SORTS = [["expiry", "⏰ nach Ablauf"], ["name", "🔤 nach Name"], ["category", "🗂️ nach Kategorie"], ["location", "📍 nach Lagerort"], ["qty", "🔢 nach Menge"], ["added", "🆕 zuletzt eingelagert"]];

function filtered() {
  const tokens = F.q.toLowerCase().split(/\s+/).filter(Boolean);
  const out = [];
  for (const p of S.items) {
    const batches = F.loc ? p.batches.filter((b) => String(b.location_id) === F.loc) : p.batches;
    const total = batches.reduce((a, b) => a + b.quantity, 0);
    const isLow = p.min_stock > 0 && p.total < p.min_stock;
    if (F.status === "low") { if (!isLow) continue; }
    else if (!F.empty && total === 0) continue;
    if (F.loc && total === 0 && F.status !== "low") continue;
    if (F.cat && String(p.category_id ?? "none") !== F.cat) continue;
    if (tokens.length) {
      const hay = [p.name, p.brand, p.barcode, p.notes, p.category_name, ...batches.map((b) => b.location_name), ...batches.map((b) => b.note)].join(" ").toLowerCase();
      if (!tokens.every((t) => hay.includes(t))) continue;
    }
    if (["expired", "soon", "ok", "none"].includes(F.status) && !batches.some((b) => expStatus(b.expiry_date) === F.status)) continue;
    const dates = batches.map((b) => b.expiry_date).filter(Boolean).sort();
    out.push({ p, batches, total, next: dates[0] || null, isLow, added: batches.map((b) => b.added_at).sort().pop() || p.created_at });
  }
  const byName = (a, b) => a.p.name.localeCompare(b.p.name, "de");
  const sorters = {
    expiry: (a, b) => (a.next || "9999") < (b.next || "9999") ? -1 : (a.next || "9999") > (b.next || "9999") ? 1 : byName(a, b),
    name: byName,
    category: (a, b) => ((cat(a.p.category_id)?.sort_order ?? 999) - (cat(b.p.category_id)?.sort_order ?? 999)) || byName(a, b),
    location: (a, b) => ((loc(a.batches[0]?.location_id)?.sort_order ?? 999) - (loc(b.batches[0]?.location_id)?.sort_order ?? 999)) || byName(a, b),
    qty: (a, b) => b.total - a.total || byName(a, b),
    added: (a, b) => (a.added < b.added ? 1 : a.added > b.added ? -1 : 0),
  };
  return out.sort(sorters[F.sort] || sorters.expiry);
}

function itemCard(x) {
  const { p, batches, total, next, isLow } = x;
  const locs = [...new Map(batches.map((b) => [b.location_id, b])).values()]
    .map((b) => `${esc(b.location_icon || "")} ${esc(b.location_name || "–")}`).join(" · ");
  return `<div class="item ${total ? "" : "empty"}" data-pid="${p.id}">${avatar(p)}
    <div class="item-main">
      <div class="item-title">${esc(p.name)} <span class="brand-s">${esc(p.brand)}</span></div>
      <div class="item-meta">${total ? expiryBadge(next) : `<span class="badge">leer</span>`}${isLow ? `<span class="badge low">🛒</span>` : ""}
        ${locs ? `<span>${locs}</span>` : ""}${batches.length > 1 ? `<span>· ${batches.length} Chargen</span>` : ""}</div>
    </div>
    <div class="qty">${total ? `<button class="icon-btn minus" data-minus="${p.id}" aria-label="Eins entnehmen">−</button>` : ""}
      <div class="qty-num">${total}<small>${esc(p.unit)}</small></div></div></div>`;
}

function renderList() {
  const list = filtered();
  const box = $("#vlist");
  if (!box) return;
  const units = list.reduce((a, x) => a + x.total, 0);
  $("#vcount").textContent = `${list.length} Produkt${list.length === 1 ? "" : "e"} · ${units} Stück`;
  if (!list.length) {
    box.innerHTML = S.items.length
      ? `<div class="card empty-state"><div class="big">🔍</div><h3>Nichts gefunden</h3><p>Probiert andere Filter oder Suchbegriffe.</p><button class="btn" data-reset>Filter zurücksetzen</button></div>`
      : `<div class="card empty-state"><div class="big">🫙</div><h3>Noch leer hier</h3><p>Scannt euer erstes Produkt – es dauert nur Sekunden.</p><a class="btn primary" href="#/scan?mode=in">📥 Erstes Produkt einlagern</a></div>`;
    return;
  }
  let html = "";
  let group = null;
  for (const x of list) {
    if (F.sort === "category" || F.sort === "location") {
      const g = F.sort === "category" ? (cat(x.p.category_id) ? `${cat(x.p.category_id).icon} ${cat(x.p.category_id).name}` : "📦 Ohne Kategorie")
        : (x.batches[0]?.location_name ? `${x.batches[0].location_icon} ${x.batches[0].location_name}` : "📍 Ohne Ort");
      if (g !== group) { html += `${group === null ? "" : "</div>"}<div class="group-head">${esc(g)}</div><div class="list">`; group = g; }
    }
    html += itemCard(x);
  }
  box.innerHTML = group === null ? `<div class="list">${html}</div>` : html + "</div>";
}

async function renderVorrat(params) {
  if (params.get("status")) F.status = params.get("status");
  if (params.get("sort")) F.sort = params.get("sort");
  if (params.get("cat")) F.cat = params.get("cat");
  view.innerHTML = `
    <div class="page-head"><div><h1>Vorrat</h1><p class="muted small" id="vcount">…</p></div><span class="spacer"></span>
      <button class="btn small ghost" data-new>✍️ Neu ohne Barcode</button></div>
    <div class="filters">
      <div class="search"><input type="search" id="q" placeholder="Suchen: Name, Marke, Barcode, Ort …" value="${esc(F.q)}" autocomplete="off"></div>
      <div class="filter-row">
        <select id="fcat" aria-label="Kategorie">${catOptions(null, "🗂️ Alle Kategorien").replace(/<option value="">/, '<option value="">')}<option value="none">📦 Ohne Kategorie</option></select>
        <select id="floc" aria-label="Lagerort">${locOptions(null, "📍 Alle Orte")}</select>
        <select id="fsort" class="sort" aria-label="Sortierung">${SORTS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select>
      </div>
      <div class="chips scroll" id="fstatus" style="margin-top:8px">
        ${STATUS.map(([k, l]) => `<button class="chip ${F.status === k ? "on" : ""}" data-status="${k}">${l}</button>`).join("")}
        <label class="chip"><input type="checkbox" id="fempty" ${F.empty ? "checked" : ""} style="width:16px;height:16px"> leere zeigen</label>
      </div>
    </div>
    <div id="vlist"><div class="skeleton"></div><div class="skeleton" style="margin-top:10px"></div></div>`;
  $("#fcat").value = F.cat; $("#floc").value = F.loc; $("#fsort").value = F.sort;
  const save = () => { const { q, ...rest } = F; store.set("filters", rest); renderList(); };
  $("#q").oninput = (e) => { F.q = e.target.value; renderList(); };
  $("#fcat").onchange = (e) => { F.cat = e.target.value; save(); };
  $("#floc").onchange = (e) => { F.loc = e.target.value; save(); };
  $("#fsort").onchange = (e) => { F.sort = e.target.value; save(); };
  $("#fempty").onchange = (e) => { F.empty = e.target.checked; save(); };
  view.onclick = (e) => {
    const st = e.target.closest("[data-status]");
    if (st) { F.status = st.dataset.status; $$("[data-status]").forEach((b) => b.classList.toggle("on", b === st)); return save(); }
    if (e.target.closest("[data-reset]")) { Object.assign(F, { q: "", cat: "", loc: "", status: "all" }); return renderVorrat(new URLSearchParams()); }
    if (e.target.closest("[data-new]")) return newProductSheet({}, (ok) => ok && renderList());
    const m = e.target.closest("[data-minus]");
    if (m) { e.stopPropagation(); const p = findItem(Number(m.dataset.minus)); return run(async () => { await consumeQuick(p, F.loc); renderList(); }); }
    const it = e.target.closest("[data-pid]");
    if (it) run(() => openProduct(Number(it.dataset.pid)));
  };
  S.refresh = () => renderList();
  await loadItems();
  renderList();
}

// ---------------------------------------------------------------------------
// Seite: Scannen
// ---------------------------------------------------------------------------
let scanner = null;
let scanBusy = false;
let lastCode = { code: "", t: 0 };
const canLive = () => window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;

function formats() {
  const Fm = window.Html5QrcodeSupportedFormats;
  return Fm ? [Fm.EAN_13, Fm.EAN_8, Fm.UPC_A, Fm.UPC_E, Fm.CODE_128, Fm.CODE_39, Fm.ITF, Fm.QR_CODE, Fm.DATA_MATRIX] : undefined;
}
function newReader(id) {
  return new window.Html5Qrcode(id, { formatsToSupport: formats(), verbose: false, experimentalFeatures: { useBarCodeDetectorIfSupported: true } });
}

async function startScanner() {
  if (!window.Html5Qrcode) return toast("Scanner-Bibliothek nicht geladen", { error: true });
  if (scanner) return;
  $("#scan-idle").classList.add("hidden");
  $("#scan-frame").classList.remove("hidden");
  scanner = newReader("reader");
  // Hohe Auflösung + Dauer-Autofokus: Barcodes werden auch aus 15–25 cm lesbar.
  const hd = {
    fps: 12,
    videoConstraints: {
      facingMode: "environment",
      width: { ideal: 1920 },
      height: { ideal: 1080 },
      advanced: [{ focusMode: "continuous" }],
    },
  };
  try {
    try {
      await scanner.start({ facingMode: "environment" }, hd, onDecoded, () => {});
    } catch (e) {
      console.warn("HD-Kamera nicht möglich, nutze Standard", e);
      try { scanner.clear(); } catch { /* egal */ }
      scanner = newReader("reader");
      await scanner.start({ facingMode: "environment" }, { fps: 12 }, onDecoded, () => {});
    }
    $("#cam-btn").textContent = "⏹ Kamera stoppen";
    store.set("camOn", true);
    setupCameraTools();
  } catch (e) {
    console.warn(e);
    scanner = null;
    $("#scan-idle").classList.remove("hidden");
    $("#scan-frame").classList.add("hidden");
    toast("Kamera nicht verfügbar – nutzt „Foto vom Barcode“ 📸", { error: true });
  }
}
/** Zoom-Regler und Taschenlampe einblenden, wenn das Gerät es unterstützt (v.a. Android). */
function setupCameraTools() {
  const tools = $("#scan-tools");
  if (!tools || !scanner) return;
  let caps;
  try { caps = scanner.getRunningTrackCameraCapabilities(); } catch { return; }
  try { scanner.applyVideoConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(() => {}); } catch { /* egal */ }
  let any = false;
  try {
    const zoom = caps.zoomFeature();
    if (zoom.isSupported()) {
      const z = $("#zoom-in");
      const max = Math.min(zoom.max(), 5);
      z.min = zoom.min(); z.max = max; z.step = zoom.step() || 0.1;
      z.value = zoom.value() || zoom.min();
      z.oninput = () => { zoom.apply(Number(z.value)).catch(() => {}); };
      $("#zoom-wrap").classList.remove("hidden");
      any = true;
    }
  } catch { /* kein Zoom */ }
  try {
    const torch = caps.torchFeature();
    if (torch.isSupported()) {
      const b = $("#torch-btn");
      let on = false;
      b.onclick = () => { on = !on; torch.apply(on).then(() => b.classList.toggle("on", on)).catch(() => { on = !on; }); };
      b.classList.remove("hidden");
      any = true;
    }
  } catch { /* keine Lampe */ }
  tools.classList.toggle("hidden", !any);
}

async function stopScanner() {
  const s = scanner;
  scanner = null;
  scanBusy = false;
  const tools = $("#scan-tools");
  if (tools) tools.classList.add("hidden");
  if (!s) return;
  try { await s.stop(); } catch { /* schon gestoppt */ }
  try { s.clear(); } catch { /* egal */ }
}
function resumeScanner() {
  scanBusy = false;
  if (scanner) { try { scanner.resume(); } catch { /* läuft schon */ } }
}
function onDecoded(text) {
  if (scanBusy) return;
  const now = Date.now();
  if (text === lastCode.code && now - lastCode.t < 2500) return;
  lastCode = { code: text, t: now };
  scanBusy = true;
  try { navigator.vibrate && navigator.vibrate(60); } catch { /* egal */ }
  try { scanner && scanner.pause(true); } catch { /* egal */ }
  handleCode(text);
}

async function downscale(file, max = 1600) {
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    if (k === 1) return file;
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.9));
    return new File([blob], "scan.jpg", { type: "image/jpeg" });
  } catch { return file; }
}
async function scanPhoto(file) {
  if (!file) return;
  if (!window.Html5Qrcode) return toast("Scanner-Bibliothek nicht geladen", { error: true });
  toast("🔎 Suche Barcode im Foto …", { ms: 1500 });
  const reader = newReader("scan-file-reader");
  let code = null;
  for (const f of [await downscale(file), file]) {
    try { code = await reader.scanFile(f, false); break; } catch { /* nächster Versuch */ }
  }
  try { reader.clear(); } catch { /* egal */ }
  if (code) { scanBusy = true; handleCode(code); }
  else toast("Kein Barcode erkannt – bitte näher und scharf fotografieren 📸", { error: true });
}

async function handleCode(code) {
  code = String(code).trim();
  const done = () => { resumeScanner(); };
  openSheet(`<div class="empty-state"><div class="big">🔎</div><h3>Einen Moment …</h3><p>Suche Barcode ${esc(code)}</p></div>`, done);
  const res = await run(() => api("barcode/" + encodeURIComponent(code)));
  if (!res) return closeSheet();
  if (S.scanMode === "out") {
    if (res.found) return consumeSheet(res.product, done);
    const body = openSheet(`<div class="empty-state"><div class="big">🤷</div><h3>Unbekanntes Produkt</h3><p>Barcode ${esc(code)} ist noch nicht im Vorrat.</p></div>
      <button class="btn mint block big" data-in>📥 Stattdessen einlagern</button>`, done);
    $("[data-in]", body).onclick = () => newProductSheet({ barcode: code, ...(res.suggestion || {}) }, done);
    return;
  }
  if (res.found) return addStockSheet(res.product, done);
  newProductSheet({ barcode: code, ...(res.suggestion || {}) }, done);
}

function renderScan(params) {
  if (params.get("mode")) { S.scanMode = params.get("mode") === "out" ? "out" : "in"; store.set("scanMode", S.scanMode); }
  const live = canLive();
  view.innerHTML = `
    <div class="page-head"><div><h1>Scannen</h1><p class="muted small">Barcode vor die Kamera – fertig. Ihr könnt gleich weiterscannen.</p></div></div>
    <div class="mode-toggle" role="tablist">
      <button class="in ${S.scanMode === "in" ? "on" : ""}" data-mode="in">📥 Einlagern</button>
      <button class="out ${S.scanMode === "out" ? "on" : ""}" data-mode="out">📤 Entnehmen</button>
    </div>
    <div class="scanner">
      <div id="reader"></div>
      <div id="scan-frame" class="scan-frame hidden"><span>Barcode in den Rahmen · ca. 15–25 cm Abstand</span></div>
      <div id="scan-tools" class="scan-tools hidden">
        <button type="button" id="torch-btn" class="icon-btn hidden" aria-label="Taschenlampe">🔦</button>
        <label id="zoom-wrap" class="zoom hidden">🔍<input type="range" id="zoom-in" aria-label="Zoom"></label>
      </div>
      <div class="scanner-idle" id="scan-idle">
        <div class="big">${live ? "📷" : "📸"}</div>
        ${live ? `<button class="btn primary big" data-cam>Kamera starten</button>`
          : `<p style="max-width:320px;margin:6px auto 14px;font-weight:700">Macht einfach ein Foto vom Barcode – BALLager erkennt ihn automatisch.</p>
             <label class="btn primary big" for="photo-in">📸 Foto aufnehmen</label>`}
      </div>
    </div>
    <div class="scan-actions">
      ${live ? `<button class="btn" id="cam-btn" data-cam>📷 Kamera</button>` : ""}
      <label class="btn" for="photo-in">📸 Foto vom Barcode</label>
      <button class="btn" data-manual>⌨️ Code eintippen</button>
      ${S.scanMode === "in" ? `<button class="btn" data-new>✍️ Ohne Barcode</button>` : `<a class="btn" href="#/vorrat">🔎 Aus Liste wählen</a>`}
    </div>
    <input type="file" id="photo-in" accept="image/*" capture="environment" hidden>
    <form id="manual" class="row hidden" style="margin-top:12px">
      <input id="manual-code" inputmode="numeric" placeholder="Barcode-Nummer, z.B. 9001234567890" autocomplete="off">
      <button class="btn primary" type="submit">OK</button>
    </form>
    ${live ? "" : `<div class="hint info" style="margin-top:14px">💡 Für den <b>Live-Scanner</b> braucht der Browser eine sichere Verbindung (HTTPS, z.B. über Home Assistant Cloud oder ein Zertifikat). Die Foto-Funktion klappt immer.</div>`}
    <div class="hint" style="margin-top:12px">🧺 <b>Tipp nach dem Einkauf:</b> Einlagern wählen, alles nacheinander scannen, Menge & Datum bestätigen – fertig.</div>`;
  $("#photo-in").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; scanPhoto(f); };
  $("#manual").onsubmit = (e) => { e.preventDefault(); const c = $("#manual-code").value.trim(); if (c) { $("#manual-code").value = ""; scanBusy = true; handleCode(c); } };
  view.onclick = (e) => {
    const m = e.target.closest("[data-mode]");
    if (m) { location.hash = `#/scan?mode=${m.dataset.mode}`; return; }
    if (e.target.closest("[data-cam]")) { scanner ? stopScanner().then(() => renderScan(new URLSearchParams())) : startScanner(); return; }
    if (e.target.closest("[data-manual]")) { $("#manual").classList.toggle("hidden"); $("#manual-code").focus(); return; }
    if (e.target.closest("[data-new]")) newProductSheet({}, () => {});
  };
  S.refresh = null;
  if (live && store.get("camOn", false)) startScanner();
}
// Kamera-Stopp merken, wenn bewusst ausgeschaltet
document.addEventListener("click", (e) => { if (e.target.closest("#cam-btn") && scanner) store.set("camOn", false); }, true);

// ---------------------------------------------------------------------------
// Seite: Einkaufsliste
// ---------------------------------------------------------------------------
async function renderEinkauf() {
  view.innerHTML = `<div class="skeleton"></div>`;
  const items = (await api("shopping")).items;
  const ticked = new Set(store.get("ticked", []));
  view.innerHTML = `
    <div class="page-head"><div><h1>Einkaufsliste</h1><p class="muted small">Alles unter dem Mindestbestand</p></div><span class="spacer"></span>
      ${items.length ? `<button class="btn small" data-share>📋 Teilen / Kopieren</button>` : ""}</div>
    ${items.length ? `<div class="list">${items.map((x) => `
      <div class="item" data-tick="${x.id}" style="${ticked.has(x.id) ? "opacity:.5" : ""}">
        <input type="checkbox" ${ticked.has(x.id) ? "checked" : ""} aria-label="erledigt" tabindex="-1">
        <div class="avatar" style="width:40px;height:40px;font-size:20px">${esc(x.category_icon || "📦")}</div>
        <div class="item-main"><div class="item-title" style="${ticked.has(x.id) ? "text-decoration:line-through" : ""}">${esc(x.name)} <span class="brand-s">${esc(x.brand)}</span></div>
          <div class="item-meta">noch ${x.total} von ${x.min_stock} ${esc(x.unit)}</div></div>
        <div class="qty"><div class="qty-num">+${x.min_stock - x.total}<small>${esc(x.unit)}</small></div>
          <button class="icon-btn" data-open="${x.id}" aria-label="Details">›</button></div>
      </div>`).join("")}</div>`
      : `<div class="card empty-state"><div class="big">🛒</div><h3>Nichts zu besorgen</h3>
         <p>Legt bei Produkten einen <b>Mindestbestand</b> fest – fällt der Vorrat darunter, landet es automatisch hier.</p></div>`}
    ${footer()}`;
  view.onclick = (e) => {
    const o = e.target.closest("[data-open]");
    if (o) { e.stopPropagation(); return run(() => openProduct(Number(o.dataset.open), () => renderEinkauf())); }
    const t = e.target.closest("[data-tick]");
    if (t) {
      const id = Number(t.dataset.tick);
      ticked.has(id) ? ticked.delete(id) : ticked.add(id);
      store.set("ticked", [...ticked].filter((i) => items.some((x) => x.id === i)));
      return renderEinkauf();
    }
    if (e.target.closest("[data-share]")) {
      const text = "🛒 Einkaufsliste (BALLager)\n" + items.filter((x) => !ticked.has(x.id)).map((x) => `• ${x.min_stock - x.total}× ${x.name}${x.brand ? " (" + x.brand + ")" : ""}`).join("\n");
      if (navigator.share) navigator.share({ text }).catch(() => {});
      else navigator.clipboard?.writeText(text).then(() => toast("📋 Kopiert!"), () => toast("Kopieren nicht möglich", { error: true }));
    }
  };
  S.refresh = () => renderEinkauf();
}

// ---------------------------------------------------------------------------
// Seite: Verlauf
// ---------------------------------------------------------------------------
async function renderHistory() {
  view.innerHTML = `<div class="skeleton"></div>`;
  const items = (await api("history?limit=400")).items;
  const groups = new Map();
  for (const m of items) {
    const d = new Date(m.created_at).toLocaleDateString("de-AT", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d).push(m);
  }
  view.innerHTML = `<div class="page-head"><div><h1>Verlauf</h1><p class="muted small">Alle Bewegungen im Vorrat</p></div></div>
    ${items.length ? [...groups].map(([d, ms]) => `<div class="group-head">${esc(d)}</div><div class="card" style="padding:4px 16px">${ms.map(historyRow).join("")}</div>`).join("")
      : `<div class="card empty-state"><div class="big">🕒</div><h3>Noch keine Bewegungen</h3></div>`}`;
  view.onclick = null;
  S.refresh = null;
}

// ---------------------------------------------------------------------------
// Seite: Einstellungen
// ---------------------------------------------------------------------------
async function renderSettings() {
  view.innerHTML = `<div class="skeleton"></div>`;
  const [st, bk] = await Promise.all([api("settings"), api("backups").catch(() => ({ items: [], path: "" }))]);
  let haServices = [];
  if (st.ha) haServices = (await api("ha/services").catch(() => ({ services: [] }))).services || [];
  const n = st.notify;
  const allHa = [...new Set([...haServices, ...n.ha_services])];
  const theme = store.get("theme", "auto");
  view.innerHTML = `
    <div class="page-head"><div><h1>Einstellungen</h1><p class="muted small">Erinnerungen, Kategorien, Lagerorte & Daten</p></div></div>

    <section class="card" id="notify-card">
      <div class="row"><h2>🔔 Erinnerungen</h2><span class="spacer"></span>
        <label class="switch"><input type="checkbox" id="n-enabled" ${n.enabled ? "checked" : ""}><i></i></label></div>
      <p class="muted small" style="margin:6px 0 10px">Einmal täglich prüft BALLager, was bald abläuft, und schickt eine freundliche Zusammenfassung.</p>
      <label class="field"><span>Erinnern … Tage vor Ablauf</span></label>
      <div class="chips" id="thr">${n.thresholds.map((t) => `<button type="button" class="chip on" data-thr="${t}">${t === 0 ? "am Ablauftag" : t + (t === 1 ? " Tag" : " Tage")} <span class="x">✕</span></button>`).join("")}
        <span class="row" style="gap:6px"><input type="number" id="thr-new" min="0" max="3650" placeholder="Tage" style="width:90px;min-height:36px;padding:4px 10px"><button type="button" class="chip add" data-thradd>+ hinzufügen</button></span></div>
      <div class="form-grid" style="margin-top:12px">
        <label class="field"><span>Uhrzeit der Prüfung</span><input type="time" id="n-time" value="${esc(n.time)}"></label>
        <label class="field"><span>„Bald ablaufend“ in der App (Tage)</span><input type="number" id="ui-soon" min="1" max="365" value="${st.ui.soon_days}"></label>
        <label class="check full"><input type="checkbox" id="n-expired" ${n.notify_expired ? "checked" : ""}> Abgelaufenes einmalig melden</label>
        <label class="check full"><input type="checkbox" id="n-low" ${n.include_low_stock ? "checked" : ""}> Einkaufsliste mitschicken</label>
      </div>

      <div class="channel">
        <div class="channel-head"><span class="ico">🏠</span><div class="grow"><div class="title" style="font-weight:900">Home Assistant</div>
          <div class="tiny muted">Push aufs Handy (Companion-App), Telegram, WhatsApp- oder Mail-Integrationen …</div></div></div>
        <div class="channel-body">
          ${st.ha ? `<div class="stack" id="ha-list">${allHa.map((s) => `<label class="check"><input type="checkbox" value="${esc(s)}" ${n.ha_services.includes(s) ? "checked" : ""}> ${esc(s)}</label>`).join("")}</div>
            <div class="row" style="margin-top:10px"><input id="ha-new" placeholder="weiterer Dienst, z.B. notify.whatsapp"><button type="button" class="btn small" data-haadd>+</button></div>`
          : `<div class="hint info">Verfügbar, sobald BALLager als Home-Assistant-Add-on läuft.</div>`}
        </div>
      </div>

      <div class="channel">
        <div class="channel-head"><span class="ico">✉️</span><div class="grow"><div style="font-weight:900">E-Mail</div><div class="tiny muted">direkt über euren Mail-Anbieter (SMTP)</div></div>
          <label class="switch"><input type="checkbox" id="m-enabled" ${n.email.enabled ? "checked" : ""}><i></i></label></div>
        <div class="channel-body form-grid" id="mail-body">
          <label class="field"><span>SMTP-Server</span><input id="m-host" value="${esc(n.email.host)}" placeholder="z.B. smtp.gmail.com"></label>
          <label class="field"><span>Port</span><input id="m-port" type="number" value="${n.email.port}"></label>
          <label class="field"><span>Verschlüsselung</span><select id="m-sec">
            ${[["starttls", "STARTTLS (587)"], ["ssl", "SSL/TLS (465)"], ["none", "keine"]].map(([k, l]) => `<option value="${k}" ${n.email.security === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          <label class="field"><span>Benutzer</span><input id="m-user" value="${esc(n.email.username)}" autocomplete="off"></label>
          <label class="field"><span>Passwort / App-Passwort</span><input id="m-pass" type="password" value="${esc(n.email.password)}" autocomplete="new-password"></label>
          <label class="field"><span>Absender</span><input id="m-from" value="${esc(n.email.sender)}" placeholder="optional"></label>
          <label class="field full"><span>Empfänger (mehrere mit Komma)</span><input id="m-to" value="${esc(n.email.recipients)}" placeholder="papa@example.at, mama@example.at"></label>
        </div>
      </div>

      <div class="channel">
        <div class="channel-head"><span class="ico">💬</span><div class="grow"><div style="font-weight:900">WhatsApp</div><div class="tiny muted">kostenlos über CallMeBot</div></div>
          <label class="switch"><input type="checkbox" id="w-enabled" ${n.whatsapp.enabled ? "checked" : ""}><i></i></label></div>
        <div class="channel-body">
          <details class="small" style="margin-bottom:10px"><summary style="cursor:pointer;font-weight:800">So bekommt ihr euren API-Key</summary>
            <ol style="margin:8px 0 0;padding-left:20px">
              <li>Auf <b>callmebot.com</b> → „Free WhatsApp API“ die aktuelle Bot-Nummer nachsehen und als Kontakt speichern.</li>
              <li>Dem Bot per WhatsApp schreiben: <i>I allow callmebot to send me messages</i></li>
              <li>Ihr bekommt einen API-Key zurück – hier mit eurer Nummer (z.B. +43664…) eintragen.</li></ol></details>
          <div id="wa-list" class="stack">${n.whatsapp.recipients.map(waRow).join("")}</div>
          <button type="button" class="btn small" style="margin-top:10px" data-waadd>+ Person hinzufügen</button>
        </div>
      </div>

      <div class="row wrap" style="margin-top:16px">
        <button class="btn primary" data-nsave>💾 Speichern</button>
        <button class="btn" data-ntest>📨 Testnachricht</button>
        <button class="btn" data-npreview>👀 Vorschau</button>
        <button class="btn" data-nrun>🔔 Jetzt prüfen & senden</button>
      </div>
      <div id="n-out" style="margin-top:12px"></div>
    </section>

    <div class="grid2" style="margin-top:16px">
      <section class="card"><h2>🗂️ Kategorien</h2><div class="mlist" id="cat-list" style="margin-top:10px">${S.meta.categories.map((c) => masterRow("categories", c)).join("")}</div>
        ${masterAdd("categories", "📦", "Neue Kategorie")}</section>
      <section class="card"><h2>📍 Lagerorte</h2><div class="mlist" id="loc-list" style="margin-top:10px">${S.meta.locations.map((l) => masterRow("locations", l)).join("")}</div>
        ${masterAdd("locations", "🏠", "Neuer Lagerort")}</section>
    </div>

    <section class="card" style="margin-top:16px">
      <h2>🛟 Daten & Sicherung</h2>
      ${bk.status ? `<div class="hint ${bk.status.integrity === "ok" ? "info" : ""}" style="margin:10px 0;${bk.status.integrity === "ok" ? "" : "background:var(--danger-l)"}">
        ${bk.status.integrity === "ok" ? "✅ Datenbank geprüft und in Ordnung" : "⚠️ Datenbank-Prüfung: " + esc(bk.status.integrity)}
        · ${bk.counts.products} Produkte · ${bk.counts.stock} Chargen · ${bk.counts.movements} Bewegungen
        <div class="tiny muted">Geprüft beim Start: ${esc((bk.status.checked_at || "").replace("T", " "))} · Schema-Version ${bk.status.schema}</div></div>` : ""}
      <p class="muted small" style="margin:6px 0 12px">Die Datenbank wird jeden Tag automatisch gesichert und geprüft (14 Tage + 24 Monate) und ist außerdem Teil eurer Home-Assistant-Backups. Vor jedem Update mit Datenbank-Änderung legt BALLager zusätzlich eine Sicherheitskopie an.</p>
      <div class="row wrap">
        <a class="btn" href="api/export.csv">📄 Export (CSV / Excel)</a>
        <a class="btn" href="api/backup/download">💾 Datenbank herunterladen</a>
        <button class="btn" data-backup>🛟 Jetzt sichern</button>
        <a class="btn" href="#/verlauf">🕒 Verlauf</a>
      </div>
      ${bk.items.length ? `<details style="margin-top:12px"><summary class="small" style="cursor:pointer;font-weight:800">${bk.items.length} Sicherungen in ${esc(bk.path)}</summary>
        <div class="tiny muted" style="margin-top:6px">${bk.items.slice(0, 40).map((b) => `${esc(b.name)} · ${(b.size / 1024).toFixed(0)} KB`).join("<br>")}</div></details>` : ""}
    </section>

    <section class="card" style="margin-top:16px">
      <h2>🎨 Darstellung</h2>
      <div class="chips" style="margin-top:10px">${[["auto", "🌗 Automatisch"], ["light", "☀️ Hell"], ["dark", "🌙 Dunkel"]].map(([k, l]) => `<button class="chip ${theme === k ? "on" : ""}" data-theme="${k}">${l}</button>`).join("")}</div>
    </section>
    <div class="footer-logo"><img src="static/icon.svg" alt=""><div style="font-weight:900;font-size:18px"><span class="c-coral">BALLa</span>ger</div><div class="tiny">Version ${esc(S.meta.version)} · Unser Vorrat. Gut sortiert.</div></div>`;

  const collect = () => ({
    notify: {
      enabled: $("#n-enabled").checked,
      thresholds: $$("[data-thr]").map((b) => Number(b.dataset.thr)),
      time: $("#n-time").value || "08:00",
      notify_expired: $("#n-expired").checked,
      include_low_stock: $("#n-low").checked,
      ha_services: $$("#ha-list input:checked").map((i) => i.value),
      email: {
        enabled: $("#m-enabled").checked, host: $("#m-host").value, port: Number($("#m-port").value) || 587,
        security: $("#m-sec").value, username: $("#m-user").value, password: $("#m-pass").value,
        sender: $("#m-from").value, recipients: $("#m-to").value,
      },
      whatsapp: {
        enabled: $("#w-enabled").checked,
        recipients: $$("#wa-list .wa-row").map((r) => ({ name: $(".wa-name", r).value, phone: $(".wa-phone", r).value, apikey: $(".wa-key", r).value })),
      },
    },
    ui: { soon_days: Number($("#ui-soon").value) || 30 },
  });
  const saveNotify = async (quiet) => { await api("settings", { method: "PUT", body: collect() }); await loadMeta(); if (!quiet) toast("💾 Einstellungen gespeichert"); };
  const out = (html) => { $("#n-out").innerHTML = html; };
  const resultHtml = (r) => `${r.sent?.length ? `<div class="hint info">✅ Gesendet über: ${esc(r.sent.join(", "))}</div>` : ""}
    ${r.errors?.length ? `<div class="hint" style="background:var(--danger-l);margin-top:8px">⚠️ ${r.errors.map(esc).join("<br>")}</div>` : ""}
    ${r.message ? `<pre class="preview" style="margin-top:8px">${esc(r.message)}</pre>` : ""}`;

  view.onclick = (e) => {
    const t = e.target;
    const thr = t.closest("[data-thr]");
    if (thr) return thr.remove();
    if (t.closest("[data-thradd]")) {
      const v = parseInt($("#thr-new").value, 10);
      if (isNaN(v) || v < 0) return;
      if ($$("[data-thr]").some((b) => Number(b.dataset.thr) === v)) return;
      $("#thr").insertAdjacentHTML("afterbegin", `<button type="button" class="chip on" data-thr="${v}">${v === 0 ? "am Ablauftag" : v + (v === 1 ? " Tag" : " Tage")} <span class="x">✕</span></button>`);
      $("#thr-new").value = "";
      return;
    }
    if (t.closest("[data-haadd]")) {
      const v = $("#ha-new").value.trim();
      if (!v.includes(".")) return toast("Format: domain.dienst, z.B. notify.mobile_app_handy", { error: true });
      $("#ha-list").insertAdjacentHTML("beforeend", `<label class="check"><input type="checkbox" value="${esc(v)}" checked> ${esc(v)}</label>`);
      $("#ha-new").value = "";
      return;
    }
    if (t.closest("[data-waadd]")) return $("#wa-list").insertAdjacentHTML("beforeend", waRow({}));
    if (t.closest("[data-wadel]")) return t.closest(".wa-row").remove();
    if (t.closest("[data-nsave]")) return run(() => saveNotify(false));
    if (t.closest("[data-ntest]")) return run(async () => { await saveNotify(true); out(`<div class="muted small">Sende …</div>`); out(resultHtml(await api("notify/test", { method: "POST" }))); });
    if (t.closest("[data-npreview]")) return run(async () => { await saveNotify(true); const r = await api("notify/preview"); out(`<pre class="preview">${esc(r.message || "Aktuell ist nichts fällig – es würde keine Nachricht verschickt. 🎉")}</pre>`); });
    if (t.closest("[data-nrun]")) return run(async () => { await saveNotify(true); out(`<div class="muted small">Prüfe …</div>`); out(resultHtml(await api("notify/run", { method: "POST" }))); });
    if (t.closest("[data-backup]")) return run(async () => { const r = await api("backups", { method: "POST" }); toast("🛟 Gesichert: " + r.path.split("/").pop()); });
    const th = t.closest("[data-theme]");
    if (th) { store.set("theme", th.dataset.theme); applyTheme(); $$("[data-theme]").forEach((b) => b.classList.toggle("on", b === th)); return; }
    const del = t.closest("[data-mdel]");
    if (del) return run(async () => {
      const [table, id] = del.dataset.mdel.split(":");
      const what = table === "categories" ? "Kategorie" : "Lagerort";
      if (!(await ask(`${what} löschen? Produkte bleiben erhalten, nur die Zuordnung entfällt.`, { ok: "Löschen", danger: true }))) return;
      await api(`${table}/${id}`, { method: "DELETE" });
      await loadMeta(); toast("🗑️ Gelöscht"); renderSettings();
    });
    const add = t.closest("[data-madd]");
    if (add) return run(async () => {
      const box = add.closest(".madd");
      const name = $(".name-in", box).value.trim();
      if (!name) return;
      await api(add.dataset.madd, { method: "POST", body: { name, icon: $(".emoji-in", box).value || "📦", color: randomColor() } });
      await loadMeta(); toast("✨ Hinzugefügt"); renderSettings();
    });
  };
  view.onchange = (e) => {
    const row = e.target.closest("[data-mrow]");
    if (!row) return;
    const [table, id] = row.dataset.mrow.split(":");
    const body = { name: $(".name-in", row).value, icon: $(".emoji-in", row).value };
    const col = $(".color-in", row);
    if (col) body.color = col.value;
    run(async () => { await api(`${table}/${id}`, { method: "PUT", body }); await loadMeta(); toast("💾 Gespeichert"); });
  };
  S.refresh = null;
}

const COLORS = ["#FF6B5B", "#FFC145", "#2EC4A5", "#5AA9E6", "#B5679E", "#F4A259", "#43AA8B", "#9B8DE0", "#EF476F", "#6C7A99"];
const randomColor = () => COLORS[Math.floor(Math.random() * COLORS.length)];
function masterRow(table, x) {
  return `<div class="mrow" data-mrow="${table}:${x.id}">
    <input class="emoji-in" value="${esc(x.icon)}" maxlength="4" aria-label="Symbol">
    <input class="name-in" value="${esc(x.name)}" aria-label="Name">
    ${table === "categories" ? `<input type="color" class="color-in" value="${esc(x.color)}" aria-label="Farbe" style="width:44px;padding:4px;min-height:40px">` : ""}
    <button class="icon-btn" data-mdel="${table}:${x.id}" aria-label="löschen">🗑️</button></div>`;
}
function masterAdd(table, icon, ph) {
  return `<div class="mrow madd" style="display:flex;gap:10px;margin-top:10px;align-items:center">
    <input class="emoji-in" value="${icon}" maxlength="4" style="width:56px;text-align:center;font-size:20px;padding:6px">
    <input class="name-in" placeholder="${ph}" style="flex:1">
    <button class="icon-btn plus" data-madd="${table}" aria-label="hinzufügen">+</button></div>`;
}
function waRow(r) {
  return `<div class="wa-row form-grid" style="grid-template-columns:1fr 1.3fr 1.3fr auto;align-items:end">
    <label class="field"><span>Name</span><input class="wa-name" value="${esc(r.name)}" placeholder="Mama"></label>
    <label class="field"><span>Telefon</span><input class="wa-phone" value="${esc(r.phone)}" placeholder="+43664…" inputmode="tel"></label>
    <label class="field"><span>API-Key</span><input class="wa-key" value="${esc(r.apikey)}" autocomplete="off"></label>
    <button type="button" class="icon-btn" data-wadel aria-label="entfernen" style="margin-bottom:3px">✕</button></div>`;
}

// ---------------------------------------------------------------------------
// Router & Start
// ---------------------------------------------------------------------------
function applyTheme() {
  const t = store.get("theme", "auto");
  if (t === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}

const ROUTES = { "": renderHome, vorrat: renderVorrat, scan: renderScan, einkauf: renderEinkauf, einstellungen: renderSettings, verlauf: renderHistory };
async function route() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  await stopScanner();
  closeSheet(true);
  const key = ROUTES[path] ? path : "";
  const navKey = key === "" ? "home" : key === "verlauf" ? "einstellungen" : key;
  $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === navKey));
  view.onclick = null; view.onchange = null;
  window.scrollTo(0, 0);
  try { await ROUTES[key](new URLSearchParams(query || "")); }
  catch (e) { console.error(e); view.innerHTML = `<div class="card empty-state"><div class="big">😵</div><h3>Ups, da hat etwas nicht geklappt</h3><p>${esc(e.message)}</p><button class="btn primary" onclick="location.reload()">Neu laden</button></div>`; }
}

window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopScanner();
  else if (location.hash.startsWith("#/scan") && $("#sheet").hidden) route();
});
applyTheme();
loadMeta().then(route).catch((e) => {
  view.innerHTML = `<div class="card empty-state"><div class="big">🔌</div><h3>Keine Verbindung zu BALLager</h3><p>${esc(e.message)}</p><button class="btn primary" onclick="location.reload()">Nochmal versuchen</button></div>`;
});
