const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const initData = tg?.initData || "";
const tgUser = tg?.initDataUnsafe?.user || null;

const state = { category: "all", offset: 0, limit: 8, total: 0, currentVideo: null, waitSeconds: 15, allItems: [] };

const CATEGORY_EMOJIS = { all: "🗂️", movie: "🎬", natok: "📺", series: "🎞️", drama: "🎭" };
function categoryLabel(cat) {
  const emoji = CATEGORY_EMOJIS[String(cat).toLowerCase()] || "📁";
  const name = cat === "all" ? "All" : cat;
  return `${emoji} ${name}`;
}

const $ = (id) => document.getElementById(id);

function setTopbarHeight() {
  const tb = document.querySelector(".topbar");
  if (tb) document.documentElement.style.setProperty("--topbar-h", tb.offsetHeight + "px");
}
setTopbarHeight();
window.addEventListener("resize", setTopbarHeight);
const haptic = (type = "light") => tg?.HapticFeedback?.impactOccurred?.(type);

function openExternal(url) {
  if (tg?.openLink) tg.openLink(url, { try_instant_view: false });
  else window.open(url, "_blank");
}

function formatDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "request failed");
  return res.json();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- Theme ----------
function initTheme() {
  let saved = "dark";
  try { saved = localStorage.getItem("cinehub_theme") || "dark"; } catch (e) {}
  applyTheme(saved);
  $("themeToggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    const next = cur === "light" ? "dark" : "light";
    applyTheme(next);
    try { localStorage.setItem("cinehub_theme", next); } catch (e) {}
    haptic();
  });
}
function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  $("themeIcon").textContent = mode === "light" ? "🌙" : "☀️";
}

// ---------- Drawer ----------
function initDrawer() {
  const open = () => { $("drawer").classList.remove("hidden"); $("drawerOverlay").classList.remove("hidden"); };
  const close = () => { $("drawer").classList.add("hidden"); $("drawerOverlay").classList.add("hidden"); };
  $("menuBtn").addEventListener("click", () => {
    haptic();
    if ($("drawer").classList.contains("hidden")) open(); else close();
  });
  $("drawerOverlay").addEventListener("click", close);
  document.querySelectorAll(".drawer-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".drawer-item").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      close();
      const view = btn.dataset.view;
      if (view === "history") loadHistory();
      if (view === "profile") renderProfile();
      showView(view);
    });
  });
}

function renderProfile() {
  const card = $("profileCard");
  if (!tgUser) {
    card.innerHTML = `<div class="profile-row"><span class="k">Status</span><span class="v">Guest</span></div>`;
    return;
  }
  card.innerHTML = `
    <div class="profile-row"><span class="k">Name</span><span class="v">${escapeHtml(tgUser.first_name || "")} ${escapeHtml(tgUser.last_name || "")}</span></div>
    <div class="profile-row"><span class="k">Username</span><span class="v">${tgUser.username ? "@" + escapeHtml(tgUser.username) : "—"}</span></div>
    <div class="profile-row"><span class="k">Telegram ID</span><span class="v">${tgUser.id}</span></div>
  `;
}

// ---------- Tabs / grid ----------
async function initTabs() {
  const data = await api("/api/categories");
  const tabs = $("tabs");
  tabs.innerHTML = "";
  data.categories.forEach((cat, i) => {
    const btn = document.createElement("button");
    btn.className = "tab" + (i === 0 ? " active" : "");
    btn.dataset.cat = cat;
    btn.textContent = categoryLabel(cat);
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.category = cat;
      state.offset = 0;
      $("searchBox").value = "";
      haptic();
      loadGrid(true);
    });
    tabs.appendChild(btn);
  });
}

async function loadGrid(reset) {
  if (reset) { $("grid").innerHTML = ""; state.offset = 0; state.allItems = []; }
  const data = await api("/api/videos", {
    method: "POST",
    body: { category: state.category, offset: state.offset, init: initData },
  });
  state.total = data.total;
  if (reset) renderHeroCarousel(data.items.slice(0, 3));
  state.allItems = state.allItems.concat(data.items);
  $("emptyState").style.display = data.items.length === 0 && reset ? "block" : "none";
  data.items.forEach((v) => $("grid").appendChild(buildCard(v)));
  state.offset += data.items.length;
  $("loadMore").style.display = state.offset < state.total ? "block" : "none";
}

function fmtCountdown(totalSeconds) {
  totalSeconds = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
}

function buildCard(v) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.title = v.title.toLowerCase();
  const isLocked = (v.locked_remaining || 0) > 0;
  if (isLocked) {
    card.classList.add("is-locked");
    card.dataset.lockedUntil = Date.now() + v.locked_remaining * 1000;
  }
  card.innerHTML = `<img class="poster" loading="lazy" src="${v.thumb}" onerror="this.style.background='#22222c'">
    ${isLocked ? `<div class="card-lock-overlay"><span class="lock-emoji">🔒</span><span class="lock-countdown">${fmtCountdown(v.locked_remaining)}</span></div>` : ""}
    <div class="card-body">
      <div class="card-title">${escapeHtml(v.title)}</div>
      <div class="card-date">🕐 ${formatDate(v.added_at)}</div>
    </div>`;
  card.addEventListener("click", () => openDetail(v.id));
  return card;
}

// Tick every card's lock countdown once a second, no re-fetching needed.
setInterval(() => {
  document.querySelectorAll(".card.is-locked").forEach((card) => {
    const remaining = (Number(card.dataset.lockedUntil) - Date.now()) / 1000;
    const label = card.querySelector(".lock-countdown");
    if (remaining <= 0) {
      card.classList.remove("is-locked");
      card.querySelector(".card-lock-overlay")?.remove();
      return;
    }
    if (label) label.textContent = fmtCountdown(remaining);
  });
}, 1000);

// ---------- Hero carousel (auto-sliding, latest 3 videos) ----------
let heroTimer = null;
let heroIndex = 0;

function renderHeroCarousel(items) {
  const track = $("heroTrack");
  const dots = $("heroDots");
  const carousel = $("heroCarousel");
  clearInterval(heroTimer);
  heroIndex = 0;

  if (!items.length) { track.innerHTML = ""; dots.innerHTML = ""; carousel.style.display = "none"; return; }
  carousel.style.display = "";

  track.innerHTML = items.map((v) => `
    <div class="hero-slide" style="background-image:url(${v.thumb})">
      <div class="hero-content">
        <div class="hero-title">${escapeHtml(v.title)}</div>
        <button class="hero-cta" data-id="${v.id}">▶ এখনই দেখুন</button>
      </div>
    </div>`).join("");
  dots.innerHTML = items.map((_, i) => `<span class="${i === 0 ? "active" : ""}"></span>`).join("");

  track.querySelectorAll(".hero-cta").forEach((btn) => {
    btn.addEventListener("click", () => openDetail(Number(btn.dataset.id)));
  });

  const goTo = (i) => {
    heroIndex = (i + items.length) % items.length;
    track.style.transform = `translateX(-${heroIndex * 100}%)`;
    dots.querySelectorAll("span").forEach((d, idx) => d.classList.toggle("active", idx === heroIndex));
  };

  if (items.length > 1) {
    heroTimer = setInterval(() => goTo(heroIndex + 1), 4000);
  }

  // manual swipe
  let startX = null;
  track.addEventListener("touchstart", (e) => { startX = e.touches[0].clientX; clearInterval(heroTimer); }, { passive: true });
  track.addEventListener("touchend", (e) => {
    if (startX === null) return;
    const dx = e.changedTouches[0].clientX - startX;
    if (Math.abs(dx) > 40) goTo(heroIndex + (dx < 0 ? 1 : -1));
    startX = null;
    if (items.length > 1) heroTimer = setInterval(() => goTo(heroIndex + 1), 4000);
  }, { passive: true });
}

$("loadMore").addEventListener("click", () => loadGrid(false));

$("searchBox").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll(".card").forEach((card) => {
    card.style.display = !q || card.dataset.title.includes(q) ? "" : "none";
  });
  $("heroCarousel").style.display = q ? "none" : "";
});

// ---------- Detail view ----------
let lockedTimer = null;

function showLockedArea(remainingSeconds) {
  clearInterval(lockedTimer);
  const tick = () => {
    remainingSeconds -= 1;
    if (remainingSeconds <= 0) {
      clearInterval(lockedTimer);
      // re-fetch so the ad-unlock flow becomes available again
      openDetail(state.currentVideo.id);
      return;
    }
    $("lockedCountdown").textContent = fmtCountdown(remainingSeconds);
  };
  $("lockedCountdown").textContent = fmtCountdown(remainingSeconds);
  lockedTimer = setInterval(tick, 1000);
}

async function openDetail(id) {
  const v = await api(`/api/video/${id}`, { method: "POST", body: { init: initData } });
  state.currentVideo = v;
  $("detailTitle").textContent = v.title;
  $("detailBadge").textContent = v.category_label;
  $("detailDesc").textContent = v.description || "";
  $("detailDesc").classList.toggle("hidden", !v.description);
  $("detailPoster").src = v.thumb;
  resetTaskUI();
  clearInterval(lockedTimer);

  const isLocked = (v.locked_remaining || 0) > 0;
  $("lockedArea").classList.toggle("hidden", !isLocked);
  $("unlockArea").classList.toggle("hidden", isLocked);
  $("doneArea").classList.add("hidden");
  $("taskArea").classList.add("hidden");
  if (isLocked) showLockedArea(v.locked_remaining);

  showView("detail");
  haptic();
}

$("detailClose").addEventListener("click", () => { clearInterval(lockedTimer); showView("grid"); });
$("backToChatBtn").addEventListener("click", () => {
  haptic("light");
  try {
    if (tg && typeof tg.close === "function") { tg.close(); return; }
  } catch (e) { /* fall through */ }
  showView("grid");
});

function resetTaskUI() {
  $("check1").textContent = "○"; $("check1").classList.remove("done");
  $("check2").textContent = "○"; $("check2").classList.remove("done");
  $("visitBtn1").disabled = false; $("visitBtn1").textContent = "VISIT"; $("visitBtn1").classList.remove("completed");
  $("visitBtn2").disabled = true; $("visitBtn2").textContent = "VISIT"; $("visitBtn2").classList.remove("completed");
  $("taskSub1").textContent = "ভিজিট করে সময় অপেক্ষা করুন";
  $("taskSub2").textContent = "প্রথম Task শেষ করুন";
  $("taskHint").textContent = "";
}

$("unlockBtn").addEventListener("click", async () => {
  const v = state.currentVideo;
  try {
    const data = await api("/api/unlock/start", { method: "POST", body: { init: initData, video_id: v.id } });
    state.waitSeconds = data.wait_seconds || 15;
    $("waitSecLabel").textContent = state.waitSeconds;
    $("unlockArea").classList.add("hidden");
    $("taskArea").classList.remove("hidden");
    armVisitButton(1, data.ad_link);
  } catch (e) {
    // video got locked (e.g. downloaded from another session) between opening the
    // detail page and tapping Unlock — refresh into the locked/countdown state
    openDetail(v.id);
  }
});

// ---------- Task 1 & 2: rotated direct ad links, verified by real time-away ----------
// Each task opens one of its 4 direct links (config.TASK1_AD_LINKS /
// TASK2_AD_LINKS, rotated per user+video by the server) and requires the
// user to actually stay away for AD_WAIT_SECONDS before it completes —
// tracked via the Page Visibility API so a quick tab-switch back can't fake it.
let watchState = null;

document.addEventListener("visibilitychange", () => {
  if (!watchState) return;
  if (document.hidden) {
    watchState.hiddenSince = Date.now();
  } else if (watchState.hiddenSince) {
    watchState.hiddenAccum += (Date.now() - watchState.hiddenSince) / 1000;
    watchState.hiddenSince = null;
    checkWatchTask();
  }
});

async function checkWatchTask() {
  if (!watchState) return;
  const { step, hiddenAccum, required } = watchState;
  const btn = $(`visitBtn${step}`);
  if (hiddenAccum >= required) {
    watchState = null;
    haptic("medium");
    const confirm = step === 1 ? confirmAd1 : confirmAd2;
    try { await confirm(); } catch (e) { /* confirm() already shows the popup */ }
    return;
  }
  const remaining = Math.ceil(required - hiddenAccum);
  btn.disabled = false;
  btn.textContent = "VISIT";
  $("taskHint").textContent = "";
  showVerifyPopup(`আপনি সম্পূর্ণ ${required} সেকেন্ড বিজ্ঞাপন পেজে থাকেননি! আরও <b>${remaining}</b> সেঃ প্রয়োজন — আবার VISIT করুন।`);
}

function armVisitButton(step, adLink) {
  const btn = $(`visitBtn${step}`);
  $(`taskSub${step}`).textContent = `ভিজিট করে ${state.waitSeconds} সেকেন্ড অপেক্ষা করুন`;
  btn.disabled = false;
  btn.textContent = "VISIT";
  btn.onclick = () => {
    openExternal(adLink);
    watchState = { step, hiddenAccum: 0, hiddenSince: null, required: state.waitSeconds };
    btn.disabled = true;
    btn.textContent = "⏳";
    $("taskHint").textContent = "Ad ট্যাবে থাকুন, ফিরে আসলে যাচাই হবে…";
  };
}

// ---------- Premium verification-failed popup ----------
function showVerifyPopup(message) {
  $("verifyPopupMsg").innerHTML = message;
  $("verifyPopupOverlay").classList.remove("hidden");
}
$("verifyPopupOk").addEventListener("click", () => $("verifyPopupOverlay").classList.add("hidden"));

async function confirmAd1(silent) {
  const v = state.currentVideo;
  try {
    const data = await api("/api/unlock/ad1", { method: "POST", body: { init: initData, video_id: v.id } });
    $("check1").textContent = "✓"; $("check1").classList.add("done");
    $("taskSub1").textContent = "সম্পন্ন";
    $("visitBtn1").disabled = true; $("visitBtn1").textContent = "COMPLETED"; $("visitBtn1").classList.add("completed");
    $("taskHint").textContent = "";
    armVisitButton(2, data.ad_link);
  } catch (e) {
    if (silent) throw e;
    showVerifyPopup(e.message);
    $("visitBtn1").disabled = false;
    $("visitBtn1").textContent = "VISIT";
  }
}

async function confirmAd2(silent) {
  const v = state.currentVideo;
  try {
    await api("/api/unlock/ad2", { method: "POST", body: { init: initData, video_id: v.id } });
    $("check2").textContent = "✓"; $("check2").classList.add("done");
    $("taskSub2").textContent = "সম্পন্ন";
    $("visitBtn2").disabled = true; $("visitBtn2").textContent = "COMPLETED"; $("visitBtn2").classList.add("completed");
    haptic("heavy");
    $("taskArea").classList.add("hidden");
    $("doneArea").classList.remove("hidden");
  } catch (e) {
    if (silent) throw e;
    showVerifyPopup(e.message);
    $("visitBtn2").disabled = false;
    $("visitBtn2").textContent = "VISIT";
  }
}

// ---------- History ----------
async function loadHistory() {
  const data = await api("/api/history", { method: "POST", body: { init: initData } });
  const list = $("historyList");
  list.innerHTML = "";
  if (!data.items.length) {
    list.innerHTML = `<div class="empty">এখনো কিছু দেখা হয়নি।</div>`;
    return;
  }
  data.items.forEach((h) => {
    const row = document.createElement("div");
    row.className = "history-item";
    row.innerHTML = `<div><div class="h-title">${escapeHtml(h.title)}</div>
                      <div class="h-meta">${h.category_label} · ${h.when}</div></div>`;
    list.appendChild(row);
  });
}

// ---------- Nav ----------
function showView(name) {
  $("grid-view").classList.toggle("hidden", name !== "grid");
  $("tabs").classList.toggle("hidden", name !== "grid");
  $("detail-view").classList.toggle("hidden", name !== "detail");
  $("history-view").classList.toggle("hidden", name !== "history");
  $("profile-view").classList.toggle("hidden", name !== "profile");
  document.querySelectorAll(".drawer-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
}

// ---------- Platform gate + admin-aware protections ----------
// Honest limitation: a web page cannot truly block OS-level screenshots or
// third-party screen-recording apps — no browser/WebView exposes that
// control to page content, and Telegram's Mini App API has no such method
// either. What follows are UI deterrents only (disable copy/long-press-save,
// blur the instant the app is backgrounded) — not real screenshot DRM.
async function bootstrap() {
  initTheme();
  initDrawer();

  state.isAdmin = false;
  if (initData) {
    try {
      const who = await api("/api/whoami", { method: "POST", body: { init: initData } });
      state.isAdmin = !!who.is_admin;
    } catch (e) { /* no valid session (e.g. opened outside Telegram) */ }
  }

  const platform = tg?.platform || "unknown";
  const isMobileApp = platform === "ios" || platform === "android";
  if (!isMobileApp && !state.isAdmin) {
    $("webBlockOverlay").classList.remove("hidden");
    return; // Telegram Web/Desktop or a plain browser — don't load the app
  }

  if (!state.isAdmin) {
    document.body.classList.add("protected");
    document.addEventListener("contextmenu", (e) => e.preventDefault());
    document.addEventListener("visibilitychange", () => {
      $("root").classList.toggle("privacy-blur", document.hidden);
    });
  }

  await initTabs();
  await loadGrid(true);
}

bootstrap();
