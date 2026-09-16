/**
 * Mini App «Шёпот» — клиент GitHub Pages → Cloudflare Tunnel → FastAPI.
 * URL туннеля обновляется скриптом restart-tunnel.sh при перезапуске cloudflared.
 */
window.SHEPOT_API_BASE =
  window.SHEPOT_API_BASE || "https://REPLACE_WITH_TUNNEL.trycloudflare.com";
const API_BASE = String(window.SHEPOT_API_BASE).replace(/\/$/, "");

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);

function setStatus(text) {
  const el = $("status-text");
  if (el) el.textContent = text;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Единый fetch: подставляет initData, логирует ошибки, возвращает JSON.
 */
async function apiFetch(path, options = {}) {
  const method = options.method || "GET";
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  const initData =
    options.initData ??
    tg?.initData ??
    window.Telegram?.WebApp?.initData ??
    "";
  if (initData) headers["X-Telegram-Init-Data"] = initData;

  const url = `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: options.body != null ? JSON.stringify(options.body) : undefined,
    });
  } catch (err) {
    console.error("[Shepot] network", err);
    throw new Error(
      `Нет связи с API (${API_BASE}). Tunnel/бот не запущен? ${err?.message || err}`
    );
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || data.message || `HTTP ${res.status}`;
    console.error("[Shepot] api error", res.status, data);
    throw new Error(msg);
  }
  return data;
}

function renderBadges(labels) {
  const box = $("blocked-badges");
  if (!box) return;
  box.innerHTML = "";
  if (!labels?.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  for (const label of labels) {
    const span = document.createElement("span");
    span.className = "badge";
    span.textContent = label;
    box.appendChild(span);
  }
}

function renderHistory(quizzes) {
  const list = $("history-list");
  list.innerHTML = "";
  if (!quizzes?.length) {
    list.innerHTML = "<li class='muted'>Пока нет завершённых квизов</li>";
    return;
  }
  for (const q of quizzes) {
    const li = document.createElement("li");
    const date = q.date ? new Date(q.date).toLocaleDateString("ru-RU") : "—";
    const analysis = (q.analysis || "").trim();
    const short =
      analysis.length > 220 ? `${analysis.slice(0, 220).trim()}…` : analysis;
    li.innerHTML = `
      <div class="topic">${escapeHtml(q.topic || "Квиз")}</div>
      <div class="date">${escapeHtml(date)} · ${escapeHtml(q.status || "")}</div>
      ${short ? `<div class="analysis">${escapeHtml(short)}</div>` : ""}
    `;
    list.appendChild(li);
  }
}

async function main() {
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.themeParams?.bg_color) {
      document.documentElement.style.setProperty("--tg-bg", tg.themeParams.bg_color);
    }
  }

  const initData = tg?.initData || "";
  if (!initData) {
    $("greeting").textContent = "Откройте Mini App из Telegram";
    setStatus("initData пуст — откройте приложение через бота «Шёпот».");
    return;
  }

  try {
    setStatus("Проверяю подпись Telegram…");
    const auth = await apiFetch("/api/auth", {
      method: "POST",
      body: { initData },
    });

    $("greeting").textContent = `Привет, ${auth.name || "друг"}!`;
    $("subtitle").textContent = `${auth.assistant} рядом · ${auth.project}`;
    setStatus(
      auth.couple_id
        ? `Авторизация ок · couple_id: ${auth.couple_id}`
        : "Авторизация ок · пара ещё не настроена"
    );

    const profile = await apiFetch("/api/profile");
    const pair = $("pair-line");
    if (pair) {
      const you = profile.name || auth.name || "Вы";
      const partner = profile.partner_name || "";
      pair.hidden = false;
      pair.textContent = partner ? `${you} × ${partner}` : you;
    }

    if (!profile.is_completed) {
      $("onboarding-card").hidden = false;
      const link = $("onboarding-link");
      const bot = profile.bot_username || "Familia_Quiz_bot";
      if (link) link.href = `https://t.me/${bot}?start=onboarding`;
    }

    $("profile-card").hidden = false;
    $("profile-summary").textContent =
      profile.ai_summary ||
      "Портрет ещё не собран — пройдите анкету в боте.";
    renderBadges(profile.blocked_labels);

    const history = await apiFetch("/api/history?limit=10");
    $("history-card").hidden = false;
    renderHistory(history.quizzes);

    setStatus("Готово");
  } catch (err) {
    console.error("[Shepot]", err);
    $("greeting").textContent = "Не удалось войти";
    setStatus(String(err.message || err));
  }
}

main();
