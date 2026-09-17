/**
 * Mini App «Шёпот» — пульт пары.
 * Если открыто с Cloudflare Tunnel — API на том же origin (initData не теряется).
 * Иначе — явный tunnel URL (обновляется HOST_FIX_MINIAPP.sh) / GitHub Pages fallback.
 */
(function () {
  const host = String(location.hostname || "");
  if (/\.trycloudflare\.com$/i.test(host)) {
    window.SHEPOT_API_BASE = window.SHEPOT_API_BASE || location.origin;
  } else {
    window.SHEPOT_API_BASE =
      window.SHEPOT_API_BASE || "https://app.wspr.online";
  }
})();
const API_BASE = String(window.SHEPOT_API_BASE || "").replace(/\/$/, "");
const BOT_URL_FALLBACK = "https://t.me/Familia_Quiz_bot";

const $ = (id) => document.getElementById(id);
const getTg = () => window.Telegram?.WebApp || null;

function botUsername() {
  const raw =
    state.profile?.bot_username ||
    state.auth?.bot_username ||
    "Familia_Quiz_bot";
  return String(raw)
    .trim()
    .replace(/^@+/, "")
    .replace(/\s+/g, "");
}

function botDeepLink(startPayload) {
  const user = botUsername();
  if (!user) return "";
  const base = `https://t.me/${user}`;
  return startPayload ? `${base}?start=${encodeURIComponent(startPayload)}` : base;
}

const state = {
  tab: "home",
  auth: null,
  profile: null,
  active: null,
  history: [],
  historyLoaded: false,
  moods: [],
  lastQuiz: null,
  streak: null,
  weMode: "portraits",
  dynamicsLoaded: false,
  trends: null,
  topicHeat: null,
  tempChart: null,
  chartJsPromise: null,
  digestLatest: null,
  digestHistory: [],
  digestLoaded: false,
  ideas: { proposed: [], saved: [], done: [] },
  ideasLoaded: false,
  ideasBusy: false,
  ideaSheetId: null,
  ideaActionId: null,
  ideasDoneOpen: false,
  portraitWho: "me",
  notes: [],
  notesLoaded: false,
  notesShowAll: false,
  noteSheetMode: null, // create | view | edit
  noteSheetId: null,
  noteDraftQuizId: null,
  noteBusy: false,
  hiddenQuestions: [],
  hiddenQuestionsLoaded: false,
  hiddenQBusy: false,
  notifications: {
    preferred_hour: null,
    reactivation_enabled: true,
    analysis_notify_enabled: true,
  },
  notifBusy: false,
  starting: false,
  initData: "",
  authToken: "",
  detailOpen: false,
  detailQuizId: null,
  discussion: { messages: [], is_active: false, busy: false },
  onboarding: null,
  onboardingOpen: false,
  onboardingBusy: false,
  onboardingHistory: [],
  onboardingPendingSubmit: false,
};

const INSIGHT_TITLES = [
  "💡 Наши инсайты",
  "🌱 Взгляд со стороны",
  "❤️ Сердечный разговор",
];

function openBotLink(url) {
  const tg = window.Telegram?.WebApp;
  let link = String(url || botDeepLink() || "").trim();

  console.log("[Shepot] openBotLink called", {
    url,
    link,
    botUsername: botUsername(),
    hasOpenTelegramLink: !!tg?.openTelegramLink,
    hasOpenLink: !!tg?.openLink,
    platform: tg?.platform,
  });

  if (!link) {
    console.error("[Shepot] empty bot link");
    return;
  }

  // Проверка формата
  if (!/^https:\/\/t\.me\//i.test(link)) {
    console.error("[Shepot] invalid bot link format:", link);
    // Пробуем исправить: добавить https://t.me/ если это просто username
    if (/^[a-zA-Z0-9_]+$/.test(link)) {
      link = "https://t.me/" + link;
      console.log("[Shepot] fixed bot link:", link);
    } else {
      return;
    }
  }

  // Попытка 1: openTelegramLink (нативный)
  try {
    if (tg?.openTelegramLink) {
      console.log("[Shepot] calling openTelegramLink with:", link);
      tg.openTelegramLink(link);
      // На iOS иногда Mini App не закрывается сам — закрываем через 500мс
      setTimeout(() => {
        try {
          if (tg?.close) {
            console.log("[Shepot] closing Mini App after openTelegramLink");
            tg.close();
          }
        } catch (err) {
          console.error("[Shepot] tg.close error", err);
        }
      }, 500);
      return;
    }
  } catch (err) {
    console.error("[Shepot] openTelegramLink error", err);
  }

  // Попытка 2: openLink (iOS часто перехватывает t.me)
  try {
    if (tg?.openLink) {
      console.log("[Shepot] calling openLink with:", link);
      tg.openLink(link);
      return;
    }
  } catch (err) {
    console.error("[Shepot] openLink error", err);
  }

  // Попытка 3: window.open
  try {
    console.log("[Shepot] fallback window.open", link);
    window.open(link, "_blank", "noopener");
  } catch (err) {
    console.error("[Shepot] window.open error", err);
  }
}

function bindBotAnchor(el, startPayload) {
  if (!el) return;
  el.setAttribute("href", "#");
  el.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openBotLink(botDeepLink(startPayload));
  });
}

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
  const token = options.token ?? state.authToken ?? "";
  if (token) headers["X-Shepot-Auth-Token"] = token;

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
    if (res.status === 401) {
      const err = new Error("Сессия истекла. Открой Mini App из бота снова.");
      err.code = 401;
      throw err;
    }
    const detail = data.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || data.message || `HTTP ${res.status}`;
    console.error("[Shepot] api", res.status, data);
    const err = new Error(msg);
    err.code = res.status;
    throw err;
  }
  return data;
}

function setTab(tab) {
  if (state.detailOpen) closeDetailView(false);
  state.tab = tab;
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.hidden = p.dataset.tab !== tab;
  });
  document.querySelectorAll(".tabbar .tab").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
  // На табах нативная BackButton не нужна
  hideTelegramBackButton();
  if (tab !== "more") {
    closeIdeaSheet();
    closeIdeaActionSheet();
  }
  if (tab === "home") {
    if (state.authToken || state.initData) refreshHome();
  } else if (tab === "history") {
    renderHistory(state.history);
    if (!state.historyLoaded) refreshHistory();
  } else if (tab === "we") {
    applyWeMode(state.weMode);
    if (state.weMode === "dynamics") refreshDynamics();
    if (state.weMode === "notes") refreshNotes();
    if (state.weMode === "hidden") refreshHiddenQuestions();
  } else if (tab === "more") {
    refreshDigest();
    refreshIdeas();
    renderNotifications();
  }
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

function fmtDateTimeRu(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const day = d.getDate();
    const mon = UTC_MONTHS_GENITIVE[d.getMonth()] || "";
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${day} ${mon}, ${hh}:${mm}`;
  } catch {
    return "—";
  }
}

function fmtDateShort(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
    });
  } catch {
    return fmtDate(iso);
  }
}

function discussUrl(quizId) {
  return botDeepLink(`discuss_${quizId}`);
}

function answeredVerb(name, gender) {
  const g = String(gender || "").toLowerCase();
  if (g === "female") return "ответила";
  if (g === "male") return "ответил";
  const n = String(name || "").trim().toLowerCase();
  if (n === "юля" || n === "юлия" || /[ая]$/i.test(n)) return "ответила";
  if (n === "рома" || n === "роман") return "ответил";
  return "ответил(а)";
}

function partnerName() {
  return (
    state.profile?.partner_name ||
    state.profile?.partner?.name ||
    "партнёр"
  );
}

function youName() {
  return state.profile?.name || state.auth?.name || "Вы";
}

/* ---------- Home ---------- */
function renderHomeHero() {
  const you = youName();
  const partner = partnerName();
  $("greeting").textContent = `Привет, ${you}`;
  const pair = $("home-pair");
  const pairText = $("home-pair-text");
  if (pair && pairText) {
    if (partner && partner !== "партнёр" && partner !== "партнёра") {
      pair.hidden = false;
      pairText.textContent = `${you} × ${partner}`;
    } else {
      pair.hidden = true;
    }
  }
  const chip = $("pair-chip");
  if (chip) {
    chip.hidden = !(partner && partner !== "партнёр" && partner !== "партнёра");
    chip.textContent =
      partner && partner !== "партнёр" && partner !== "партнёра"
        ? `${you} × ${partner}`
        : you;
  }
  renderStreakBadge(state.streak);
}

function streakLabel(streak) {
  const n = Number(streak?.current_streak || 0);
  const best = Number(streak?.longest_streak || 0);
  if (n <= 0) return "";
  if (n === 1) return "🌱 Первый день общения";
  if (n >= 7) return `🔥 ${n} дней подряд · рекорд: ${best || n}`;
  return `🔥 ${n} дней подряд`;
}

function renderStreakBadge(streak) {
  const el = $("streak-badge");
  if (!el) return;
  const text = streakLabel(streak);
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
}

function renderHomeCta(active) {
  const start = $("cta-start");
  const answer = $("cta-answer");
  const waiting = $("cta-waiting");
  const analyzing = $("cta-analyzing");
  const status = $("home-status");
  const sub = $("home-status-sub");
  const body = $("home-status-body");
  const sk = document.querySelector("#home-status-card [data-sk]");
  const retry = $("cta-retry");

  if (sk) sk.hidden = true;
  if (retry) retry.hidden = true;
  if (body) body.hidden = false;
  status.hidden = false;
  start.hidden = true;
  answer.hidden = true;
  waiting.hidden = true;
  if (analyzing) analyzing.hidden = true;
  if (sub) {
    sub.hidden = true;
    sub.textContent = "";
  }

  const partner = partnerName();
  const you = youName();

  if (!active?.has_active) {
    status.textContent = "Можно начать новый разговор";
    start.hidden = false;
    return;
  }

  if (active.analysis_pending || (active.you_answered && active.partner_answered)) {
    status.textContent = "Люм готовит разбор...";
    if (analyzing) analyzing.hidden = false;
    return;
  }

  if (!active.you_answered) {
    status.textContent = "Есть открытый квиз — ответь в чате";
    if (sub) {
      sub.hidden = false;
      // Имя в именительном; «увидит» без рода
      sub.textContent = `Люм уже задал вопросы. Ответь — тогда ${partner} увидит твои ответы.`;
    }
    answer.hidden = false;
    return;
  }

  // you answered, partner not — имя только в именительном
  status.textContent = `Ждём, пока ${partner} ответит`;
  if (sub) {
    sub.hidden = false;
    sub.textContent = `Ты ${answeredVerb(you, state.profile?.gender)}. Как только ${partner} ответит, Люм сравнит ваши ответы.`;
  }
  waiting.hidden = false;
  $("cta-waiting-text").textContent = `Ждём, пока ${partner} ответит`;
}

function renderLastQuiz(quizzes) {
  const card = $("last-quiz-card");
  if (!card) return;
  const list = quizzes || [];
  const done = list.filter((q) =>
    ["analyzed", "closed", "exchanging", "analysis_pending", "collecting"].includes(q.status)
  );
  // предпочитаем разобранный; иначе первый из истории
  const q =
    done.find((x) => x.status === "analyzed" || (x.analysis || "").trim()) ||
    done[0] ||
    list[0];

  if (!q) {
    card.dataset.quizId = "";
    card.classList.add("is-placeholder");
    $("last-quiz-topic").textContent = "Первый разговор впереди";
    $("last-quiz-date").textContent = "";
    $("last-quiz-analysis").textContent =
      "Начните с кнопки выше — Люм пришлёт вопросы в чат.";
    return;
  }

  card.classList.remove("is-placeholder");
  card.dataset.quizId = String(q.quiz_id);
  $("last-quiz-topic").textContent = q.topic_label || q.topic || "Квиз";
  $("last-quiz-date").textContent = fmtDateShort(q.date);
  const preview = previewAnalysis(q.analysis, 160);
  $("last-quiz-analysis").textContent =
    preview || "Разбор Люма появится здесь после ответов.";
}

async function refreshActive() {
  const sk = document.querySelector("#home-status-card [data-sk]");
  const body = $("home-status-body");
  if (sk) sk.hidden = false;
  if (body) body.hidden = true;
  try {
    state.active = await apiFetch("/api/quiz/active");
    if (state.active?.streak) {
      state.streak = state.active.streak;
      renderStreakBadge(state.streak);
    }
    renderHomeCta(state.active);
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  } finally {
    if (sk) sk.hidden = true;
  }
}

async function refreshLastQuiz() {
  try {
    const history = await apiFetch("/api/history?limit=1");
    const quizzes = history.quizzes || [];
    state.lastQuiz = quizzes[0] || null;
    renderLastQuiz(quizzes);
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  }
}

async function refreshHome() {
  renderHomeHero();
  await Promise.all([refreshActive(), refreshLastQuiz(), refreshDigest({ bannerOnly: true })]);
}

const STATUS_LABELS = {
  collecting: "Ждём ответа",
  exchanging: "Ждём ответа",
  analysis_pending: "Люм думает",
  closed: "Завершён",
  analyzed: "Разобран",
};

function statusBadgeMeta(status) {
  if (status === "analyzed") return { cls: "badge-ok", text: "Разобран" };
  if (status === "analysis_pending") return { cls: "badge-pending", text: "Люм думает" };
  if (status === "collecting" || status === "exchanging") {
    return { cls: "badge-wait", text: "Ждём ответа" };
  }
  if (status === "closed") return { cls: "badge-ok", text: "Завершён" };
  return { cls: "badge-wait", text: STATUS_LABELS[status] || status || "" };
}

function statusLabel(status) {
  if (!status) return "";
  return STATUS_LABELS[status] || status;
}

function previewAnalysis(text, maxLen = 110) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  if (!t) return "";
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen).trim()}…`;
}

function setHistorySkeleton(on) {
  const sk = $("history-skeleton");
  const list = $("history-list");
  if (sk) sk.hidden = !on;
  if (list && on) list.innerHTML = "";
}

function renderHistory(quizzes) {
  const list = $("history-list");
  const empty = $("history-empty");
  if (!list || !empty) return;
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
    const badge = statusBadgeMeta(q.status);
    const preview = previewAnalysis(q.analysis);
    li.innerHTML = `
      <div class="hist-top">
        <div class="topic">${escapeHtml(label)}</div>
        <span class="status-badge ${badge.cls}">${escapeHtml(badge.text)}</span>
      </div>
      <div class="date">${escapeHtml(fmtDateShort(q.date))}</div>
      ${preview ? `<div class="analysis preview">${escapeHtml(preview)}</div>` : ""}
    `;
    li.addEventListener("click", () => openQuizDetail(q.quiz_id));
    list.appendChild(li);
  }
}

async function refreshHistory() {
  setHistorySkeleton(true);
  try {
    const history = await apiFetch("/api/history?limit=30");
    state.history = history.quizzes || [];
    state.historyLoaded = true;
    renderHistory(state.history);
    renderLastQuiz(state.history);
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  } finally {
    setHistorySkeleton(false);
  }
}

function isDebugMode() {
  try {
    return new URLSearchParams(location.search || "").get("debug") === "1";
  } catch (_) {
    return false;
  }
}

function setDiag(lines) {
  const el = $("diag");
  if (!el) return;
  if (!isDebugMode()) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  const text = (Array.isArray(lines) ? lines : [lines]).filter(Boolean).join("\n");
  el.hidden = !text;
  el.textContent = text;
}

function showBootError(title, text) {
  $("greeting").textContent = title;
  const pair = $("home-pair");
  if (pair) pair.hidden = true;
  const sk = document.querySelector("#home-status-card [data-sk]");
  if (sk) sk.hidden = true;
  const body = $("home-status-body");
  if (body) body.hidden = false;
  $("home-status").hidden = false;
  $("home-status").textContent = text;
  const sub = $("home-status-sub");
  if (sub) sub.hidden = true;
  $("cta-retry").hidden = false;
  $("cta-start").hidden = true;
  $("cta-answer").hidden = true;
  $("cta-waiting").hidden = true;
  const analyzing = $("cta-analyzing");
  if (analyzing) analyzing.hidden = true;
  $("history-empty").hidden = false;
  $("pair-chip").hidden = true;
}

function parseTgWebAppDataFromHash(hash) {
  const h = String(hash || "").replace(/^#/, "");
  const key = "tgWebAppData=";
  const i = h.indexOf(key);
  if (i < 0) return "";
  let rest = h.slice(i + key.length);
  const m = rest.match(/&tgWebApp[A-Za-z]+=/);
  if (m) rest = rest.slice(0, m.index);
  if (!rest) return "";
  try {
    return decodeURIComponent(rest.replace(/\+/g, " "));
  } catch (_) {
    return rest;
  }
}

function initDataFromTelegramStorage() {
  try {
    const raw = sessionStorage.getItem("__telegram__initParams");
    if (!raw) return "";
    const parsed = JSON.parse(raw);
    const data = parsed?.tgWebAppData;
    return data && String(data).length > 20 ? String(data) : "";
  } catch (_) {
    return "";
  }
}

function extractInitData() {
  const fromTg = getTg()?.initData;
  if (fromTg && String(fromTg).length > 20) return fromTg;

  try {
    const hash = window.location.hash || sessionStorage.getItem("shepot_launch_hash") || "";
    const fromHash = parseTgWebAppDataFromHash(hash);
    if (fromHash && fromHash.length > 20) {
      try {
        sessionStorage.setItem("shepot_tgWebAppData", fromHash);
      } catch (_) {
        /* ignore */
      }
      return fromHash;
    }
  } catch (_) {
    /* ignore */
  }

  try {
    const saved = sessionStorage.getItem("shepot_tgWebAppData");
    if (saved && saved.length > 20) return saved;
  } catch (_) {
    /* ignore */
  }

  const fromStore = initDataFromTelegramStorage();
  if (fromStore) return fromStore;

  return "";
}

async function waitForInitData(timeoutMs = 8000) {
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

function extractLaunchToken() {
  try {
    const q = new URLSearchParams(window.location.search || "");
    const t = q.get("t");
    if (t && t.length > 10) {
      sessionStorage.setItem("shepot_auth_t", t);
      return t;
    }
  } catch (_) {
    /* ignore */
  }
  try {
    const saved = sessionStorage.getItem("shepot_auth_t");
    if (saved && saved.length > 10) return saved;
  } catch (_) {
    /* ignore */
  }
  return "";
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

  const launchToken = extractLaunchToken();
  state.authToken = launchToken;

  setDiag([
    `host: ${location.host}`,
    `API: ${API_BASE}`,
    `token: ${launchToken ? "да" : "нет"}`,
    `platform: ${getTg()?.platform || "—"}`,
  ]);

  if (!getTg() && !launchToken) {
    showBootError(
      "Нет Telegram SDK",
      "Страница открыта вне WebApp. Нажмите «🌿 Открыть Шёпот» в чате с ботом."
    );
    return;
  }

  const initData = await waitForInitData(2500);
  state.initData = initData;

  if (!initData && !launchToken) {
    console.warn("[Shepot] empty initData and token", {
      host: location.host,
      hash: (location.hash || "").slice(0, 120),
    });
    showBootError(
      "Сессия не получена",
      "Закройте Mini App, в боте нажмите /menu (обновит кнопку) и снова «🌿 Открыть Шёпот»."
    );
    setDiag([
      `host: ${location.host}`,
      `API: ${API_BASE}`,
      `initData: ПУСТО`,
      `token: нет`,
      `hash: ${(location.hash || "нет").slice(0, 70)}`,
      `platform: ${getTg()?.platform || "—"}`,
    ]);
    return;
  }

  try {
    const health = await fetch(`${API_BASE}/api/health`, { method: "GET" })
      .then((r) => r.json())
      .catch((e) => ({ error: String(e.message || e) }));
    setDiag([
      `host: ${location.host}`,
      `API: ${API_BASE}`,
      `health: ${health.status || health.error || JSON.stringify(health)}`,
      `initData: ${initData ? initData.length + " симв." : "ПУСТО"}`,
      `token: ${launchToken ? "да" : "нет"}`,
    ]);

    const authBody = launchToken
      ? { token: launchToken, initData: initData || "" }
      : { initData };
    state.auth = await apiFetch("/api/auth", {
      method: "POST",
      body: authBody,
    });
    await refreshAll();
    await maybeStartOnboarding();
    $("cta-retry").hidden = true;
    setDiag([
      `host: ${location.host}`,
      `квизов: ${state.history.length}`,
      `вход: ${launchToken ? "token" : "initData"}`,
      `CTA: ${state.active?.cta || "—"}`,
    ]);
  } catch (err) {
    console.error("[Shepot]", err);
    showBootError("Не удалось загрузить данные", String(err.message || err));
    setDiag([
      `host: ${location.host}`,
      `API: ${API_BASE}`,
      `ошибка: ${String(err.message || err)}`,
    ]);
  }
}

function setWeSkeleton(on) {
  const skP = $("portrait-skeleton");
  const body = $("portrait-body");
  const skT = $("topics-skeleton");
  const grid = $("topics-grid");
  if (skP) skP.hidden = !on;
  if (body) body.hidden = !!on;
  if (skT) skT.hidden = !on;
  if (grid) grid.hidden = !!on;
}

function applyWeMode(mode) {
  const allowed = new Set(["portraits", "dynamics", "notes", "hidden"]);
  state.weMode = allowed.has(mode) ? mode : "portraits";
  const portraits = $("we-portraits");
  const dynamics = $("we-dynamics");
  const notes = $("we-notes");
  const hidden = $("we-hidden");
  if (portraits) portraits.hidden = state.weMode !== "portraits";
  if (dynamics) dynamics.hidden = state.weMode !== "dynamics";
  if (notes) notes.hidden = state.weMode !== "notes";
  if (hidden) hidden.hidden = state.weMode !== "hidden";
  document.querySelectorAll("#we-mode-segment .seg").forEach((b) => {
    b.classList.toggle("active", b.dataset.weMode === state.weMode);
  });
}

function loadChartJs() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (state.chartJsPromise) return state.chartJsPromise;
  state.chartJsPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/chart.js@4";
    s.async = true;
    s.onload = () => resolve(window.Chart);
    s.onerror = () => reject(new Error("Не удалось загрузить Chart.js"));
    document.head.appendChild(s);
  });
  return state.chartJsPromise;
}

const UTC_MONTHS_SHORT = [
  "янв",
  "фев",
  "мар",
  "апр",
  "мая",
  "июн",
  "июл",
  "авг",
  "сен",
  "окт",
  "ноя",
  "дек",
];

const UTC_MONTHS_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

function fmtUtcShort(isoDate) {
  // isoDate: YYYY-MM-DD — отображаем в UTC без локализации ОС
  const m = String(isoDate || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return isoDate || "";
  const day = String(Number(m[3]));
  const mon = UTC_MONTHS_SHORT[Number(m[2]) - 1] || m[2];
  return `${day} ${mon}`;
}

function round1(n) {
  return Math.round(Number(n) * 10) / 10;
}

function renderTrends(trends) {
  const avgEl = $("temp-avg");
  const trendEl = $("temp-trend");
  const deltaEl = $("temp-delta");
  const empty = $("temp-empty");
  const wrap = $("temp-chart-wrap");
  const points = trends?.points || [];

  const avg = round1(trends?.avg_temperature || 0);
  if (avgEl) avgEl.textContent = points.length ? `${avg.toFixed(1)}°` : "—°";

  const dir = trends?.trend_direction || "flat";
  const delta = round1(trends?.trend_delta || 0);
  if (trendEl) {
    trendEl.className = "temp-trend " + dir;
    trendEl.textContent = dir === "up" ? "↑" : dir === "down" ? "↓" : "→";
  }
  if (deltaEl) {
    if (!points.length) deltaEl.textContent = "";
    else {
      const sign = delta > 0 ? "+" : "";
      deltaEl.textContent = `${sign}${delta.toFixed(1)}°`;
    }
  }

  const showChart = points.length >= 2;
  if (empty) empty.hidden = showChart;
  if (wrap) wrap.hidden = !showChart;
  if (!showChart) {
    if (state.tempChart) {
      try {
        state.tempChart.destroy();
      } catch (_) {
        /* ignore */
      }
      state.tempChart = null;
    }
    return;
  }

  loadChartJs()
    .then((Chart) => {
      const canvas = $("temp-chart");
      if (!canvas || !Chart) return;
      const ctx = canvas.getContext("2d");
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height || 180);
      gradient.addColorStop(0, "rgba(140, 100, 255, 0.4)");
      gradient.addColorStop(1, "rgba(80, 220, 160, 0.05)");

      const labels = points.map((p) => fmtUtcShort(p.date));
      const data = points.map((p) => Number(p.temperature));

      if (state.tempChart) {
        try {
          state.tempChart.destroy();
        } catch (_) {
          /* ignore */
        }
      }

      state.tempChart = new Chart(ctx, {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              data,
              borderColor: "rgba(140, 100, 255, 0.9)",
              backgroundColor: gradient,
              fill: true,
              tension: 0.35,
              pointRadius: 4,
              pointHoverRadius: 6,
              pointBackgroundColor: "rgba(140, 100, 255, 1)",
              borderWidth: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title: (items) => {
                  const i = items[0]?.dataIndex ?? 0;
                  return points[i]?.date || "";
                },
                label: (item) => `Температура: ${item.formattedValue}°`,
              },
            },
          },
          scales: {
            y: {
              min: 0,
              max: 10,
              ticks: { stepSize: 2, color: "#9aa3b5" },
              grid: { color: "rgba(155, 126, 217, 0.12)" },
            },
            x: {
              ticks: { color: "#9aa3b5", maxRotation: 0, autoSkip: true, maxTicksLimit: 6 },
              grid: { display: false },
            },
          },
        },
      });
    })
    .catch((err) => toast(String(err.message || err)));
}

function matchBarClass(rate) {
  const pct = Number(rate) * 100;
  if (pct < 40) return "heat-low";
  if (pct <= 70) return "heat-mid";
  return "heat-high";
}

function renderTopicHeat(topics) {
  const root = $("topic-heat");
  const empty = $("topic-heat-empty");
  if (!root) return;
  root.innerHTML = "";
  const list = topics || [];
  if (!list.length) {
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  for (const t of list) {
    const rate = Number(t.avg_match_rate || 0);
    const pct = Math.round(rate * 100);
    const card = document.createElement("div");
    card.className = "heat-card";
    card.innerHTML = `
      <div class="heat-top">
        <span class="heat-label">${escapeHtml(t.label || t.code || "Тема")}</span>
        <span class="heat-count muted">${Number(t.quiz_count || 0)} квиз.</span>
      </div>
      <div class="heat-bar"><div class="heat-fill ${matchBarClass(rate)}" style="width:${pct}%"></div></div>
      <p class="heat-meta muted">Совпадение ${pct}%</p>
    `;
    root.appendChild(card);
  }
}

async function refreshDynamics() {
  if (!(state.authToken || state.initData)) return;
  try {
    const [trends, topicsRes] = await Promise.all([
      apiFetch("/api/insights/trends?period=30"),
      apiFetch("/api/insights/topics"),
    ]);
    state.trends = trends;
    state.topicHeat = topicsRes.topics || [];
    state.dynamicsLoaded = true;
    renderTrends(trends);
    renderTopicHeat(state.topicHeat);
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  }
}

/* ---------- Private notes ---------- */

function clipNotePreview(text, maxLines = 4) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const lines = raw.split(/\n/);
  if (lines.length > maxLines) {
    return lines.slice(0, maxLines).join("\n") + "…";
  }
  if (raw.length > 220) return raw.slice(0, 220).trimEnd() + "…";
  return raw;
}

function lastFinishedQuiz() {
  const list = state.history || [];
  return list.find((q) => q && q.quiz_id) || null;
}

function renderNotes() {
  const listEl = $("notes-list");
  const empty = $("notes-empty");
  const showAllBtn = $("notes-show-all");
  if (!listEl) return;
  const notes = state.notes || [];
  listEl.innerHTML = "";

  if (!notes.length) {
    if (empty) empty.hidden = false;
    if (showAllBtn) showAllBtn.hidden = true;
    return;
  }
  if (empty) empty.hidden = true;

  const limit = state.notesShowAll ? notes.length : 10;
  const visible = notes.slice(0, limit);
  if (showAllBtn) {
    showAllBtn.hidden = notes.length <= 10 || state.notesShowAll;
  }

  for (const note of visible) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "note-card";
    card.dataset.noteId = String(note.id);
    const badge =
      note.quiz_id && note.quiz_topic
        ? `<span class="note-quiz-badge">🌿 ${escapeHtml(note.quiz_topic)}${
            note.quiz_date ? ` · ${escapeHtml(fmtDateShort(note.quiz_date))}` : ""
          }</span>`
        : "";
    card.innerHTML = `
      <span class="note-card-date">${escapeHtml(fmtDateTimeRu(note.created_at))}</span>
      <span class="note-card-text">${escapeHtml(clipNotePreview(note.text))}</span>
      ${badge}
    `;
    card.addEventListener("click", () => openNoteView(note.id));
    listEl.appendChild(card);
  }
}

async function refreshNotes(force) {
  if (!(state.authToken || state.initData)) return;
  if (state.notesLoaded && !force) {
    renderNotes();
    return;
  }
  try {
    const res = await apiFetch("/api/notes?limit=100");
    state.notes = res.notes || [];
    state.notesLoaded = true;
    renderNotes();
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  }
}

function openSheetEl(id) {
  const sheet = $(id);
  if (!sheet) return;
  sheet.hidden = false;
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closeSheetEl(id) {
  const sheet = $(id);
  if (!sheet) return;
  sheet.classList.remove("is-open");
  setTimeout(() => {
    if (!sheet.classList.contains("is-open")) sheet.hidden = true;
  }, 220);
}

function autoExpandTextarea(el) {
  if (!el) return;
  el.style.height = "auto";
  const next = Math.min(Math.max(el.scrollHeight, 120), 300);
  el.style.height = `${next}px`;
  el.style.overflowY = el.scrollHeight > 300 ? "auto" : "hidden";
}

function syncNoteSaveEnabled() {
  const btn = $("note-save-btn");
  const ta = $("note-textarea");
  if (!btn || !ta) return;
  btn.disabled = String(ta.value || "").trim().length < 3 || state.noteBusy;
}

function setNoteFormMode(mode) {
  const view = $("note-view-mode");
  const edit = $("note-edit-mode");
  if (view) view.hidden = mode !== "view";
  if (edit) edit.hidden = mode === "view";
}

function openNoteCreate(opts = {}) {
  const quizId = opts.quizId != null ? Number(opts.quizId) : null;
  const linkChecked = !!opts.linkChecked;
  state.noteSheetMode = "create";
  state.noteSheetId = null;
  state.noteDraftQuizId = quizId;
  setNoteFormMode("edit");
  const title = $("note-form-title");
  if (title) title.textContent = "Новая заметка";
  const ta = $("note-textarea");
  if (ta) {
    ta.value = "";
    autoExpandTextarea(ta);
  }
  const wrap = $("note-link-quiz-wrap");
  const cb = $("note-link-quiz");
  if (wrap) wrap.hidden = false;
  if (cb) {
    if (quizId) {
      cb.checked = true;
      state.noteDraftQuizId = quizId;
    } else if (linkChecked) {
      const last = lastFinishedQuiz();
      cb.checked = !!last;
      state.noteDraftQuizId = last?.quiz_id || null;
    } else {
      cb.checked = false;
      state.noteDraftQuizId = null;
    }
  }
  syncNoteSaveEnabled();
  openSheetEl("note-sheet");
}

function openNoteView(noteId) {
  const note = (state.notes || []).find((n) => Number(n.id) === Number(noteId));
  if (!note) return;
  state.noteSheetMode = "view";
  state.noteSheetId = note.id;
  setNoteFormMode("view");
  const dateEl = $("note-sheet-date");
  const textEl = $("note-sheet-text");
  const badge = $("note-sheet-badge");
  if (dateEl) dateEl.textContent = fmtDateTimeRu(note.created_at);
  if (textEl) textEl.textContent = note.text || "";
  if (badge) {
    if (note.quiz_id && note.quiz_topic) {
      badge.hidden = false;
      badge.textContent = `🌿 ${note.quiz_topic}${
        note.quiz_date ? ` · ${fmtDateShort(note.quiz_date)}` : ""
      }`;
    } else {
      badge.hidden = true;
      badge.textContent = "";
    }
  }
  openSheetEl("note-sheet");
}

function openNoteEditFromView() {
  const note = (state.notes || []).find((n) => Number(n.id) === Number(state.noteSheetId));
  if (!note) return;
  state.noteSheetMode = "edit";
  setNoteFormMode("edit");
  const title = $("note-form-title");
  if (title) title.textContent = "Редактировать";
  const ta = $("note-textarea");
  if (ta) {
    ta.value = note.text || "";
    autoExpandTextarea(ta);
  }
  const wrap = $("note-link-quiz-wrap");
  if (wrap) wrap.hidden = true;
  syncNoteSaveEnabled();
}

function closeNoteSheet() {
  closeSheetEl("note-sheet");
  state.noteSheetMode = null;
  state.noteSheetId = null;
  state.noteDraftQuizId = null;
}

function openNoteDeleteConfirm() {
  openSheetEl("note-delete-confirm");
}

function closeNoteDeleteConfirm() {
  closeSheetEl("note-delete-confirm");
}

async function saveNoteFromSheet() {
  if (state.noteBusy) return;
  const ta = $("note-textarea");
  const text = String(ta?.value || "").trim();
  if (text.length < 3) {
    toast("Напиши хотя бы несколько слов");
    return;
  }
  if (text.length > 2000) {
    toast("Слишком длинная заметка");
    return;
  }

  const isEdit = state.noteSheetMode === "edit" && state.noteSheetId;
  state.noteBusy = true;
  syncNoteSaveEnabled();

  let quizId = null;
  if (!isEdit) {
    const cb = $("note-link-quiz");
    if (cb?.checked) {
      quizId =
        state.noteDraftQuizId ||
        lastFinishedQuiz()?.quiz_id ||
        null;
    }
  }

  const snapshot = (state.notes || []).map((n) => ({ ...n }));
  let tempId = null;

  try {
    if (isEdit) {
      const id = state.noteSheetId;
      const idx = state.notes.findIndex((n) => Number(n.id) === Number(id));
      if (idx >= 0) {
        state.notes[idx] = {
          ...state.notes[idx],
          text,
          updated_at: new Date().toISOString(),
        };
        renderNotes();
      }
      const updated = await apiFetch(`/api/notes/${id}`, {
        method: "PUT",
        body: { text },
      });
      if (idx >= 0) state.notes[idx] = updated;
      else state.notes.unshift(updated);
      renderNotes();
      toast("Заметка обновлена");
    } else {
      tempId = `tmp_${Date.now()}`;
      const optimistic = {
        id: tempId,
        text,
        quiz_id: quizId,
        quiz_topic: null,
        quiz_date: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      if (quizId) {
        const q = (state.history || []).find((x) => Number(x.quiz_id) === Number(quizId));
        if (q) {
          optimistic.quiz_topic = q.topic_label || q.topic || null;
          optimistic.quiz_date = q.date || null;
        }
      }
      state.notes = [optimistic, ...(state.notes || [])];
      renderNotes();
      const created = await apiFetch("/api/notes", {
        method: "POST",
        body: { text, quiz_id: quizId },
      });
      state.notes = [
        created,
        ...(state.notes || []).filter((n) => n.id !== tempId),
      ];
      renderNotes();
      toast("Заметка сохранена");
    }
    closeNoteSheet();
  } catch (err) {
    state.notes = snapshot;
    renderNotes();
    toast(String(err.message || err));
  } finally {
    state.noteBusy = false;
    syncNoteSaveEnabled();
  }
}

async function deleteNoteConfirmed() {
  const id = state.noteSheetId;
  if (!id || state.noteBusy) return;
  state.noteBusy = true;
  const snapshot = (state.notes || []).map((n) => ({ ...n }));
  state.notes = (state.notes || []).filter((n) => Number(n.id) !== Number(id));
  renderNotes();
  closeNoteDeleteConfirm();
  closeNoteSheet();
  try {
    await apiFetch(`/api/notes/${id}`, { method: "DELETE" });
    toast("Заметка удалена");
  } catch (err) {
    state.notes = snapshot;
    renderNotes();
    toast(String(err.message || err));
  } finally {
    state.noteBusy = false;
  }
}

/* ---------- Hidden questions ---------- */

function renderHiddenQuestions() {
  const listEl = $("hidden-q-list");
  const empty = $("hidden-q-empty");
  const newBtn = $("hidden-q-new-btn");
  if (!listEl) return;
  listEl.innerHTML = "";
  const items = state.hiddenQuestions || [];
  if (!items.length) {
    if (empty) empty.hidden = false;
    if (newBtn) newBtn.hidden = true;
    return;
  }
  if (empty) empty.hidden = true;
  if (newBtn) newBtn.hidden = false;

  const pending = items.filter((q) => q.status === "pending");
  const used = items.filter((q) => q.status === "used");

  const appendGroup = (title, rows, usedMode) => {
    if (!rows.length) return;
    const h = document.createElement("p");
    h.className = "hidden-q-group";
    h.textContent = title;
    listEl.appendChild(h);
    for (const q of rows) {
      const card = document.createElement("div");
      card.className = "hidden-q-card" + (usedMode ? " is-used" : "");
      const meta = usedMode
        ? `<span class="hidden-q-badge">✓ Уже в квизе от ${escapeHtml(
            fmtDateShort(q.used_at || q.created_at)
          )}</span>`
        : `<span class="hidden-q-date">${escapeHtml(fmtDateTimeRu(q.created_at))}</span>`;
      card.innerHTML = `
        <div class="hidden-q-card-body">
          <p class="hidden-q-text">${escapeHtml(q.text || "")}</p>
          ${meta}
        </div>
        ${
          usedMode
            ? ""
            : `<button type="button" class="hidden-q-del" data-hq-id="${q.id}" aria-label="Удалить">✕</button>`
        }
      `;
      listEl.appendChild(card);
    }
  };

  appendGroup("Активные", pending, false);
  appendGroup("Уже в квизах", used, true);

  listEl.querySelectorAll("[data-hq-id]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteHiddenQuestion(Number(btn.dataset.hqId));
    });
  });
}

async function refreshHiddenQuestions(force) {
  if (!(state.authToken || state.initData)) return;
  if (state.hiddenQuestionsLoaded && !force) {
    renderHiddenQuestions();
    return;
  }
  try {
    const res = await apiFetch("/api/hidden-questions");
    state.hiddenQuestions = res.questions || [];
    state.hiddenQuestionsLoaded = true;
    renderHiddenQuestions();
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  }
}

function syncHiddenQSaveEnabled() {
  const btn = $("hidden-q-save-btn");
  const ta = $("hidden-q-textarea");
  if (!btn || !ta) return;
  const n = String(ta.value || "").trim().length;
  btn.disabled = n < 5 || n > 200 || state.hiddenQBusy;
}

function openHiddenQuestionCreate() {
  const ta = $("hidden-q-textarea");
  if (ta) {
    ta.value = "";
    autoExpandTextarea(ta);
  }
  syncHiddenQSaveEnabled();
  openSheetEl("hidden-q-sheet");
}

function closeHiddenQuestionSheet() {
  closeSheetEl("hidden-q-sheet");
}

async function saveHiddenQuestion() {
  if (state.hiddenQBusy) return;
  const ta = $("hidden-q-textarea");
  const text = String(ta?.value || "").trim();
  if (text.length < 5) {
    toast("Напиши чуть подробнее (от 5 символов)");
    return;
  }
  if (text.length > 200) {
    toast("Максимум 200 символов");
    return;
  }
  const pendingCount = (state.hiddenQuestions || []).filter((q) => q.status === "pending").length;
  if (pendingCount >= 5) {
    toast("У вас уже 5 активных вопросов. Дождитесь, пока Люм задаст один из них.");
    return;
  }

  state.hiddenQBusy = true;
  syncHiddenQSaveEnabled();
  const snapshot = (state.hiddenQuestions || []).map((q) => ({ ...q }));
  const tempId = `tmp_hq_${Date.now()}`;
  const optimistic = {
    id: tempId,
    text,
    status: "pending",
    created_at: new Date().toISOString(),
    used_at: null,
  };
  state.hiddenQuestions = [optimistic, ...(state.hiddenQuestions || [])];
  renderHiddenQuestions();
  try {
    const created = await apiFetch("/api/hidden-questions", {
      method: "POST",
      body: { text },
    });
    state.hiddenQuestions = [
      created,
      ...(state.hiddenQuestions || []).filter((q) => q.id !== tempId),
    ];
    renderHiddenQuestions();
    closeHiddenQuestionSheet();
    toast("Скрытый вопрос сохранён 🤫");
  } catch (err) {
    state.hiddenQuestions = snapshot;
    renderHiddenQuestions();
    toast(String(err.message || err));
  } finally {
    state.hiddenQBusy = false;
    syncHiddenQSaveEnabled();
  }
}

async function deleteHiddenQuestion(id) {
  if (!id || state.hiddenQBusy) return;
  state.hiddenQBusy = true;
  const snapshot = (state.hiddenQuestions || []).map((q) => ({ ...q }));
  state.hiddenQuestions = (state.hiddenQuestions || []).filter(
    (q) => Number(q.id) !== Number(id)
  );
  renderHiddenQuestions();
  try {
    await apiFetch(`/api/hidden-questions/${id}`, { method: "DELETE" });
    toast("Удалено");
  } catch (err) {
    state.hiddenQuestions = snapshot;
    renderHiddenQuestions();
    toast(String(err.message || err));
  } finally {
    state.hiddenQBusy = false;
  }
}

/* ---------- Notification settings ---------- */

function syncNotificationsFromProfile(profile) {
  const n = profile?.notifications || {};
  state.notifications = {
    preferred_hour: n.preferred_hour == null ? null : Number(n.preferred_hour),
    reactivation_enabled: n.reactivation_enabled !== false,
    analysis_notify_enabled: n.analysis_notify_enabled !== false,
  };
}

/** Можно ли надёжно перевести UTC ↔ локальное время браузера. */
function canDetectLocalTz() {
  try {
    if (typeof Date === "undefined" || typeof Date.prototype.getTimezoneOffset !== "function") {
      return false;
    }
    // sanity: offset должен быть конечным числом
    const off = new Date().getTimezoneOffset();
    if (!Number.isFinite(off)) return false;
    if (typeof Intl !== "undefined" && Intl.DateTimeFormat) {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      // если API есть, но timezone пустой — всё равно offset обычно ок
      void tz;
    }
    return true;
  } catch (_) {
    return false;
  }
}

/** UTC hour (0–23) → локальный час пользователя. */
function utcHourToLocal(utcHour) {
  const h = ((Number(utcHour) % 24) + 24) % 24;
  if (!canDetectLocalTz()) return h;
  const d = new Date();
  d.setUTCHours(h, 0, 0, 0);
  return d.getHours();
}

/** Локальный час пользователя → UTC hour (0–23) для API. */
function localHourToUtc(localHour) {
  const h = ((Number(localHour) % 24) + 24) % 24;
  if (!canDetectLocalTz()) return h;
  const d = new Date();
  d.setHours(h, 0, 0, 0);
  return d.getUTCHours();
}

/** Формат «21:00» без суффикса. */
function fmtHourClock(h) {
  const n = ((Number(h) % 24) + 24) % 24;
  if (!Number.isFinite(n)) return "—";
  return `${String(n).padStart(2, "0")}:00`;
}

/**
 * Отображение preferred_hour из API (UTC):
 * — локально: «21:00 (твоё время)»
 * — fallback: «21:00 UTC»
 */
function fmtPreferredHourDisplay(utcHour) {
  if (utcHour == null || Number.isNaN(Number(utcHour))) return null;
  if (canDetectLocalTz()) {
    return `${fmtHourClock(utcHourToLocal(utcHour))} (твоё время)`;
  }
  return `${fmtHourClock(utcHour)} UTC`;
}

/** Подпись опции в селекте (локальный или UTC час). */
function fmtHourOptionLabel(hourValue) {
  if (canDetectLocalTz()) {
    return `${fmtHourClock(hourValue)} (твоё время)`;
  }
  return `${fmtHourClock(hourValue)} UTC`;
}

function renderNotifications() {
  const hourText = $("notif-hour-text");
  const react = $("notif-reactivation");
  const analysis = $("notif-analysis");
  const tzNote = document.querySelector(".notif-tz-note");
  const n = state.notifications || {};
  if (hourText) {
    if (n.preferred_hour == null || Number.isNaN(Number(n.preferred_hour))) {
      hourText.textContent =
        "Люм ещё учится твоим привычкам. Ответь на несколько квизов.";
    } else {
      hourText.textContent = `Обычно ты отвечаешь в ${fmtPreferredHourDisplay(
        n.preferred_hour
      )}`;
    }
  }
  if (tzNote) {
    tzNote.textContent = canDetectLocalTz()
      ? "Время квиза показано в твоём часовом поясе."
      : "Часовой пояс не определён — время в UTC.";
  }
  if (react) react.checked = n.reactivation_enabled !== false;
  if (analysis) analysis.checked = n.analysis_notify_enabled !== false;
}

function fillHourSelect(selectedUtc) {
  const sel = $("notif-hour-select");
  if (!sel) return;
  sel.innerHTML = "";
  const useLocal = canDetectLocalTz();
  for (let h = 0; h < 24; h++) {
    const opt = document.createElement("option");
    // value всегда локальный час (или UTC при fallback) — то, что видит пользователь
    opt.value = String(h);
    opt.textContent = fmtHourOptionLabel(h);
    sel.appendChild(opt);
  }
  let selectedLocal;
  if (selectedUtc != null && selectedUtc !== "" && !Number.isNaN(Number(selectedUtc))) {
    selectedLocal = useLocal ? utcHourToLocal(selectedUtc) : Number(selectedUtc);
  } else if (state.notifications?.preferred_hour != null) {
    selectedLocal = useLocal
      ? utcHourToLocal(state.notifications.preferred_hour)
      : Number(state.notifications.preferred_hour);
  } else {
    selectedLocal = useLocal ? new Date().getHours() : 10;
  }
  sel.value = String(((Number(selectedLocal) % 24) + 24) % 24);
  updateHourPreview();
}

function updateHourPreview() {
  const sel = $("notif-hour-select");
  const prev = $("notif-hour-preview");
  if (!sel || !prev) return;
  const localH = Number(sel.value);
  if (canDetectLocalTz()) {
    prev.textContent = `Квиз будет приходить около ${fmtHourClock(localH)} (твоё время)`;
  } else {
    prev.textContent = `Квиз будет приходить около ${fmtHourClock(localH)} UTC`;
  }
}

function openNotifHourSheet() {
  fillHourSelect(state.notifications?.preferred_hour);
  openSheetEl("notif-hour-sheet");
}

function closeNotifHourSheet() {
  closeSheetEl("notif-hour-sheet");
}

async function saveNotificationSettings(patch) {
  if (state.notifBusy) return;
  const prev = { ...(state.notifications || {}) };
  state.notifications = { ...prev, ...patch };
  renderNotifications();
  state.notifBusy = true;
  try {
    const res = await apiFetch("/api/notifications/settings", {
      method: "POST",
      body: patch,
    });
    syncNotificationsFromProfile({ notifications: res.notifications });
    renderNotifications();
    if (state.profile) state.profile.notifications = res.notifications;
  } catch (err) {
    state.notifications = prev;
    renderNotifications();
    toast(String(err.message || err));
  } finally {
    state.notifBusy = false;
  }
}

/* ——— Онбординг Mini App ——— */

function renderWeOnboardingBanner() {
  const banner = $("ob-we-banner");
  if (!banner) return;
  const incomplete = state.profile && state.profile.is_completed === false;
  banner.hidden = !incomplete;
}

async function maybeStartOnboarding() {
  if (!state.profile || state.profile.is_completed) {
    hideOnboardingScreen();
    return;
  }
  try {
    const st = await apiFetch("/api/onboarding/state");
    state.onboarding = st;
    if (st.is_completed) {
      hideOnboardingScreen();
      return;
    }
    openOnboardingScreen(st);
  } catch (err) {
    console.warn("[Shepot] onboarding state", err);
  }
}

function openOnboardingScreen(st) {
  state.onboarding = st;
  state.onboardingOpen = true;
  state.onboardingHistory = [];
  state.onboardingPendingSubmit = false;
  document.body.classList.add("onboarding-open");
  const screen = $("onboarding-screen");
  if (screen) screen.hidden = false;
  if (st.is_completed) {
    showOnboardingDone(st);
  } else {
    $("ob-done-view").hidden = true;
    $("ob-question-view").hidden = false;
    renderOnboardingState(st, { animate: true });
  }
}

function hideOnboardingScreen() {
  state.onboardingOpen = false;
  state.onboardingBusy = false;
  state.onboardingPendingSubmit = false;
  document.body.classList.remove("onboarding-open");
  const screen = $("onboarding-screen");
  if (screen) screen.hidden = true;
}

function showOnboardingDone(st) {
  $("ob-question-view").hidden = true;
  $("ob-done-view").hidden = false;
  const text =
    (st.ai_summary_public || st.ai_summary || "").trim() ||
    "Портрет почти готов — Люм ещё уточняет детали.";
  $("ob-done-summary").textContent = text;
  const total = st.total_steps || 18;
  $("ob-progress-label").textContent = `Шаг ${total} из ${total}`;
  $("ob-progress-bar").style.width = "100%";
}

function renderOnboardingState(st, { animate = true, fromBack = false } = {}) {
  state.onboarding = st;
  if (st.is_completed) {
    showOnboardingDone(st);
    return;
  }
  $("ob-done-view").hidden = true;
  $("ob-question-view").hidden = false;

  const step = Number(st.step || 0);
  const total = Number(st.total_steps || 18);
  const displayStep = Math.min(step + 1, total);
  $("ob-progress-label").textContent = `Шаг ${displayStep} из ${total}`;
  $("ob-progress-bar").style.width = `${Math.round((displayStep / total) * 100)}%`;

  const q = st.current_question;
  const status = $("ob-status");
  if (!q) {
    $("ob-question-text").textContent = st.needs_followups
      ? "Люм готовит уточняющие вопросы…"
      : "Загрузка…";
    $("ob-options").hidden = true;
    $("ob-open").hidden = true;
    status.hidden = false;
    status.textContent = "Подождите пару секунд";
    $("ob-back").hidden = true;
    if (st.needs_followups) {
      setTimeout(() => refreshOnboardingState(), 800);
    }
    return;
  }
  status.hidden = true;

  const slide = $("ob-slide");
  if (animate && slide) {
    slide.classList.remove("is-animating");
    void slide.offsetWidth;
    slide.classList.add("is-animating");
  }

  $("ob-question-text").textContent = q.text || "";
  const isMc = q.type === "multiple_choice";
  $("ob-options").hidden = !isMc;
  $("ob-open").hidden = isMc;

  if (isMc) {
    const box = $("ob-options");
    box.innerHTML = "";
    (q.options || []).forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ob-option";
      btn.textContent = opt;
      btn.addEventListener("click", () => submitOnboardingAnswer(opt, btn));
      box.appendChild(btn);
    });
  } else {
    const ta = $("ob-textarea");
    if (ta) {
      ta.value = "";
      ta.disabled = false;
      autoExpandTextarea(ta);
    }
    syncObNextEnabled();
  }

  state.onboardingPendingSubmit = false;
  const canBack = !fromBack && state.onboardingHistory.length > 0;
  $("ob-back").hidden = !canBack;
}

async function refreshOnboardingState() {
  try {
    const st = await apiFetch("/api/onboarding/state");
    renderOnboardingState(st, { animate: false });
  } catch (err) {
    toast(String(err.message || err));
  }
}

function syncObNextEnabled() {
  const ta = $("ob-textarea");
  const btn = $("ob-next");
  if (!btn) return;
  btn.disabled = state.onboardingBusy || !(ta && ta.value.trim());
}

async function submitOnboardingAnswer(answer, pickedBtn) {
  if (state.onboardingBusy || !state.onboarding) return;
  const step = Number(state.onboarding.step || 0);
  const q = state.onboarding.current_question;
  if (q) {
    state.onboardingHistory.push({
      step,
      question: { ...q },
    });
  }
  state.onboardingBusy = true;
  state.onboardingPendingSubmit = true;
  setObControlsDisabled(true);
  if (pickedBtn) pickedBtn.classList.add("is-picked");
  const status = $("ob-status");
  if (status) {
    status.hidden = false;
    status.textContent =
      step >= 14 ? "Люм думает…" : "Сохраняю…";
  }
  try {
    const st = await apiFetch("/api/onboarding/answer", {
      method: "POST",
      body: { step, answer: String(answer || "") },
    });
    if (state.profile) state.profile.is_completed = !!st.is_completed;
    if (st.is_completed) {
      if (state.profile) {
        state.profile.ai_summary = st.ai_summary;
        state.profile.ai_summary_public = st.ai_summary_public;
        state.profile.ai_summary_private = st.ai_summary_private;
      }
      showOnboardingDone(st);
      renderWeOnboardingBanner();
    } else {
      renderOnboardingState(st, { animate: true });
    }
  } catch (err) {
    state.onboardingHistory.pop();
    toast(String(err.message || err));
    setObControlsDisabled(false);
    if (status) status.hidden = true;
  } finally {
    state.onboardingBusy = false;
    state.onboardingPendingSubmit = false;
    setObControlsDisabled(false);
  }
}

async function skipOnboardingAnswer() {
  if (state.onboardingBusy || !state.onboarding) return;
  const step = Number(state.onboarding.step || 0);
  const q = state.onboarding.current_question;
  if (q) state.onboardingHistory.push({ step, question: { ...q } });
  state.onboardingBusy = true;
  setObControlsDisabled(true);
  try {
    const st = await apiFetch("/api/onboarding/skip", {
      method: "POST",
      body: { step, answer: "" },
    });
    if (state.profile) state.profile.is_completed = !!st.is_completed;
    if (st.is_completed) {
      if (state.profile) {
        state.profile.ai_summary = st.ai_summary;
        state.profile.ai_summary_public = st.ai_summary_public;
      }
      showOnboardingDone(st);
      renderWeOnboardingBanner();
    } else {
      renderOnboardingState(st, { animate: true });
    }
  } catch (err) {
    state.onboardingHistory.pop();
    toast(String(err.message || err));
  } finally {
    state.onboardingBusy = false;
    setObControlsDisabled(false);
  }
}

function setObControlsDisabled(on) {
  $("ob-options")?.querySelectorAll("button").forEach((b) => {
    b.disabled = !!on;
  });
  const ta = $("ob-textarea");
  if (ta) ta.disabled = !!on;
  const next = $("ob-next");
  if (next) next.disabled = !!on || !(ta && ta.value.trim());
  const skip = $("ob-skip");
  if (skip) skip.disabled = !!on;
  const back = $("ob-back");
  if (back) back.disabled = !!on;
}

function onboardingGoBack() {
  if (state.onboardingBusy || state.onboardingPendingSubmit) return;
  const ta = $("ob-textarea");
  if (ta && !$("ob-open")?.hidden && ta.value.trim()) {
    ta.value = "";
    syncObNextEnabled();
    return;
  }
  const prev = state.onboardingHistory.pop();
  if (!prev?.question) {
    $("ob-back").hidden = true;
    return;
  }
  // Локальный просмотр предыдущего; сервер остаётся на текущем шаге.
  // Повторный ответ идемпотентен (step < current → то же состояние).
  const ghost = {
    ...state.onboarding,
    step: prev.step,
    current_question: prev.question,
    needs_followups: false,
  };
  renderOnboardingState(ghost, { animate: true, fromBack: true });
  $("ob-back").hidden = state.onboardingHistory.length === 0;
  const status = $("ob-status");
  if (status) {
    status.hidden = false;
    status.textContent = "Уже отвечено — новый выбор просто вернёт к текущему шагу";
  }
}

async function finishOnboardingToApp() {
  hideOnboardingScreen();
  try {
    state.profile = await apiFetch("/api/profile");
    syncNotificationsFromProfile(state.profile);
    renderPortrait();
    renderWeOnboardingBanner();
    renderHomeHero();
  } catch (err) {
    console.warn(err);
  }
  setTab("home");
  toast("Добро пожаловать в Шёпот");
}

function closeOnboardingApp() {
  const tg = getTg();
  try {
    if (tg?.close) tg.close();
    else hideOnboardingScreen();
  } catch (e) {
    hideOnboardingScreen();
  }
}

function renderPortrait() {
  const me = state.profile;
  if (!me) return;
  renderWeOnboardingBanner();
  const partner = me.partner || {};
  const who = state.portraitWho;
  const isMe = who === "me";
  const name = isMe
    ? me.name || state.auth?.name || "Вы"
    : partner.name || me.partner_name || "Партнёр";

  const updated = isMe ? me.updated_at : partner.updated_at;
  $("portrait-name").textContent = name || "—";
  $("portrait-updated").textContent = updated
    ? `обновлено ${fmtDate(updated)}`
    : "";

  const badge = $("portrait-public-badge");
  const note = $("portrait-private-note");
  const empty = $("portrait-empty");
  const summaryEl = $("portrait-summary");
  const onboarding = $("portrait-onboarding");
  const openBot = $("portrait-open-bot");
  const emptyText = $("portrait-empty-text");

  badge.hidden = true;
  note.hidden = true;
  empty.hidden = true;
  summaryEl.hidden = false;
  summaryEl.textContent = "";

  if (isMe) {
    const hasPortrait = Boolean(me.is_completed && (me.ai_summary || me.ai_summary_public));
    if (!hasPortrait) {
      empty.hidden = false;
      summaryEl.hidden = true;
      if (!me.is_completed) {
        emptyText.textContent =
          "Заверши анкету, чтобы Люм начал собирать твой портрет.";
        openBot.hidden = true;
        onboarding.hidden = true;
      } else {
        emptyText.textContent =
          "Люм ещё собирает твой портрет. Загляни чуть позже.";
        openBot.hidden = false;
        onboarding.hidden = true;
      }
    } else {
      summaryEl.textContent = me.ai_summary || me.ai_summary_public || "";
      onboarding.hidden = false;
    }
  } else {
    onboarding.hidden = true;
    const publicText = (partner.ai_summary_public || "").trim();
    const hasPublic = Boolean(publicText);
    const completed = Boolean(partner.is_completed);

    if (!completed && !hasPublic) {
      empty.hidden = false;
      summaryEl.hidden = true;
      emptyText.textContent =
        "Люм ещё не собрал портрет партнёра. Пусть пройдёт анкету в боте.";
      openBot.hidden = true;
    } else if (hasPublic) {
      summaryEl.textContent = publicText;
      badge.hidden = false;
      if (partner.has_private) {
        note.hidden = false;
        note.textContent = `Полную версию видит только ${name} в боте`;
      }
    } else {
      // completed but no public split yet
      empty.hidden = false;
      summaryEl.hidden = true;
      emptyText.textContent =
        "Публичная версия портрета ещё не готова. Попросите партнёра обновить анкету в боте.";
      openBot.hidden = true;
    }
  }
}

function renderTopics(moods) {
  const grid = $("topics-grid");
  if (!grid) return;
  grid.innerHTML = "";
  const list = (moods || []).filter((m) => m.code && m.code !== "surprise");
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
      ? `<span class="lock" aria-hidden="true">🔒</span><span>${escapeHtml(m.label)}</span>`
      : `<span>${escapeHtml(m.label)}</span>`;
    chip.addEventListener("click", () => onTopicChipTap(m));
    grid.appendChild(chip);
  }
}

let _pendingBlockMood = null;

function openTopicConfirm(mood) {
  _pendingBlockMood = mood;
  $("topic-confirm-title").textContent = `Закрыть тему ${mood.label}?`;
  $("topic-confirm-text").textContent =
    "Люм больше не будет её спрашивать.";
  const sheet = $("topic-confirm");
  sheet.hidden = false;
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closeTopicConfirm() {
  _pendingBlockMood = null;
  const sheet = $("topic-confirm");
  sheet.classList.remove("is-open");
  setTimeout(() => {
    if (!sheet.classList.contains("is-open")) sheet.hidden = true;
  }, 220);
}

async function onTopicChipTap(mood) {
  if (mood.blocked) {
    // разблокировать сразу
    await setTopicBlocked(mood, false);
    return;
  }
  openTopicConfirm(mood);
}

async function setTopicBlocked(mood, blocked) {
  try {
    await apiFetch("/api/topics/block", {
      method: "POST",
      body: { code: mood.code, blocked },
    });
    toast(blocked ? "Тема закрыта" : "Тема открыта заново");
    await refreshMoods();
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  }
}

async function refreshProfile() {
  try {
    setWeSkeleton(true);
    state.profile = await apiFetch("/api/profile");
    renderPortrait();
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else throw err;
  } finally {
    setWeSkeleton(false);
  }
}

async function refreshMoods() {
  try {
    const skT = $("topics-skeleton");
    const grid = $("topics-grid");
    if (skT) skT.hidden = false;
    if (grid) grid.hidden = true;
    state.moods = (await apiFetch("/api/moods")).moods || [];
    renderTopics(state.moods);
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else throw err;
  } finally {
    const skT = $("topics-skeleton");
    const grid = $("topics-grid");
    if (skT) skT.hidden = true;
    if (grid) grid.hidden = false;
  }
}

function renderMoodSheet(moods) {
  const grid = $("mood-grid");
  grid.innerHTML = "";
  $("mood-error").hidden = true;
  // 2×3: только конкретные темы (без surprise) — до 6 шт.
  const list = (moods || []).filter((m) => m.code && m.code !== "surprise").slice(0, 6);
  for (const m of list) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mood-btn" + (m.blocked ? " is-blocked" : "");
    btn.textContent = m.label;
    btn.addEventListener("click", () => {
      if (m.blocked) {
        toast("Тема закрыта. Открой её в табе «Мы».");
        return;
      }
      startQuiz(m.code);
    });
    grid.appendChild(btn);
  }
}

function openMoodSheet() {
  renderMoodSheet(state.moods);
  const sheet = $("mood-sheet");
  sheet.hidden = false;
  // force reflow → animate in
  requestAnimationFrame(() => {
    sheet.classList.add("is-open");
  });
}

function closeMoodSheet() {
  const sheet = $("mood-sheet");
  sheet.classList.remove("is-open");
  setTimeout(() => {
    if (!sheet.classList.contains("is-open")) sheet.hidden = true;
  }, 220);
}

async function startQuiz(moodCode) {
  if (state.starting) return;
  state.starting = true;
  const err = $("mood-error");
  err.hidden = true;
  try {
    await apiFetch("/api/quiz/start", {
      method: "POST",
      body: { mood_code: moodCode },
    });
    closeMoodSheet();
    toast("Квиз отправлен в чат с Люмом 🌿");
    setTimeout(() => {
      try {
        getTg()?.close?.();
      } catch (_) {
        /* ignore */
      }
    }, 1500);
  } catch (e) {
    if (e.code === 409) {
      toast("У вас уже есть активный квиз. Продолжите в боте.");
      closeMoodSheet();
    } else if (e.code === 400) {
      toast(String(e.message || e));
    } else {
      toast(String(e.message || e));
    }
  } finally {
    state.starting = false;
  }
}

function onBackButtonClick() {
  closeDetailView(true);
}

function showTelegramBackButton() {
  const bb = window.Telegram?.WebApp?.BackButton;
  if (!bb) return;
  try {
    bb.onClick(onBackButtonClick);
    bb.show();
  } catch (_) {
    /* older clients / browser */
  }
}

function hideTelegramBackButton() {
  const bb = window.Telegram?.WebApp?.BackButton;
  if (!bb) return;
  try {
    bb.offClick(onBackButtonClick);
    bb.hide();
  } catch (_) {
    /* older clients / browser */
  }
}

/** После сворачивания Mini App — восстановить BackButton, если открыты детали. */
function restoreTelegramBackButtonIfNeeded() {
  if (state.detailOpen) showTelegramBackButton();
  else hideTelegramBackButton();
}

function setDetailSkeleton(on) {
  const sk = $("detail-skeleton");
  const content = $("detail-content");
  if (sk) sk.hidden = !on;
  if (content) content.hidden = !!on;
}

function renderAccordion(questions, myUserId) {
  const root = $("detail-accordion");
  if (!root) return;
  root.innerHTML = "";
  (questions || []).forEach((item, idx) => {
    const qText = item.question || item.text || "";
    const details = document.createElement("details");
    details.className = "acc-item";
    if (idx === 0) details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = `${idx + 1}. ${qText}`;
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "acc-body";
    const answers = item.answers || [];
    if (!answers.length) {
      body.innerHTML = `<p class="muted small">Ответов пока нет</p>`;
    } else {
      answers.forEach((a, i) => {
        const mine = myUserId && Number(a.user_id) === Number(myUserId);
        const side = i % 2 === 0 ? "left" : "right";
        const block = document.createElement("div");
        block.className = `answer-block ${side}${mine ? " mine" : ""}`;
        if (a.skipped) {
          block.innerHTML = `<strong>${escapeHtml(a.name || "—")}:</strong> <em class="skipped">(пропущен)</em>`;
        } else {
          const val = a.answer || a.selected_option || a.text || "—";
          block.innerHTML = `<strong>${escapeHtml(a.name || "—")}:</strong> ${escapeHtml(val)}`;
        }
        body.appendChild(block);
      });
    }
    details.appendChild(body);
    root.appendChild(details);
  });
}

/* ---------- Discussion chat (quiz detail) ---------- */

function renderDiscussion() {
  const empty = $("discussion-empty");
  const chat = $("discussion-chat");
  const list = $("discussion-messages");
  const startBtn = $("discussion-start-btn");
  if (!list) return;
  const msgs = state.discussion?.messages || [];
  const active = !!state.discussion?.is_active;
  list.innerHTML = "";

  if (!msgs.length) {
    if (empty) {
      empty.hidden = false;
      const p = empty.querySelector("p");
      if (p) {
        p.textContent = "Приватный разговор с Люмом — партнёр его не увидит.";
      }
    }
    if (chat) chat.hidden = true;
    if (startBtn) startBtn.textContent = "Начать обсуждение";
    return;
  }
  if (chat) chat.hidden = false;

  for (const m of msgs) {
    const row = document.createElement("div");
    const isLum = m.role === "lum";
    row.className = "discussion-bubble " + (isLum ? "is-lum" : "is-user");
    row.innerHTML = isLum
      ? `<span class="discussion-ico" aria-hidden="true">🌿</span><p>${escapeHtml(m.text || "")}</p>`
      : `<p>${escapeHtml(m.text || "")}</p>`;
    list.appendChild(row);
  }

  const compose = document.querySelector(".discussion-compose");
  if (compose) compose.hidden = !active;

  if (!active) {
    if (empty) {
      empty.hidden = false;
      const p = empty.querySelector("p");
      if (p) {
        p.textContent =
          "Обсуждение закрыто. Можно продолжить — партнёр по-прежнему ничего не увидит.";
      }
    }
    if (startBtn) startBtn.textContent = "Продолжить обсуждение";
  } else {
    if (empty) empty.hidden = true;
    if (startBtn) startBtn.textContent = "Начать обсуждение";
  }

  requestAnimationFrame(() => {
    list.scrollTop = list.scrollHeight;
  });
}

async function loadDiscussion(quizId) {
  state.discussion = { messages: [], is_active: false, busy: false };
  renderDiscussion();
  try {
    const res = await apiFetch(`/api/quiz/${quizId}/discussion`);
    state.discussion = {
      messages: res.messages || [],
      is_active: !!res.is_active,
      busy: false,
    };
    renderDiscussion();
  } catch (err) {
    toast(String(err.message || err));
  }
}

async function startDiscussionUi() {
  const quizId = state.detailQuizId;
  if (!quizId) return;
  // Если уже есть активный чат — просто показать
  if (state.discussion?.messages?.length && state.discussion.is_active) {
    const empty = $("discussion-empty");
    const chat = $("discussion-chat");
    if (empty) empty.hidden = true;
    if (chat) chat.hidden = false;
    $("discussion-input")?.focus();
    return;
  }
  // Старт: отправить мягкий триггер — пустое нельзя; используем готовое первое сообщение через POST с текстом-намерением
  // Если истории нет — POST с текстом откроет приветствие + ответ. Лучше: показать чат после первого user msg.
  // Для кнопки «Начать» — сразу открываем compose и ждём ввода; при первом send API создаст greet.
  state.discussion = {
    messages: state.discussion?.messages || [],
    is_active: true,
    busy: false,
  };
  const empty = $("discussion-empty");
  const chat = $("discussion-chat");
  if (empty) empty.hidden = true;
  if (chat) chat.hidden = false;
  renderDiscussion();
  $("discussion-input")?.focus();
}

function setDiscussionTyping(on) {
  const el = $("discussion-typing");
  if (el) el.hidden = !on;
}

async function sendDiscussionMessage() {
  const quizId = state.detailQuizId;
  const ta = $("discussion-input");
  if (!quizId || !ta || state.discussion?.busy) return;
  const text = String(ta.value || "").trim();
  if (text.length < 1) {
    toast("Напиши сообщение");
    return;
  }
  const snapshot = (state.discussion.messages || []).map((m) => ({ ...m }));
  const optimisticUser = {
    role: "user",
    text,
    created_at: new Date().toISOString(),
  };
  state.discussion.messages = [...snapshot, optimisticUser];
  state.discussion.is_active = true;
  state.discussion.busy = true;
  ta.value = "";
  renderDiscussion();
  setDiscussionTyping(true);
  try {
    const res = await apiFetch(`/api/quiz/${quizId}/discussion`, {
      method: "POST",
      body: { text },
    });
    // перезагружаем историю, чтобы подтянуть greet + reply без дублей
    const fresh = await apiFetch(`/api/quiz/${quizId}/discussion`);
    state.discussion.messages = fresh.messages || [];
    state.discussion.is_active = fresh.is_active !== false;
    if (res.reply && !(fresh.messages || []).some((m) => m.role === "lum" && m.text === res.reply)) {
      state.discussion.messages.push({
        role: "lum",
        text: res.reply,
        created_at: new Date().toISOString(),
      });
    }
    renderDiscussion();
  } catch (err) {
    state.discussion.messages = snapshot;
    renderDiscussion();
    toast(String(err.message || err));
  } finally {
    state.discussion.busy = false;
    setDiscussionTyping(false);
  }
}

async function doneDiscussionUi() {
  const quizId = state.detailQuizId;
  if (!quizId || state.discussion?.busy) return;
  state.discussion.busy = true;
  try {
    await apiFetch(`/api/quiz/${quizId}/discussion`, {
      method: "POST",
      body: { done: true },
    });
    await loadDiscussion(quizId);
    toast("Обсуждение закрыто");
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    state.discussion.busy = false;
  }
}

async function openQuizDetail(quizId) {
  const view = $("quiz-detail-view");
  if (!view) return;
  state.detailOpen = true;
  state.detailQuizId = Number(quizId);
  view.hidden = false;
  document.body.classList.add("detail-open");
  setDetailSkeleton(true);
  showTelegramBackButton();

  try {
    history.pushState({ shepotDetail: true, quizId: Number(quizId) }, "");
  } catch (_) {
    /* ignore */
  }

  try {
    const q = await apiFetch(`/api/quiz/${quizId}`);
    const title = q.topic_label || q.topic || `Квиз #${quizId}`;
    $("detail-title").textContent = title;
    $("detail-date").textContent = fmtDateShort(q.date);
    $("detail-insight-title").textContent =
      INSIGHT_TITLES[Math.floor(Math.random() * INSIGHT_TITLES.length)];

    const analysis = (q.analysis || "").trim();
    $("detail-analysis").textContent = analysis
      ? analysis
      : "Люм ещё готовит разбор. Загляни позже.";

    const myId = state.auth?.user_id || state.profile?.user_id;
    renderAccordion(q.questions || [], myId);

    const discussBtn = $("detail-discuss");
    if (discussBtn) {
      discussBtn.onclick = (e) => {
        e.preventDefault();
        const url = q.discuss_url || discussUrl(quizId);
        openBotLink(url);
        setTimeout(() => {
          try {
            getTg()?.close?.();
          } catch (_) {
            /* ignore */
          }
        }, 300);
      };
    }

    const noteBtn = $("detail-note-btn");
    if (noteBtn) {
      noteBtn.onclick = () => {
        openNoteCreate({ quizId: Number(quizId), linkChecked: true });
      };
    }

    await loadDiscussion(Number(quizId));
  } catch (e) {
    if (e.code === 404) {
      toast("Квиз не найден");
      closeDetailView(true);
      return;
    }
    toast(String(e.message || e));
    closeDetailView(true);
    return;
  } finally {
    setDetailSkeleton(false);
  }
}

function closeDetailView(useHistoryBack) {
  if (!state.detailOpen) return;
  state.detailOpen = false;
  state.detailQuizId = null;
  const view = $("quiz-detail-view");
  if (view) view.hidden = true;
  document.body.classList.remove("detail-open");
  hideTelegramBackButton();
  if (useHistoryBack) {
    try {
      if (history.state?.shepotDetail) history.back();
    } catch (_) {
      /* ignore */
    }
  }
  // Остаёмся на текущем табе (Главная / История) — не форсим history
  if (state.tab === "history") renderHistory(state.history);
}

function closeDetail() {
  closeDetailView(true);
}

async function loadMoods() {
  await refreshMoods();
}

const DIGEST_SEEN_KEY = "shepot_digest_seen_at";
const DIGEST_VISIT_KEY = "shepot_last_visit";

function digestSeenAt() {
  try {
    return Number(localStorage.getItem(DIGEST_SEEN_KEY) || 0);
  } catch (_) {
    return 0;
  }
}

function markDigestSeen() {
  try {
    localStorage.setItem(DIGEST_SEEN_KEY, String(Date.now()));
    localStorage.setItem(DIGEST_VISIT_KEY, String(Date.now()));
  } catch (_) {
    /* ignore */
  }
}

function utcMonthName(monthIdx) {
  return UTC_MONTHS_SHORT[monthIdx] || "";
}

function utcMonthGenitive(monthIdx) {
  return UTC_MONTHS_GENITIVE[monthIdx] || utcMonthName(monthIdx);
}

/** Диапазон недели: «9 — 15 сентября» или «30 авг — 5 сен». */
function fmtWeekRange(startIso, endIso, createdIso) {
  const parse = (iso) => {
    const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return null;
    return { y: Number(m[1]), mo: Number(m[2]), d: Number(m[3]) };
  };

  let a = parse(startIso);
  let b = parse(endIso);

  // Старые записи без week_start/end — восстановить пн–вс по created_at (UTC)
  if ((!a || !b) && createdIso) {
    const c = parse(createdIso);
    if (c) {
      const utc = Date.UTC(c.y, c.mo - 1, c.d);
      const dow = new Date(utc).getUTCDay(); // 0=вс … 6=сб
      const offsetMon = dow === 0 ? -6 : 1 - dow;
      const monMs = utc + offsetMon * 86400000;
      const sunMs = monMs + 6 * 86400000;
      const mon = new Date(monMs);
      const sun = new Date(sunMs);
      a = {
        y: mon.getUTCFullYear(),
        mo: mon.getUTCMonth() + 1,
        d: mon.getUTCDate(),
      };
      b = {
        y: sun.getUTCFullYear(),
        mo: sun.getUTCMonth() + 1,
        d: sun.getUTCDate(),
      };
    }
  }

  if (!a || !b) return "Эта неделя";

  if (a.mo === b.mo && a.y === b.y) {
    return `${a.d} — ${b.d} ${utcMonthGenitive(b.mo - 1)}`;
  }
  return `${a.d} ${utcMonthName(a.mo - 1)} — ${b.d} ${utcMonthName(b.mo - 1)}`;
}

function digestShortText(d) {
  if (!d) return "";
  const short = (d.pattern_short || "").trim();
  if (short) return short;
  const full = (d.pattern || "").trim();
  if (full.length <= 120) return full;
  return `${full.slice(0, 117).trim()}…`;
}

function isDigestFresh(d) {
  if (!d?.created_at) return false;
  const created = Date.parse(d.created_at);
  if (!Number.isFinite(created)) return false;
  const ageMs = Date.now() - created;
  if (ageMs > 7 * 24 * 60 * 60 * 1000) return false;
  return created > digestSeenAt();
}

function renderDigestBanner(d) {
  const banner = $("digest-banner");
  if (!banner) return;
  if (!d || !isDigestFresh(d)) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  $("digest-banner-text").textContent = digestShortText(d);
}

function renderDigestLatest(d) {
  const latest = $("digest-latest");
  const empty = $("digest-empty");
  if (!d) {
    if (latest) latest.hidden = true;
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  if (latest) latest.hidden = false;
  $("digest-week-range").textContent = fmtWeekRange(
    d.week_start,
    d.week_end,
    d.created_at
  );
  $("digest-pattern").textContent = d.pattern || "";
  const temp = $("digest-temp");
  if (temp) {
    temp.textContent =
      d.avg_temperature != null
        ? `Средняя температура: ${Number(d.avg_temperature).toFixed(1)}°`
        : "";
  }
  renderDigestActions(d);
}

function renderDigestActions(d) {
  const list = $("digest-actions");
  if (!list || !d) return;
  list.innerHTML = "";
  const actions = d.actions || [];
  actions.forEach((a, idx) => {
    const li = document.createElement("li");
    li.className = "digest-action" + (a.done ? " is-done" : "");
    li.innerHTML = `
      <label class="digest-check">
        <input type="checkbox" data-digest-action="${idx}" ${a.done ? "checked" : ""} />
        <span class="digest-check-box" aria-hidden="true"></span>
        <span class="digest-action-text">${escapeHtml(a.text || "")}</span>
      </label>
    `;
    const input = li.querySelector("input");
    input?.addEventListener("change", async () => {
      const done = Boolean(input.checked);
      li.classList.toggle("is-done", done);
      try {
        const res = await apiFetch("/api/digest/action", {
          method: "POST",
          body: { digest_id: d.id, action_index: idx, done },
        });
        if (state.digestLatest && res.actions) {
          state.digestLatest.actions = res.actions;
        }
      } catch (err) {
        input.checked = !done;
        li.classList.toggle("is-done", !done);
        toast(String(err.message || err));
      }
    });
    list.appendChild(li);
  });
}

function renderDigestHistory(items, latestId) {
  const list = $("digest-history");
  const empty = $("digest-history-empty");
  if (!list) return;
  list.innerHTML = "";
  const past = (items || []).filter((d) => d.id !== latestId);
  if (!past.length) {
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  for (const d of past) {
    const li = document.createElement("li");
    li.className = "digest-hist-item";
    const title = `${fmtWeekRange(d.week_start, d.week_end, d.created_at)} · ${d.quiz_count || 0} квиза`;
    li.innerHTML = `
      <button type="button" class="digest-hist-toggle">
        <span>${escapeHtml(title)}</span>
        <span class="digest-hist-chevron">▾</span>
      </button>
      <div class="digest-hist-body" hidden>
        <p class="summary small">${escapeHtml(d.pattern || "")}</p>
      </div>
    `;
    const btn = li.querySelector(".digest-hist-toggle");
    const body = li.querySelector(".digest-hist-body");
    btn?.addEventListener("click", () => {
      if (!body) return;
      body.hidden = !body.hidden;
      li.classList.toggle("is-open", !body.hidden);
    });
    list.appendChild(li);
  }
}

async function refreshDigest(opts = {}) {
  if (!(state.authToken || state.initData)) return;
  const bannerOnly = Boolean(opts.bannerOnly);
  try {
    if (bannerOnly && state.digestLoaded && state.digestLatest) {
      renderDigestBanner(state.digestLatest);
      return;
    }
    const [latestRes, histRes] = await Promise.all([
      apiFetch("/api/digest/latest"),
      bannerOnly ? Promise.resolve(null) : apiFetch("/api/digest/history?limit=10"),
    ]);
    state.digestLatest = latestRes.digest || null;
    if (histRes) {
      state.digestHistory = histRes.digests || [];
      state.digestLoaded = true;
    }
    renderDigestBanner(state.digestLatest);
    if (!bannerOnly) {
      renderDigestLatest(state.digestLatest);
      renderDigestHistory(state.digestHistory, state.digestLatest?.id);
      if (state.digestLatest) markDigestSeen();
    }
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else if (!bannerOnly) toast(String(err.message || err));
  }
}

function openDigestFromBanner() {
  markDigestSeen();
  renderDigestBanner(null);
  setTab("more");
  requestAnimationFrame(() => {
    $("digest-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

const IDEA_CAT = {
  romance: { emoji: "❤️", label: "романтика", cls: "cat-romance" },
  adventure: { emoji: "🏔", label: "приключение", cls: "cat-adventure" },
  cozy: { emoji: "🛋", label: "уют", cls: "cat-cozy" },
  deep_talk: { emoji: "🌱", label: "разговор", cls: "cat-deep" },
  fun: { emoji: "😂", label: "веселье", cls: "cat-fun" },
};

function emptyIdeasState() {
  return { proposed: [], saved: [], done: [] };
}

function normalizeIdeasPayload(raw) {
  if (!raw) return emptyIdeasState();
  if (Array.isArray(raw)) {
    const out = emptyIdeasState();
    for (const idea of raw) {
      const st = idea.status || "proposed";
      if (out[st]) out[st].push(idea);
    }
    return out;
  }
  return {
    proposed: raw.proposed || [],
    saved: raw.saved || [],
    done: raw.done || [],
  };
}

function allIdeasFlat() {
  const g = state.ideas || emptyIdeasState();
  return [...(g.proposed || []), ...(g.saved || []), ...(g.done || [])];
}

function findIdea(id) {
  return allIdeasFlat().find((i) => Number(i.id) === Number(id));
}

function removeIdeaFromGroups(id) {
  const g = state.ideas || emptyIdeasState();
  const nid = Number(id);
  g.proposed = (g.proposed || []).filter((i) => Number(i.id) !== nid);
  g.saved = (g.saved || []).filter((i) => Number(i.id) !== nid);
  g.done = (g.done || []).filter((i) => Number(i.id) !== nid);
  state.ideas = g;
}

function placeIdeaInGroup(idea) {
  removeIdeaFromGroups(idea.id);
  const st = idea.status || "proposed";
  if (!state.ideas[st]) state.ideas[st] = [];
  state.ideas[st].unshift(idea);
}

function setIdeasSkeleton(on) {
  const sk = $("ideas-skeleton");
  if (sk) sk.hidden = !on;
}

function ideaDoneDate(idea) {
  const iso = idea.updated_at || idea.created_at || "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return "";
  return `${Number(m[3])} ${utcMonthGenitive(Number(m[2]) - 1)}`;
}

function ideaCardHtml(idea, mode) {
  const cat = IDEA_CAT[idea.category] || IDEA_CAT.cozy;
  const saved = idea.status === "saved";
  let meta = escapeHtml(idea.duration_hint || "");
  let actions = "";
  let menuBtn = "";

  if (mode === "done") {
    meta = escapeHtml(ideaDoneDate(idea));
  } else if (mode === "saved") {
    menuBtn = `<button type="button" class="idea-menu" data-idea-menu="${idea.id}" aria-label="Ещё">⋯</button>`;
    actions = `<button type="button" class="idea-btn idea-btn-done" data-idea-done="${idea.id}">✓ Сделали</button>`;
  } else {
    actions = `<button type="button" class="idea-btn idea-btn-save" data-idea-save="${idea.id}">${
      saved ? "✓ Сохранено" : "❤️ Сохранить"
    }</button>
         <button type="button" class="idea-btn idea-btn-x" data-idea-dismiss="${idea.id}" aria-label="Скрыть">✕</button>`;
  }

  return `
    <article class="idea-card${mode === "done" ? " is-done-archive" : ""}" data-idea-id="${idea.id}" data-idea-mode="${mode}" role="button" tabindex="0">
      <div class="idea-top">
        <span class="idea-cat ${cat.cls}">${cat.emoji} ${cat.label}</span>
        <div class="idea-meta">
          <span class="idea-duration muted">${meta}</span>
          ${menuBtn}
        </div>
      </div>
      <h3 class="idea-title">${escapeHtml(idea.title || "")}</h3>
      <p class="idea-desc">${escapeHtml(idea.description || "")}</p>
      ${
        mode === "done"
          ? ""
          : `<div class="idea-bottom">
        <span class="idea-budget muted">${escapeHtml(idea.budget_hint || "")}</span>
        <div class="idea-actions">${actions}</div>
      </div>
      <button type="button" class="idea-undo" data-idea-undo="${idea.id}" hidden>Вернуть</button>`
      }
    </article>
  `;
}

function fillIdeaSheet(idea) {
  const cat = IDEA_CAT[idea.category] || IDEA_CAT.cozy;
  const catEl = $("idea-sheet-cat");
  if (catEl) {
    catEl.className = `idea-cat ${cat.cls}`;
    catEl.textContent = `${cat.emoji} ${cat.label}`;
  }
  $("idea-sheet-duration").textContent =
    idea.status === "done"
      ? ideaDoneDate(idea)
      : idea.duration_hint || "";
  $("idea-sheet-title").textContent = idea.title || "";
  $("idea-sheet-desc").textContent = idea.description || "";
  $("idea-sheet-budget").textContent = idea.budget_hint
    ? `💰 ${idea.budget_hint}`
    : "";
  const saveBtn = $("idea-sheet-save");
  if (saveBtn) {
    if (idea.status === "done") {
      saveBtn.hidden = true;
    } else {
      saveBtn.hidden = false;
      const saved = idea.status === "saved";
      saveBtn.textContent = saved ? "✓ Сохранено" : "❤️ Сохранить";
      saveBtn.disabled = saved;
      saveBtn.classList.toggle("is-saved", saved);
    }
  }
}

function openIdeaSheet(ideaId) {
  const idea = findIdea(ideaId);
  if (!idea || idea.status === "dismissed") return;
  state.ideaSheetId = Number(ideaId);
  fillIdeaSheet(idea);
  const sheet = $("idea-sheet");
  if (!sheet) return;
  sheet.hidden = false;
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closeIdeaSheet() {
  state.ideaSheetId = null;
  const sheet = $("idea-sheet");
  if (!sheet) return;
  sheet.classList.remove("is-open");
  setTimeout(() => {
    if (!sheet.classList.contains("is-open")) sheet.hidden = true;
  }, 220);
}

function openIdeaActionSheet(ideaId) {
  const idea = findIdea(ideaId);
  if (!idea || idea.status !== "saved") return;
  state.ideaActionId = Number(ideaId);
  const sheet = $("idea-action-sheet");
  if (!sheet) return;
  sheet.hidden = false;
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closeIdeaActionSheet() {
  state.ideaActionId = null;
  const sheet = $("idea-action-sheet");
  if (!sheet) return;
  sheet.classList.remove("is-open");
  setTimeout(() => {
    if (!sheet.classList.contains("is-open")) sheet.hidden = true;
  }, 220);
}

function syncIdeaSheetIfOpen() {
  if (state.ideaSheetId == null) return;
  const idea = findIdea(state.ideaSheetId);
  if (!idea || idea.status === "dismissed") {
    closeIdeaSheet();
    return;
  }
  fillIdeaSheet(idea);
}

function renderIdeas() {
  const list = $("ideas-list");
  const savedWrap = $("ideas-saved-wrap");
  const savedList = $("ideas-saved");
  const doneWrap = $("ideas-done-wrap");
  const doneList = $("ideas-done");
  const doneTitle = $("ideas-done-title");
  const empty = $("ideas-empty");
  if (!list) return;

  const g = state.ideas || emptyIdeasState();
  const proposed = g.proposed || [];
  const saved = g.saved || [];
  const done = g.done || [];

  list.innerHTML = "";
  if (savedList) savedList.innerHTML = "";
  if (doneList) doneList.innerHTML = "";

  if (!proposed.length && !saved.length && !done.length) {
    if (empty) empty.hidden = false;
    if (savedWrap) savedWrap.hidden = true;
    if (doneWrap) doneWrap.hidden = true;
    syncIdeaSheetIfOpen();
    return;
  }
  if (empty) empty.hidden = true;

  for (const idea of proposed) {
    list.insertAdjacentHTML("beforeend", ideaCardHtml(idea, "proposed"));
  }
  if (savedWrap && savedList) {
    savedWrap.hidden = !saved.length;
    for (const idea of saved) {
      savedList.insertAdjacentHTML("beforeend", ideaCardHtml(idea, "saved"));
    }
  }
  if (doneWrap && doneList && doneTitle) {
    if (!done.length) {
      doneWrap.hidden = true;
    } else {
      doneWrap.hidden = false;
      doneTitle.textContent = `✅ Уже сделали · ${done.length}`;
      doneList.hidden = !state.ideasDoneOpen;
      $("ideas-done-toggle")?.setAttribute(
        "aria-expanded",
        state.ideasDoneOpen ? "true" : "false"
      );
      doneWrap.classList.toggle("is-open", state.ideasDoneOpen);
      for (const idea of done) {
        doneList.insertAdjacentHTML("beforeend", ideaCardHtml(idea, "done"));
      }
    }
  }

  bindIdeaCardEvents(list);
  if (savedList) bindIdeaCardEvents(savedList);
  if (doneList) bindIdeaCardEvents(doneList);
  syncIdeaSheetIfOpen();
}

function bindIdeaLongPress(card, ideaId) {
  let timer = null;
  const clear = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  card.addEventListener(
    "touchstart",
    (e) => {
      if (e.target.closest("button")) return;
      clear();
      timer = setTimeout(() => {
        timer = null;
        openIdeaActionSheet(ideaId);
      }, 500);
    },
    { passive: true }
  );
  card.addEventListener("touchend", clear, { passive: true });
  card.addEventListener("touchmove", clear, { passive: true });
  card.addEventListener("touchcancel", clear, { passive: true });
}

function bindIdeaCardEvents(root) {
  root.querySelectorAll(".idea-card").forEach((card) => {
    const id = Number(card.dataset.ideaId);
    const mode = card.dataset.ideaMode;
    const open = () => openIdeaSheet(id);
    card.onclick = (e) => {
      if (e.target.closest("button, .idea-actions, .idea-undo, .idea-menu")) return;
      open();
    };
    card.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    };
    if (mode === "saved") bindIdeaLongPress(card, id);
  });

  root.querySelectorAll("[data-idea-save]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      onIdeaSave(Number(btn.dataset.ideaSave), btn);
    };
  });
  root.querySelectorAll("[data-idea-dismiss]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      onIdeaDismiss(Number(btn.dataset.ideaDismiss));
    };
  });
  root.querySelectorAll("[data-idea-done]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      onIdeaDone(Number(btn.dataset.ideaDone));
    };
  });
  root.querySelectorAll("[data-idea-undo]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      onIdeaUndo(Number(btn.dataset.ideaUndo));
    };
  });
  root.querySelectorAll("[data-idea-menu]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      openIdeaActionSheet(Number(btn.dataset.ideaMenu));
    };
  });
}

async function onIdeaSave(id, btn) {
  const idea = findIdea(id);
  if (!idea || idea.status === "saved") return;
  const prev = { ...idea };
  idea.status = "saved";
  placeIdeaInGroup(idea);
  renderIdeas();
  try {
    const res = await apiFetch(`/api/ideas/${id}/status`, {
      method: "POST",
      body: { status: "saved" },
    });
    placeIdeaInGroup(res.idea || idea);
    renderIdeas();
  } catch (err) {
    placeIdeaInGroup(prev);
    renderIdeas();
    toast("Не удалось сохранить. Попробуйте позже");
  }
}

async function onIdeaDismiss(id) {
  const idea = findIdea(id);
  if (!idea) return;
  const prev = { ...idea };
  idea.status = "dismissed";
  const card = document.querySelector(`.idea-card[data-idea-id="${id}"]`);
  if (card) {
    card.classList.add("is-leaving");
    const undo = card.querySelector(".idea-undo");
    if (undo) {
      undo.hidden = false;
      undo.onclick = (e) => {
        e.stopPropagation();
        clearTimeout(idea._dismissTimer);
        onIdeaUndo(id);
      };
    }
  }
  idea._dismissTimer = setTimeout(() => {
    removeIdeaFromGroups(id);
    renderIdeas();
  }, 5000);

  try {
    await apiFetch(`/api/ideas/${id}/status`, {
      method: "POST",
      body: { status: "dismissed" },
    });
  } catch (err) {
    clearTimeout(idea._dismissTimer);
    placeIdeaInGroup(prev);
    renderIdeas();
    toast("Не удалось сохранить. Попробуйте позже");
  }
}

async function onIdeaUndo(id) {
  let idea = findIdea(id);
  if (!idea) {
    idea = { id, status: "proposed", title: "", description: "" };
  }
  clearTimeout(idea._dismissTimer);
  idea.status = "proposed";
  placeIdeaInGroup(idea);
  renderIdeas();
  try {
    const res = await apiFetch(`/api/ideas/${id}/status`, {
      method: "POST",
      body: { status: "proposed" },
    });
    placeIdeaInGroup(res.idea || idea);
    renderIdeas();
  } catch (err) {
    toast("Не удалось сохранить. Попробуйте позже");
  }
}

async function onIdeaDone(id) {
  const idea = findIdea(id);
  if (!idea) return;
  const prev = { ...idea };
  idea.status = "done";
  placeIdeaInGroup(idea);
  renderIdeas();
  try {
    const res = await apiFetch(`/api/ideas/${id}/status`, {
      method: "POST",
      body: { status: "done" },
    });
    placeIdeaInGroup(res.idea || idea);
    renderIdeas();
    toast("Отметили — класс 🌿");
  } catch (err) {
    placeIdeaInGroup(prev);
    renderIdeas();
    toast("Не удалось сохранить. Попробуйте позже");
  }
}

async function onIdeaRestoreToProposed(id) {
  const idea = findIdea(id);
  if (!idea) return;
  const prev = { ...idea };
  idea.status = "proposed";
  placeIdeaInGroup(idea);
  closeIdeaActionSheet();
  renderIdeas();
  try {
    const res = await apiFetch(`/api/ideas/${id}/status`, {
      method: "POST",
      body: { status: "proposed" },
    });
    placeIdeaInGroup(res.idea || idea);
    renderIdeas();
  } catch (err) {
    placeIdeaInGroup(prev);
    renderIdeas();
    toast("Не удалось сохранить. Попробуйте позже");
  }
}

async function onIdeaDeleteForever(id) {
  const idea = findIdea(id);
  if (!idea) return;
  const prev = { ...idea };
  const card = document.querySelector(`.idea-card[data-idea-id="${id}"]`);
  closeIdeaActionSheet();
  if (card) {
    card.classList.add("is-fade-out");
    await new Promise((r) => setTimeout(r, 300));
  }
  removeIdeaFromGroups(id);
  renderIdeas();
  try {
    await apiFetch(`/api/ideas/${id}`, { method: "DELETE" });
    toast("Идея удалена");
  } catch (err) {
    placeIdeaInGroup(prev);
    renderIdeas();
    toast("Не удалось сохранить. Попробуйте позже");
  }
}

async function refreshIdeas() {
  if (!(state.authToken || state.initData)) return;
  setIdeasSkeleton(true);
  try {
    const res = await apiFetch("/api/ideas");
    state.ideas = normalizeIdeasPayload(res.ideas);
    state.ideasLoaded = true;
    renderIdeas();
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else toast(String(err.message || err));
  } finally {
    setIdeasSkeleton(false);
  }
}

async function regenerateIdeas() {
  if (state.ideasBusy) return;
  state.ideasBusy = true;
  const btn = $("ideas-refresh");
  const gen = $("ideas-generate");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("is-spin");
  }
  if (gen) gen.disabled = true;
  setIdeasSkeleton(true);
  try {
    const res = await apiFetch("/api/ideas/regenerate", { method: "POST", body: {} });
    const next = normalizeIdeasPayload(res.ideas);
    // сохраняем saved/done из кэша, меняем только proposed
    state.ideas = {
      proposed: next.proposed || [],
      saved: state.ideas.saved || [],
      done: state.ideas.done || [],
    };
    state.ideasLoaded = true;
    renderIdeas();
    toast("Люм предложил новые идеи 💡");
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    state.ideasBusy = false;
    setIdeasSkeleton(false);
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-spin");
    }
    if (gen) gen.disabled = false;
  }
}

async function refreshAll() {
  $("pull-indicator").hidden = false;
  setWeSkeleton(true);
  setHistorySkeleton(true);
  try {
    const [active, history, profile, moodsRes] = await Promise.all([
      apiFetch("/api/quiz/active"),
      apiFetch("/api/history?limit=30"),
      apiFetch("/api/profile"),
      apiFetch("/api/moods"),
    ]);
    state.active = active;
    state.history = history.quizzes || [];
    state.historyLoaded = true;
    state.profile = profile;
    state.moods = moodsRes.moods || [];
    if (active?.streak) state.streak = active.streak;
    syncNotificationsFromProfile(profile);
    renderNotifications();

    renderHomeHero();
    renderHomeCta(active);
    renderLastQuiz(state.history);
    renderHistory(state.history);
    renderPortrait();
    renderWeOnboardingBanner();
    renderTopics(state.moods);
    await refreshDigest();
    await refreshIdeas();
    await refreshNotes(true);
    await refreshHiddenQuestions(true);
    setDiag([`Квизов в истории: ${state.history.length}`, `Статус: ${active?.status_text || "—"}`]);
  } catch (err) {
    if (err.code === 401) toast(err.message);
    else throw err;
  } finally {
    setWeSkeleton(false);
    setHistorySkeleton(false);
    $("pull-indicator").hidden = true;
  }
}

function bindUi() {
  document.querySelectorAll(".tabbar .tab").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });

  $("ob-close")?.addEventListener("click", () => closeOnboardingApp());
  $("ob-continue")?.addEventListener("click", () => finishOnboardingToApp());
  $("ob-we-continue")?.addEventListener("click", () => maybeStartOnboarding());
  $("ob-back")?.addEventListener("click", () => onboardingGoBack());
  $("ob-skip")?.addEventListener("click", () => skipOnboardingAnswer());
  $("ob-next")?.addEventListener("click", () => {
    const ta = $("ob-textarea");
    const text = (ta?.value || "").trim();
    if (text) submitOnboardingAnswer(text);
  });
  $("ob-textarea")?.addEventListener("input", () => {
    autoExpandTextarea($("ob-textarea"));
    syncObNextEnabled();
  });

  $("cta-start")?.addEventListener("click", openMoodSheet);
  $("cta-retry")?.addEventListener("click", () => bootSession());
  $("cta-answer")?.addEventListener("click", () => {
    openBotLink(botDeepLink("quiz_continue"));
    // Mini App не закрываем — можно вернуться
  });

  $("more-open-bot")?.addEventListener("click", (e) => {
    e.preventDefault();
    openBotLink(botDeepLink());
  });
  bindBotAnchor($("portrait-open-bot"), "onboarding");
  $("portrait-onboarding")?.addEventListener("click", (e) => {
    e.preventDefault();
    maybeStartOnboarding();
  });
  $("more-manage-topics")?.addEventListener("click", () => {
    applyWeMode("portraits");
    setTab("we");
  });
  $("more-reset-onboarding")?.addEventListener("click", async () => {
    const ok = window.confirm(
      "Сбросить анкету и портрет? Придётся пройти 18 шагов заново."
    );
    if (!ok) return;
    try {
      const st = await apiFetch("/api/onboarding/reset", { method: "POST", body: {} });
      if (state.profile) {
        state.profile.is_completed = false;
        state.profile.ai_summary = null;
        state.profile.ai_summary_public = null;
        state.profile.ai_summary_private = null;
      }
      renderWeOnboardingBanner();
      openOnboardingScreen(st);
      toast("Анкета сброшена");
    } catch (err) {
      toast(String(err.message || err));
    }
  });
  $("ideas-refresh")?.addEventListener("click", () => regenerateIdeas());
  $("ideas-generate")?.addEventListener("click", () => regenerateIdeas());
  $("ideas-done-toggle")?.addEventListener("click", () => {
    state.ideasDoneOpen = !state.ideasDoneOpen;
    renderIdeas();
  });
  document.querySelectorAll("[data-close-idea]").forEach((el) =>
    el.addEventListener("click", closeIdeaSheet)
  );
  document.querySelectorAll("[data-close-idea-action]").forEach((el) =>
    el.addEventListener("click", closeIdeaActionSheet)
  );
  $("idea-sheet-save")?.addEventListener("click", async () => {
    if (state.ideaSheetId == null) return;
    await onIdeaSave(state.ideaSheetId);
  });
  $("idea-action-restore")?.addEventListener("click", async () => {
    if (state.ideaActionId == null) return;
    await onIdeaRestoreToProposed(state.ideaActionId);
  });
  $("idea-action-delete")?.addEventListener("click", async () => {
    if (state.ideaActionId == null) return;
    await onIdeaDeleteForever(state.ideaActionId);
  });

  const banner = $("digest-banner");
  banner?.addEventListener("click", openDigestFromBanner);
  banner?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openDigestFromBanner();
    }
  });

  document.querySelectorAll("[data-go-home]").forEach((b) =>
    b.addEventListener("click", () => {
      setTab("home");
      openMoodSheet();
    })
  );
  document.querySelectorAll("[data-go-home-only]").forEach((b) =>
    b.addEventListener("click", () => setTab("home"))
  );

  document.querySelectorAll("[data-close-mood]").forEach((el) =>
    el.addEventListener("click", closeMoodSheet)
  );

  $("detail-back")?.addEventListener("click", () => closeDetailView(true));

  const lastCard = $("last-quiz-card");
  const openLast = () => {
    const id = lastCard?.dataset.quizId;
    if (id) openQuizDetail(id);
  };
  lastCard?.addEventListener("click", openLast);
  lastCard?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openLast();
    }
  });

  window.addEventListener("popstate", () => {
    if (state.detailOpen) closeDetailView(false);
  });

  // Свернули Mini App и вернулись — BackButton снова нужна, если открыты детали
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") restoreTelegramBackButtonIfNeeded();
  });
  window.addEventListener("focus", restoreTelegramBackButtonIfNeeded);
  try {
    getTg()?.onEvent?.("activated", restoreTelegramBackButtonIfNeeded);
  } catch (_) {
    /* ignore */
  }

  document.querySelectorAll("#we-mode-segment .seg").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyWeMode(btn.dataset.weMode);
      if (state.weMode === "dynamics") refreshDynamics();
      if (state.weMode === "notes") refreshNotes();
      if (state.weMode === "hidden") refreshHiddenQuestions();
    });
  });

  $("home-note-btn")?.addEventListener("click", () => openNoteCreate({ quizId: null }));
  $("notes-new-btn")?.addEventListener("click", () => openNoteCreate({ linkChecked: false }));
  $("notes-empty-create")?.addEventListener("click", () => openNoteCreate({ linkChecked: false }));
  $("notes-show-all")?.addEventListener("click", () => {
    state.notesShowAll = true;
    renderNotes();
  });

  $("hidden-q-new-btn")?.addEventListener("click", openHiddenQuestionCreate);
  $("hidden-q-empty-create")?.addEventListener("click", openHiddenQuestionCreate);
  document.querySelectorAll("[data-close-hidden-q]").forEach((el) =>
    el.addEventListener("click", closeHiddenQuestionSheet)
  );
  $("hidden-q-save-btn")?.addEventListener("click", () => saveHiddenQuestion());
  $("hidden-q-textarea")?.addEventListener("input", () => {
    autoExpandTextarea($("hidden-q-textarea"));
    syncHiddenQSaveEnabled();
  });

  $("notif-hour-edit")?.addEventListener("click", openNotifHourSheet);
  document.querySelectorAll("[data-close-notif-hour]").forEach((el) =>
    el.addEventListener("click", closeNotifHourSheet)
  );
  $("notif-hour-select")?.addEventListener("change", updateHourPreview);
  $("notif-hour-save")?.addEventListener("click", async () => {
    const sel = $("notif-hour-select");
    const localH = Number(sel?.value);
    if (!Number.isFinite(localH) || localH < 0 || localH > 23) {
      toast("Выбери час");
      return;
    }
    const utcH = localHourToUtc(localH);
    await saveNotificationSettings({ preferred_hour: utcH });
    closeNotifHourSheet();
    toast("Время квиза сохранено");
  });
  $("notif-reactivation")?.addEventListener("change", (e) => {
    saveNotificationSettings({ reactivation_enabled: !!e.target.checked });
  });
  $("notif-analysis")?.addEventListener("change", (e) => {
    saveNotificationSettings({ analysis_notify_enabled: !!e.target.checked });
  });

  $("discussion-start-btn")?.addEventListener("click", () => startDiscussionUi());
  $("discussion-send-btn")?.addEventListener("click", () => sendDiscussionMessage());
  $("discussion-done-btn")?.addEventListener("click", () => doneDiscussionUi());
  $("discussion-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendDiscussionMessage();
    }
  });

  document.querySelectorAll("[data-close-note]").forEach((el) =>
    el.addEventListener("click", closeNoteSheet)
  );
  document.querySelectorAll("[data-close-note-delete]").forEach((el) =>
    el.addEventListener("click", closeNoteDeleteConfirm)
  );
  $("note-edit-btn")?.addEventListener("click", openNoteEditFromView);
  $("note-delete-btn")?.addEventListener("click", openNoteDeleteConfirm);
  $("note-delete-ok")?.addEventListener("click", () => deleteNoteConfirmed());
  $("note-save-btn")?.addEventListener("click", () => saveNoteFromSheet());
  $("note-textarea")?.addEventListener("input", () => {
    autoExpandTextarea($("note-textarea"));
    syncNoteSaveEnabled();
  });
  $("note-link-quiz")?.addEventListener("change", (e) => {
    if (e.target.checked) {
      state.noteDraftQuizId =
        state.noteDraftQuizId || lastFinishedQuiz()?.quiz_id || null;
      if (!state.noteDraftQuizId) {
        e.target.checked = false;
        toast("Пока нет разобранного квиза для привязки");
      }
    } else {
      // keep explicit quiz from detail if user unchecks then rechecks — draft stays
    }
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

  document.querySelectorAll("[data-close-topic-confirm]").forEach((el) =>
    el.addEventListener("click", closeTopicConfirm)
  );
  $("topic-confirm-cancel")?.addEventListener("click", closeTopicConfirm);
  $("topic-confirm-ok")?.addEventListener("click", async () => {
    const mood = _pendingBlockMood;
    closeTopicConfirm();
    if (mood) await setTopicBlocked(mood, true);
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
          if (state.tab === "we" && state.weMode === "dynamics") {
            await refreshDynamics();
          } else if (state.tab === "we" && state.weMode === "notes") {
            await refreshNotes(true);
          } else if (state.tab === "we" && state.weMode === "hidden") {
            await refreshHiddenQuestions(true);
          } else {
            await refreshAll();
          }
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
