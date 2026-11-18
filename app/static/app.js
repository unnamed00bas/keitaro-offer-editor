const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
    err.status = r.status;
    err.data = data;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = err ? "err" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), err ? 6000 : 2500);
}

/* ---------- автокомплит офферов (ходит в наш /api/offers, тот — в Keitaro) ---------- */
function autocomplete(root, onPick) {
  const input = $(".ac-input", root);
  const list = $(".ac-list", root);
  let timer, items = [], sel = -1;
  const render = () => {
    list.innerHTML = items.map((o, i) => `<li data-i="${i}" class="${i === sel ? "sel" : ""}">${esc(o.id)} — ${esc(o.name)}</li>`).join("");
    list.style.display = items.length ? "block" : "none";
  };
  const pick = (o) => { input.value = `${o.id} — ${o.name}`; items = []; render(); onPick(o); };
  input.addEventListener("input", () => {
    onPick(null);
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        items = await api("GET", `/api/offers?q=${encodeURIComponent(input.value)}`);
        const q = input.value.trim();
        // карточки части офферов скрыты правами в Keitaro — разрешаем добавить по точному ID
        if (/^\d+$/.test(q) && !items.some((o) => String(o.id) === q)) items.push({ id: +q, name: "по ID (нет в списке, доступном этому пользователю Keitaro)" });
        sel = -1; render();
      }
      catch (e) { toast(e.message, true); }
    }, 200);
  });
  input.addEventListener("focus", () => input.dispatchEvent(new Event("input")));
  input.addEventListener("keydown", (e) => {
    if (!items.length) return;
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, items.length - 1); render(); e.preventDefault(); }
    if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    if (e.key === "Enter" && sel >= 0) { pick(items[sel]); e.preventDefault(); }
  });
  list.addEventListener("mousedown", (e) => { const li = e.target.closest("li"); if (li) pick(items[+li.dataset.i]); });
  input.addEventListener("blur", () => setTimeout(() => { items = []; render(); }, 150));
  return { clear: () => { input.value = ""; onPick(null); } };
}

/* ---------- вкладки ---------- */
function showTab() {
  const tab = location.hash.startsWith("#editor") ? "editor" : "create";
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  const m = location.hash.match(/^#editor\/(\d+)/);
  if (m && +m[1] !== currentKtId) openCampaign(+m[1]);
  if (tab === "editor") loadCampaignList();
}

/* ---------- создание ---------- */
async function initCreate() {
  const cfg = await api("GET", "/api/config");
  $("#prefix").textContent = cfg.prefix;
  $("#create-hint").textContent =
    `Домен id ${cfg.domain_id}, группа id ${cfg.group_id}, источник id ${cfg.traffic_source_id}. ` +
    `Поток 1: выбранные страны → ${cfg.google_url}. Поток 2: все остальные → оффер (100%).`;
  const form = $("#create-form");
  const ac = autocomplete($("#create-ac"), (o) => (form.offer_id.value = o ? o.id : ""));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.offer_id.value) return toast("Выберите оффер из списка", true);
    const btn = $("button", form);
    btn.disabled = true;
    try {
      const c = await api("POST", "/api/campaigns", { name: form.name.value, geo: form.geo.value, offer_id: +form.offer_id.value });
      $("#create-result").innerHTML = `<div class="result">Создана кампания <b>${esc(c.name)}</b> (KT id ${c.kt_id}).
        <a href="${esc(c.kt_url)}" target="_blank" rel="noopener">Открыть в Keitaro ↗</a> ·
        <a href="#editor/${c.kt_id}">Открыть в редакторе</a></div>`;
      form.reset(); ac.clear();
      toast("Кампания создана");
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; }
  });
}

/* ---------- модалка ---------- */
function modal(html, buttons) {
  const m = $("#modal");
  $(".modal-body", m).innerHTML = html;
  const actions = $(".modal-actions", m);
  actions.innerHTML = "";
  return new Promise((resolve) => {
    buttons.forEach(([label, value, cls]) => {
      const b = document.createElement("button");
      b.textContent = label;
      if (cls) b.className = cls;
      b.onclick = () => { m.hidden = true; resolve(value); };
      actions.appendChild(b);
    });
    m.hidden = false;
  });
}

/* ---------- редактор ---------- */
let currentKtId = null;
let currentCampaign = null;
let stats = null;

async function loadCampaignList() {
  try {
    const list = await api("GET", "/api/campaigns");
    const sel = $("#campaign-select");
    sel.innerHTML = `<option value="">— кампании с префиксом (${list.length}) —</option>` +
      list.map((c) => `<option value="${c.kt_id}" ${c.kt_id === currentKtId ? "selected" : ""}>${esc(c.name)} (#${c.kt_id})</option>`).join("");
  } catch (e) { toast(e.message, true); }
}

function setCampaign(ktId, url) {
  currentKtId = ktId;
  $("#campaign-id").value = ktId || "";
  const v = $("#btn-view");
  if (url) v.href = url; else v.removeAttribute("href");
  if (ktId && location.hash !== `#editor/${ktId}`) history.replaceState(null, "", `#editor/${ktId}`);
}

async function openCampaign(ktId) {
  if (ktId !== currentKtId) { $("#streams").innerHTML = ""; stats = null; }
  setCampaign(ktId);
  try {
    renderCampaign(await api("GET", `/api/campaigns/${ktId}`));
  } catch (e) {
    await fetchCampaign(ktId); // в редакторе ещё нет — подтягиваем из Keitaro
  }
}

async function fetchCampaign(ktId) {
  try {
    const c = await api("POST", `/api/campaigns/${ktId}/fetch`);
    renderCampaign(c);
    const conflicts = c.streams.reduce((n, s) => n + (s.conflicts || 0), 0);
    toast(conflicts ? `Потоки загружены. Расхождений с черновиком: ${conflicts}, они отмечены` : "Потоки загружены из Keitaro", !!conflicts);
  } catch (e) { toast(e.message, true); }
}

function renderCampaign(c) {
  currentCampaign = c;
  setCampaign(c.kt_id, c.kt_url);
  $("#editor-hint").textContent = `${c.name}${c.geo ? " · гео " + c.geo : ""} · загружено ${c.fetched_at ? new Date(c.fetched_at + "Z").toLocaleString() : "—"}`;
  const box = $("#streams");
  box.innerHTML = "";
  c.streams.forEach((s) => box.appendChild(streamEl(s)));
  if (!c.streams.length) box.innerHTML = `<p class="hint">В кампании нет потоков.</p>`;
  loadStats();
}

async function loadStats() {
  if (!currentKtId) return;
  try {
    stats = await api("GET", `/api/campaigns/${currentKtId}/stats?days=${$("#stats-days").value}`);
    loadStatsCells();
  } catch (e) { toast("Статистика: " + e.message, true); }
}

function sparkline(series) {
  const w = 84, h = 22, max = Math.max(1, ...series);
  const pts = series.map((v, i) => `${(i * w / Math.max(1, series.length - 1)).toFixed(1)},${(h - 2 - v / max * (h - 4)).toFixed(1)}`).join(" ");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="#2f6f9f" stroke-width="1.5" points="${pts}"/></svg>`;
}

function fillStats(el) {
  const key = el.dataset.stats;
  const trendEl = el.parentElement.querySelector(`[data-trend="${key}"]`);
  const o = stats && stats.offers[key];
  if (!stats) return;
  if (!o) {
    el.innerHTML = `<span class="flat">нет кликов</span>`;
    if (trendEl) trendEl.innerHTML = sparkline(stats.days.map(() => 0)) + ` <span class="flat">—</span>`;
    return;
  }
  el.innerHTML = `<b>${o.clicks}</b> кл. · ${o.conversions} конв. · CR ${o.cr}% · $${o.revenue}<br><span class="flat">сегодня ${o.today}</span>`;
  let delta = `<span class="flat">0%</span>`;
  if (o.trend_new) delta = `<span class="up">new</span>`;
  else if (o.trend !== null) delta = `<span class="${o.trend > 0 ? "up" : o.trend < 0 ? "down" : "flat"}">${o.trend > 0 ? "▲" : o.trend < 0 ? "▼" : ""}${Math.abs(o.trend)}%</span>`;
  if (trendEl) trendEl.innerHTML = sparkline(o.series) + " " + delta;
}

function streamEl(s) {
  const el = document.createElement("div");
  el.dataset.stream = s.id;
  if (!s.has_offers) {
    el.className = "stream plain";
    el.innerHTML = `Stream: <b>${esc(s.name)}</b> <small>(#${s.position}, KT id ${s.kt_id}) · <span data-today="${s.kt_id}"></span></small>`;
    if (stats) setTimeout(loadStatsCells, 0);
    return el;
  }
  el.className = "stream" + (s.draft ? " draft" : "");
  const rows = s.offers.map((o) => {
    const removed = o.status === "removed";
    const key = `${s.kt_id}:${o.offer_id}`;
    const badges = [
      o.is_new && !removed ? `<span class="badge">new</span>` : "",
      o.removed_in_kt ? `<span class="badge gray">удалён в KT</span>` : "",
      removed && !o.removed_in_kt ? `<span class="badge gray">${o.published_status === "active" ? "будет удалён" : "архив"}</span>` : "",
      !removed && o.changed && !o.is_new ? `<span class="badge">было ${o.published_share}%</span>` : "",
      o.conflict ? `<span class="badge warn">расхождение</span>` : "",
    ].join("");
    return `<tr class="${removed ? "removed" : ""}" data-offer="${o.offer_id}">
      <td><span class="oname">[${o.offer_id}] ${esc(o.name)}</span>${badges}
        ${o.conflict ? `<div class="conflict-note">⚠ ${esc(o.conflict)}</div>` : ""}</td>
      <td style="white-space:nowrap">${removed ? "0%" :
        `<span class="share" data-act="edit-share" title="Задать и закрепить долю">${o.share}%</span>
         <button class="pin ${o.pinned ? "on" : ""}" data-act="pin" title="${o.pinned ? "Открепить" : "Закрепить долю"}">📌</button>`}</td>
      <td class="stats" data-stats="${key}"></td>
      <td class="trend" data-trend="${key}"></td>
      <td style="text-align:right">${removed
        ? `<button data-act="bring-back">↺ Bring back</button>`
        : `<button class="danger" data-act="remove">🗑 Remove</button>`}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `
    <div class="stream-head">
      <div>Stream: <b>${esc(s.name)}</b> <small>(#${s.position}, KT id ${s.kt_id}) · <span data-today="${s.kt_id}"></span></small>
        ${s.draft ? `<span class="badge">черновик, не опубликован</span>` : ""}</div>
      <div>Σ <b class="${s.total === 100 ? "" : "total-bad"}">${s.total}%</b>
        <button class="primary" data-act="push" ${s.draft ? "" : "disabled"}>Push to KT</button>
        <button data-act="cancel" ${s.draft || s.conflicts ? "" : "disabled"}>Cancel</button></div>
    </div>
    <table><thead><tr><th>Offer</th><th>Share</th><th>Stats</th><th>Trends</th><th></th></tr></thead><tbody>${rows ||
      `<tr><td colspan="5" class="hint">Офферов нет</td></tr>`}</tbody></table>
    <div class="add-row">
      <div class="ac"><input class="ac-input" placeholder="Добавить оффер: ID или название"><ul class="ac-list"></ul></div>
      <button data-act="add">Add</button>
    </div>`;
  let picked = null;
  autocomplete($(".ac", el), (o) => (picked = o));
  if (stats) setTimeout(loadStatsCells, 0);
  el.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    const offerId = btn.closest("tr")?.dataset.offer;
    const base = `/api/streams/${s.id}`;
    try {
      let res;
      if (act === "add") {
        if (!picked) return toast("Выберите оффер из списка", true);
        res = await api("POST", `${base}/offers`, { offer_id: picked.id });
      } else if (act === "remove") res = await api("POST", `${base}/offers/${offerId}/remove`);
      else if (act === "bring-back") res = await api("POST", `${base}/offers/${offerId}/bring-back`);
      else if (act === "pin") {
        const o = s.offers.find((x) => x.offer_id == offerId);
        res = await api("POST", `${base}/offers/${offerId}/pin`, { pinned: !o.pinned });
      } else if (act === "edit-share") {
        const o = s.offers.find((x) => x.offer_id == offerId);
        const v = prompt("Доля оффера, % (будет закреплена)", o.share);
        if (v === null) return;
        res = await api("POST", `${base}/offers/${offerId}/pin`, { pinned: true, share: parseInt(v, 10) });
      } else if (act === "cancel") res = await api("POST", `${base}/cancel`);
      else if (act === "push") res = await pushStream(s);
      if (res) replaceStream(res);
    } catch (err) { toast(err.message, true); }
  });
  return el;
}

function loadStatsCells() {
  if (!stats) return;
  document.querySelectorAll("[data-stats]").forEach(fillStats);
  document.querySelectorAll("[data-today]").forEach((el) => {
    const st = stats.streams[el.dataset.today];
    el.textContent = `clicks today: ${st ? st.today : 0} · за ${stats.days.length} дн.: ${st ? st.clicks : 0}`;
  });
}

function replaceStream(res) {
  const old = document.querySelector(`[data-stream="${res.id}"]`);
  if (old) old.replaceWith(streamEl(res));
  if (currentCampaign) currentCampaign.streams = currentCampaign.streams.map((x) => (x.id === res.id ? res : x));
}

async function pushStream(s) {
  try {
    const res = await api("POST", `/api/streams/${s.id}/push`);
    toast("Опубликовано в Keitaro");
    return res;
  } catch (err) {
    if (err.status !== 409) throw err;
    const d = err.data.diff || {};
    const items = [
      ...(d.added_in_kt || []).map((id) => `<li>в Keitaro добавлен оффер <b>${id}</b></li>`),
      ...(d.removed_in_kt || []).map((id) => `<li>в Keitaro удалён оффер <b>${id}</b></li>`),
      ...(d.share_changed || []).map((x) => `<li>оффер <b>${x.offer_id}</b>: доля ${x.was}% → ${x.now}%</li>`),
    ].join("");
    const choice = await modal(
      `<h3>Поток изменили в Keitaro</h3><p>После последнего Fetch в Keitaro поменялось:</p><ul>${items}</ul>
       <p class="hint">«Fetch и слить» подтянет эти изменения и сохранит ваш черновик. «Опубликовать поверх» перезапишет поток вашей версией.</p>`,
      [["Отмена", null], ["Опубликовать поверх", "force", "danger"], ["Fetch и слить", "fetch", "primary"]]
    );
    if (choice === "force") {
      const res = await api("POST", `/api/streams/${s.id}/push?force=true`);
      toast("Опубликовано поверх изменений в Keitaro");
      return res;
    }
    if (choice === "fetch") await fetchCampaign(currentKtId);
    return null;
  }
}

async function showAllOffers() {
  if (!currentCampaign) return toast("Сначала откройте кампанию", true);
  const offerStreams = currentCampaign.streams.filter((s) => s.has_offers);
  const where = {};
  offerStreams.forEach((s) => s.offers.forEach((o) => (where[o.offer_id] = o.status === "active" ? `в потоке, ${o.share}%` : "в архиве")));
  const clicks = {};
  if (stats) Object.entries(stats.offers).forEach(([k, v]) => { const id = k.split(":")[1]; clicks[id] = (clicks[id] || 0) + v.clicks; });
  const all = await api("GET", "/api/offers?limit=1000");
  const rowsHtml = (q) => all
    .filter((o) => !q || `${o.id} ${o.name}`.toLowerCase().includes(q.toLowerCase()))
    .map((o) => `<tr><td>${o.id}</td><td>${esc(o.name)}</td><td>${where[o.id] || ""}</td><td>${clicks[o.id] || 0}</td>
      <td>${where[o.id] && where[o.id] !== "в архиве" || !offerStreams.length ? "" : `<button data-add="${o.id}">${where[o.id] ? "Bring back" : "Add"}</button>`}</td></tr>`).join("");
  const p = modal(
    `<h3>Все офферы Keitaro (${all.length})</h3>
     <input id="all-q" placeholder="Фильтр по ID или названию" style="margin-bottom:8px">
     <table><thead><tr><th>ID</th><th>Оффер</th><th>В кампании</th><th>Клики</th><th></th></tr></thead><tbody id="all-rows">${rowsHtml("")}</tbody></table>`,
    [["Закрыть", null]]
  );
  $("#all-q").addEventListener("input", (e) => ($("#all-rows").innerHTML = rowsHtml(e.target.value)));
  $("#all-rows").parentElement.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-add]");
    if (!b) return;
    try {
      const res = await api("POST", `/api/streams/${offerStreams[0].id}/offers`, { offer_id: +b.dataset.add });
      replaceStream(res);
      b.replaceWith(document.createTextNode("добавлен в черновик"));
    } catch (err) { toast(err.message, true); }
  });
  await p;
}

function initEditor() {
  $("#campaign-select").addEventListener("change", (e) => e.target.value && openCampaign(+e.target.value));
  $("#btn-fetch").addEventListener("click", () => {
    const id = +$("#campaign-id").value;
    if (!id) return toast("Укажите ID кампании или выберите из списка", true);
    fetchCampaign(id).then(loadCampaignList);
  });
  $("#stats-days").addEventListener("change", loadStats);
  $("#btn-all-offers").addEventListener("click", () => showAllOffers().catch((e) => toast(e.message, true)));
}

window.addEventListener("hashchange", showTab);
initCreate().catch((e) => toast(e.message, true));
initEditor();
showTab();
