/**
 * Mini App «Шёпот» — базовая инициализация.
 * API через Cloudflare Tunnel (бэкенд на VPS).
 */
window.SHEPOT_API_BASE = "https://gpl-modern-answers-afternoon.trycloudflare.com";
const API_BASE = window.SHEPOT_API_BASE;

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);

function setStatus(text) {
  $("status-text").textContent = text;
}

async function api(path, { method = "GET", body, initData } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (initData) headers["X-Telegram-Init-Data"] = initData;
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  }
  return data;
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
    li.innerHTML = `
      <div class="topic">${escapeHtml(q.topic || "Квиз")}</div>
      <div class="date">${escapeHtml(date)} · ${escapeHtml(q.status || "")}</div>
    `;
    list.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function main() {
  if (tg) {
    tg.ready();
    tg.expand();
    document.documentElement.style.setProperty("--tg-bg", tg.themeParams.bg_color || "");
  }

  const initData = tg?.initData || "";
  if (!initData) {
    $("greeting").textContent = "Откройте Mini App из Telegram";
    setStatus("initData пуст — откройте приложение через бота «Шёпот».");
    return;
  }

  try {
    setStatus("Проверяю подпись Telegram…");
    const auth = await api("/api/auth", {
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

    const profile = await api("/api/profile", { initData });
    $("profile-card").hidden = false;
    $("profile-summary").textContent =
      profile.ai_summary || "Портрет ещё не собран — пройдите анкету в боте.";
    $("blocked-topics").textContent = profile.blocked_labels?.length
      ? `Закрытые темы: ${profile.blocked_labels.join(", ")}`
      : "Закрытых тем нет";

    const history = await api("/api/history?limit=10", { initData });
    $("history-card").hidden = false;
    renderHistory(history.quizzes);
  } catch (err) {
    console.error(err);
    $("greeting").textContent = "Не удалось войти";
    setStatus(String(err.message || err));
  }
}

main();
