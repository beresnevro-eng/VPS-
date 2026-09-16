/**
 * Mini App «Шёпот» — пульт пары.
 * API через Cloudflare Tunnel (обновляется HOST_FIX_MINIAPP.sh).
 */
window.SHEPOT_API_BASE =
  window.SHEPOT_API_BASE || "https://foster-develop-vhs-advert.trycloudflare.com";
const API_BASE = String(window.SHEPOT_API_BASE).replace(/\/$/, "");
const BOT_URL = "https://t.me/Familia_Quiz_bot";

const $ = (id) => document.getElementById(id);
const getTg = () => window.Telegram?.WebApp || null;

const state = {
  tab: "home",
  auth: null,
  profile: null,
  active: null,
  history: [],
  moods: [],
  portraitWho: "me",
  starting: false,
  initData: "",
};

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function toast(msg, ms = 2600) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, ms);
}

async function apiFetch(path, options = {}) {
  const method = options.method || "GET";
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  const initData = options.initData ?? state.initData ?? getTg()?.initData ?? "";
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
    throw new Error(`Нет связи с API. ${err?.message || err}`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || data.message || `HTTP ${res.status}`;
    console.error("[Shepot] api", res.status, data);
    throw new Error(msg);
  }
  return data;
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.hidden = p.dataset.tab !== tab;
  });
  document.querySelectorAll(".tabbar .tab").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return "—";
  }
}

function discussUrl(quizId) {
  return `${BOT_URL}?start=discuss_${quizId}`;
}

/* ---------- Home CTA (3 states) ---------- */
function renderHomeCta(active) {
  const start = $("cta-start");
  const answer = $("cta-answer");
  const waiting = $("cta-waiting");
  const status = $("home-status");
  const sk = document.querySelector("#home-status-card [data-sk]");

  if (sk) sk.hidden = true;
  status.hidden = false;
  start.hidden = true;
  answer.hidden = true;
  waiting.hidden = true;

  const cta = active?.cta || "start";
  status.textContent = active?.status_text || "Можно начать разговор";

  if (cta === "answer") {
    answer.hidden = false;
    answer.href = active?.bot_url || BOT_URL;
  } else if (cta === "waiting") {
    waiting.hidden = false;
    $("cta-waiting-text").textContent = active?.status_text || "Ждём партнёра";
  } else {
    start.hidden = false;
  }
}

function renderLastQuiz(quizzes) {
  const card = $("last-quiz-card");
  const empty = $("home-empty");
  const done = (quizzes || []).filter((q) =>
    ["analyzed", "closed", "exchanging", "analysis_pending"].includes(q.status)
  );
  if (!done.length) {
    card.hidden = true;
    if (!state.active?.has_active) empty.hidden = false;
    else empty.hidden = true;
    return;
  }
  empty.hidden = true;
  const q = done[0];
  card.hidden = false;
  $("last-quiz-topic").textContent = q.topic_label || q.topic || "Квиз";
  $("last-quiz-date").textContent = fmtDate(q.date);
  $("last-quiz-analysis").textContent =
    (q.analysis || "").trim() || "Разбор Люма появится здесь.";
  const discuss = $("last-quiz-discuss");
  discuss.href = q.discuss_url || discussUrl(q.quiz_id);
  card.dataset.quizId = String(q.quiz_id);
}

const STATUS_LABELS = {
  collecting: "Сбор ответов",
  exchanging: "Обмен ответами",
  analysis_pending: "Ждём разбор",
  closed: "Завершён",
  analyzed: "С разбором",
};

function statusLabel(status) {
  if (!status) return "";
  return STATUS_LABELS[status] || status;
}

function renderHistory(quizzes) {
  const list = $("history-list");
  const empty = $("history-empty");
  list.innerHTML = "";
  if (!quizzes?.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  for (const q of quizzes) {
    const li = document.createElement("li");
    li.dataset.quizId = String(q.quiz_id);
    const label = q.topic_label || q.topic || "Квиз";
    const st = statusLabel(q.status);
    li.innerHTML = `
      <div class="topic">${escapeHtml(label)}</div>
      <div class="date">${escapeHtml(fmtDate(q.date))}${st ? ` · ${escapeHtml(st)}` : ""}</div>
      <div class="analysis preview">${escapeHtml((q.analysis || "").slice(0, 160))}</div>
    `;
    li.addEventListener("click", () => openQuizDetail(q.quiz_id));
    list.appendChild(li);
  }
}

function setDiag(lines) {
  const el = $("diag");
  if (!el) return;
  const text = (Array.isArray(lines) ? lines : [lines]).filter(Boolean).join("\n");
  el.hidden = !text;
  el.textContent = text;
}

function showBootError(title, text) {
  $("greeting").textContent = title;
  $("home-sub").textContent = "Люм рядом";
  const sk = document.querySelector("#home-status-card [data-sk]");
  if (sk) sk.hidden = true;
  $("home-status").hidden = false;
  $("home-status").textContent = text;
  $("cta-retry").hidden = false;
  $("cta-start").hidden = true;
  $("cta-answer").hidden = true;
  $("cta-waiting").hidden = true;
  $("history-empty").hidden = false;
  $("home-empty").hidden = true;
  $("pair-chip").hidden = true;
}

function extractInitData() {
  const fromTg = getTg()?.initData;
  if (fromTg && String(fromTg).length > 8) return fromTg;

  // Hash, который Telegram кладёт при открытии WebApp
  try {
    const hash = window.location.hash || sessionStorage.getItem("shepot_launch_hash") || "";
    if (hash.includes("tgWebAppData=")) {
      const params = new URLSearchParams(hash.replace(/^#/, ""));
      const raw = params.get("tgWebAppData");
      if (raw && raw.length > 8) return raw;
    }
  } catch (_) {
    /* ignore */
  }

  try {
    const saved = sessionStorage.getItem("shepot_tgWebAppData");
    if (saved && saved.length > 8) return saved;
  } catch (_) {
    /* ignore */
  }

  return "";
}

async function waitForInitData(timeoutMs = 5000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const w = getTg();
    if (w) {
      try {
        w.ready();
        w.expand?.();
      } catch (_) {
        /* ignore */
      }
    }
    const data = extractInitData();
    if (data) return data;
    await new Promise((r) => setTimeout(r, 80));
  }
  return extractInitData();
}

async function bootSession() {
  const w = getTg();
  if (w) {
    try {
      w.ready();
      w.expand();
      w.setHeaderColor?.("#14181f");
      w.setBackgroundColor?.("#14181f");
    } catch (_) {
      /* older clients */
    }
  }

  $("greeting").textContent = "Загрузка…";
  $("cta-retry").hidden = true;
  const sk = document.querySelector("#home-status-card [data-sk]");
  if (sk) sk.hidden = false;
  $("home-status").hidden = true;
  setDiag([
    `API: ${API_BASE}`,
    `Telegram SDK: ${getTg() ? "да" : "нет"}`,
    `platform: ${getTg()?.platform || "—"}`,
  ]);

  if (!getTg()) {
    showBootError(
      "Нет Telegram SDK",
      "Страница открыта вне WebApp. Нажмите «🌿 Открыть Шёпот» в чате с ботом."
    );
    return;
  }

  const initData = await waitForInitData(5000);
  state.initData = initData;
  setDiag([
    `API: ${API_BASE}`,
    `initData: ${initData ? initData.length + " символов" : "ПУСТО"}`,
    `user: ${getTg()?.initDataUnsafe?.user?.id || "—"}`,
    `platform: ${getTg()?.platform || "—"}`,
  ]);

  if (!initData) {
    console.warn("[Shepot] empty initData", {
      hash: (location.hash || "").slice(0, 80),
      saved: !!sessionStorage.getItem("shepot_tgWebAppData"),
      unsafe: getTg()?.initDataUnsafe,
    });
    showBootError(
      "Сессия не получена",
      "На macOS Telegram часто теряет сессию из‑за редиректа GitHub Pages. Закройте Mini App, в боте нажмите /menu и снова «🌿 Открыть Шёпот». Если не поможет — откройте с телефона."
    );
    setDiag([
      `API: ${API_BASE}`,
      `initData: ПУСТО`,
      `hash: ${(location.hash || "нет").slice(0, 60)}`,
      `saved: ${sessionStorage.getItem("shepot_tgWebAppData") ? "да" : "нет"}`,
      `platform: ${getTg()?.platform || "—"}`,
    ]);
    return;
  }

  try {
    // быстрый ping API до auth
    const health = await fetch(`${API_BASE}/api/health`, { method: "GET" }).then((r) =>
      r.json()
    ).catch((e) => ({ error: String(e.message || e) }));
    setDiag([
      `API: ${API_BASE}`,
      `health: ${health.status || health.error || JSON.stringify(health)}`,
      `initData: ${initData.length} символов`,
      `user: ${getTg()?.initDataUnsafe?.user?.id || "—"}`,
    ]);

    state.auth = await apiFetch("/api/auth", {
      method: "POST",
      body: { initData },
    });
    await refreshAll();
    $("cta-retry").hidden = true;
    // после успеха diag можно свернуть
    setDiag([
      `Загружено квизов: ${state.history.length}`,
      `CTA: ${state.active?.cta || "—"}`,
    ]);
  } catch (err) {
    console.error("[Shepot]", err);
    showBootError("Не удалось загрузить данные", String(err.message || err));
    setDiag([
      `API: ${API_BASE}`,
      `ошибка: ${String(err.message || err)}`,
      `initData: ${initData.length} символов`,
    ]);
  }
}

function renderPortrait() {
  const me = state.profile;
  if (!me) return;
  const partner = me.partner || {};
  const who = state.portraitWho;
  const isMe = who === "me";
  const name = isMe ? me.name : partner.name || me.partner_name || "Партнёр";
  const summary = isMe ? me.ai_summary : partner.ai_summary;
  const completed = isMe ? me.is_completed : partner.is_completed;

  $("portrait-name").textContent = name || "—";
  $("portrait-summary").textContent =
    summary ||
    (completed
      ? "Портрет ещё формируется."
      : isMe
        ? "Люм ещё не собрал ваш портрет — заполните анкету в боте."
        : "Партнёр ещё не прошёл анкету.");

  const link = $("portrait-onboarding");
  link.hidden = Boolean(isMe ? completed && summary : true);
  if (isMe && !completed) link.hidden = false;
}

function renderTopics(moods) {
  const grid = $("topics-grid");
  grid.innerHTML = "";
  const list = (moods || []).filter((m) => m.blockable);
  if (!list.length) {
    grid.innerHTML = "<p class='muted small'>Темы загрузятся после обновления.</p>";
    return;
  }
  for (const m of list) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `topic-chip${m.blocked ? " blocked" : ""}`;
    chip.dataset.code = m.code;
    chip.innerHTML = m.blocked
      ? `${escapeHtml(m.label)} <span class="x" title="Открыть">×</span>`
      : escapeHtml(m.label);
    chip.addEventListener("click", () => toggleTopic(m));
    grid.appendChild(chip);
  }
}

async function toggleTopic(mood) {
  const next = !mood.blocked;
  const ok = next
    ? confirm(`Больше не спрашивать про «${mood.label}»?`)
    : confirm(`Снова открыть тему «${mood.label}»?`);
  if (!ok) return;
  try {
    await apiFetch("/api/topics/block", {
      method: "POST",
      body: { code: mood.code, blocked: next },
    });
    toast(next ? "Тема закрыта" : "Тема открыта");
    await loadMoods();
  } catch (err) {
    toast(String(err.message || err));
  }
}

function renderMoodSheet(moods) {
  const grid = $("mood-grid");
  grid.innerHTML = "";
  $("mood-error").hidden = true;
  for (const m of moods || []) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mood-btn";
    btn.textContent = m.label;
    btn.disabled = Boolean(m.blocked);
    btn.addEventListener("click", () => startQuiz(m.code));
    grid.appendChild(btn);
  }
}

function openMoodSheet() {
  renderMoodSheet(state.moods);
  $("mood-sheet").hidden = false;
}

function closeMoodSheet() {
  $("mood-sheet").hidden = true;
}

async function startQuiz(moodCode) {
  if (state.starting) return;
  state.starting = true;
  const err = $("mood-error");
  err.hidden = true;
  try {
    const res = await apiFetch("/api/quiz/start", {
      method: "POST",
      body: { mood_code: moodCode },
    });
    toast(res.message || "Квиз ушёл в чат");
    closeMoodSheet();
    // Автозакрытие Mini App → пользователь видит вопрос в чате
    setTimeout(() => {
      try {
        getTg()?.close?.();
      } catch (_) {
        /* ignore */
      }
    }, 450);
  } catch (e) {
    err.hidden = false;
    err.textContent = String(e.message || e);
  } finally {
    state.starting = false;
  }
}

async function openQuizDetail(quizId) {
  const sheet = $("quiz-detail");
  const body = $("detail-body");
  body.innerHTML = "<div class='skeleton sk-card'></div>";
  sheet.hidden = false;
  try {
    const q = await apiFetch(`/api/quiz/${quizId}`);
    $("detail-title").textContent = q.topic_label || q.topic || `Квиз #${quizId}`;
    $("detail-date").textContent = fmtDate(q.date);
    $("detail-discuss").href = q.discuss_url || discussUrl(quizId);

    let html = "";
    if (q.analysis) {
      html += `<div class="card" style="margin:0 0 12px"><h2>Разбор Люма</h2><p class="summary">${escapeHtml(q.analysis)}</p></div>`;
    }
    for (const item of q.questions || []) {
      const answers = (item.answers || [])
        .map((a) => {
          const val = a.selected_option || a.text || "—";
          const skip = a.skipped ? " (пропуск)" : "";
          return `<div class="a">${escapeHtml(a.name || "—")}: «${escapeHtml(val)}»${skip}</div>`;
        })
        .join("");
      html += `<div class="detail-q"><div class="q">${escapeHtml(item.text || "")}</div>${answers || "<div class='a muted'>Ответов пока нет</div>"}</div>`;
    }
    body.innerHTML = html || "<p class='muted'>Пока нет деталей.</p>";
  } catch (e) {
    body.innerHTML = `<p class="error">${escapeHtml(e.message || e)}</p>`;
  }
}

function closeDetail() {
  $("quiz-detail").hidden = true;
}

async function loadMoods() {
  state.moods = (await apiFetch("/api/moods")).moods || [];
  renderTopics(state.moods);
}

async function refreshAll() {
  $("pull-indicator").hidden = false;
  try {
    const [active, history, profile] = await Promise.all([
      apiFetch("/api/quiz/active"),
      apiFetch("/api/history?limit=20"),
      apiFetch("/api/profile"),
    ]);
    state.active = active;
    state.history = history.quizzes || [];
    // enrich labels from moods if needed
    state.profile = profile;

    const you = profile.name || state.auth?.name || "Вы";
    const partner = profile.partner_name || profile.partner?.name || "";
    const chip = $("pair-chip");
    chip.hidden = false;
    chip.textContent = partner ? `${you} × ${partner}` : you;
    $("greeting").textContent = `Привет, ${you}!`;
    $("home-sub").textContent = `${state.auth?.assistant || "Люм"} рядом`;

    renderHomeCta(active);
    renderLastQuiz(state.history);
    renderHistory(state.history);
    renderPortrait();
    await loadMoods();
    setDiag([`Квизов в истории: ${state.history.length}`, `Статус: ${active?.status_text || "—"}`]);
  } finally {
    $("pull-indicator").hidden = true;
  }
}

function bindUi() {
  document.querySelectorAll(".tabbar .tab").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });

  $("cta-start")?.addEventListener("click", openMoodSheet);
  $("cta-start-empty")?.addEventListener("click", openMoodSheet);
  $("cta-retry")?.addEventListener("click", () => bootSession());

  document.querySelectorAll("[data-go-home]").forEach((b) =>
    b.addEventListener("click", () => {
      setTab("home");
      openMoodSheet();
    })
  );

  document.querySelectorAll("[data-close-mood]").forEach((el) =>
    el.addEventListener("click", closeMoodSheet)
  );
  document.querySelectorAll("[data-close-detail]").forEach((el) =>
    el.addEventListener("click", closeDetail)
  );

  $("last-quiz-open")?.addEventListener("click", () => {
    const id = $("last-quiz-card")?.dataset.quizId;
    if (id) openQuizDetail(id);
  });

  document.querySelectorAll("#portrait-segment .seg").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.portraitWho = btn.dataset.who;
      document.querySelectorAll("#portrait-segment .seg").forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
      renderPortrait();
    });
  });

  // Pull-to-refresh (простой жест сверху)
  let startY = 0;
  let pulling = false;
  window.addEventListener(
    "touchstart",
    (e) => {
      if (window.scrollY <= 0) {
        startY = e.touches[0].clientY;
        pulling = true;
      }
    },
    { passive: true }
  );
  window.addEventListener(
    "touchend",
    async (e) => {
      if (!pulling) return;
      pulling = false;
      const dy = e.changedTouches[0].clientY - startY;
      if (dy > 70) {
        try {
          await refreshAll();
          toast("Обновлено");
        } catch (err) {
          toast(String(err.message || err));
        }
      }
    },
    { passive: true }
  );
}

async function main() {
  bindUi();
  setTab("home");
  // Пока грузимся — пустые состояния не оставляем «чёрным экраном»
  $("history-empty").hidden = false;
  await bootSession();
}

main();
