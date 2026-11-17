const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
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
      try { items = await api("GET", `/api/offers?q=${encodeURIComponent(input.value)}`); sel = -1; render(); }
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

/* ---------- редактор ---------- */
let currentKtId = null;

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
  if (ktId !== currentKtId) $("#streams").innerHTML = "";
  setCampaign(ktId);
  try {
    const c = await api("GET", `/api/campaigns/${ktId}`);
    renderCampaign(c);
  } catch (e) {
    // в редакторе ещё нет — подтягиваем из Keitaro
    await fetchCampaign(ktId);
  }
}

async function fetchCampaign(ktId) {
  const drafts = document.querySelectorAll(".stream.draft").length;
  if (drafts && !confirm("Есть неопубликованные изменения. Fetch заменит их состоянием из Keitaro. Продолжить?")) return;
  try {
    const c = await api("POST", `/api/campaigns/${ktId}/fetch`);
    renderCampaign(c);
    toast("Потоки загружены из Keitaro");
  } catch (e) { toast(e.message, true); }
}

function renderCampaign(c) {
  setCampaign(c.kt_id, c.kt_url);
  $("#editor-hint").textContent = `${c.name}${c.geo ? " · гео " + c.geo : ""} · загружено ${c.fetched_at ? new Date(c.fetched_at + "Z").toLocaleString() : "—"}`;
  const box = $("#streams");
  box.innerHTML = "";
  c.streams.forEach((s) => box.appendChild(streamEl(s)));
  if (!c.streams.length) box.innerHTML = `<p class="hint">В кампании нет потоков.</p>`;
}

function streamEl(s) {
  const el = document.createElement("div");
  el.dataset.stream = s.id;
  if (!s.has_offers) {
    el.className = "stream plain";
    el.innerHTML = `Stream: <b>${esc(s.name)}</b> <small>(#${s.position}, KT id ${s.kt_id})</small>`;
    return el;
  }
  el.className = "stream" + (s.draft ? " draft" : "");
  const rows = s.offers.map((o) => {
    const removed = o.status === "removed";
    const badges = [
      o.is_new && !removed ? `<span class="badge">new</span>` : "",
      o.removed_in_kt ? `<span class="badge gray">удалён в KT</span>` : "",
      removed && !o.removed_in_kt ? `<span class="badge gray">${o.published_status === "active" ? "будет удалён" : "архив"}</span>` : "",
      !removed && o.changed && !o.is_new ? `<span class="badge">было ${o.published_share}%</span>` : "",
    ].join("");
    return `<tr class="${removed ? "removed" : ""}" data-offer="${o.offer_id}">
      <td><span class="oname">[${o.offer_id}] ${esc(o.name)}</span>${badges}</td>
      <td style="white-space:nowrap">${removed ? "0%" :
        `<span class="share" data-act="edit-share" title="Задать и закрепить долю">${o.share}%</span>
         <button class="pin ${o.pinned ? "on" : ""}" data-act="pin" title="${o.pinned ? "Открепить" : "Закрепить долю"}">📌</button>`}</td>
      <td style="text-align:right">${removed
        ? `<button data-act="bring-back">↺ Bring back</button>`
        : `<button class="danger" data-act="remove">🗑 Remove</button>`}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `
    <div class="stream-head">
      <div>Stream: <b>${esc(s.name)}</b> <small>(#${s.position}, KT id ${s.kt_id})</small>
        ${s.draft ? `<span class="badge">черновик, не опубликован</span>` : ""}</div>
      <div>Σ <b class="${s.total === 100 ? "" : "total-bad"}">${s.total}%</b>
        <button class="primary" data-act="push" ${s.draft ? "" : "disabled"}>Push to KT</button>
        <button data-act="cancel" ${s.draft ? "" : "disabled"}>Cancel</button></div>
    </div>
    <table><thead><tr><th>Offer</th><th>Share</th><th></th></tr></thead><tbody>${rows ||
      `<tr><td colspan="3" class="hint">Офферов нет</td></tr>`}</tbody></table>
    <div class="add-row">
      <div class="ac"><input class="ac-input" placeholder="Добавить оффер: ID или название"><ul class="ac-list"></ul></div>
      <button data-act="add">Add</button>
    </div>`;
  let picked = null;
  autocomplete($(".ac", el), (o) => (picked = o));
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
      else if (act === "push") { res = await api("POST", `${base}/push`); toast("Опубликовано в Keitaro"); }
      el.replaceWith(streamEl(res));
    } catch (err) { toast(err.message, true); }
  });
  return el;
}

function initEditor() {
  $("#campaign-select").addEventListener("change", (e) => e.target.value && openCampaign(+e.target.value));
  $("#btn-fetch").addEventListener("click", () => {
    const id = +$("#campaign-id").value;
    if (!id) return toast("Укажите ID кампании или выберите из списка", true);
    fetchCampaign(id).then(loadCampaignList);
  });
}

window.addEventListener("hashchange", showTab);
initCreate().catch((e) => toast(e.message, true));
initEditor();
showTab();
