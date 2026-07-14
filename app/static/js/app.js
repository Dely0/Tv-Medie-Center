/* TV Media Center */
let _currentView = "home";
let _currentType = "";
let _searchTimer = null;
let _searchFocused = false;
let _browsePage = 1;
let _searchPage = 1;
let _hlsInstance = null;
let _playerTimer = null;
let _playId = 0;       // current video id
let _playEp = 0;       // current episode number
let _playEps = [];     // episode list [{num, title}]
let _navCycling = 0; // 导航栏切换Tab时阻止autoFocusView抢焦点(计数器,支持连续快速切换)

function esc(s) {
  if (!s) return "";
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function navigateTo(view, param) {
  // If leaving player, stop it first
  if (_currentView === "player" && view !== "player") {
    stopPlayerInternal(false);
  }
  document.querySelectorAll(".view").forEach(v => { v.classList.remove("active"); v.classList.add("hidden"); });
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  _currentView = view;
  const el = document.getElementById("view-" + view);
  el.classList.remove("hidden");
  el.classList.add("active");

  if (view === "home") {
    document.querySelector('.nav-btn[data-view="home"]').classList.add("active");
    loadHome();
  } else if (view === "browse") {
    _currentType = param || "movie";
    const nb = document.querySelector('.nav-btn[data-type="' + _currentType + '"]');
    if (nb) nb.classList.add("active");
    loadBrowse(_currentType);
  } else if (view === "history") {
    const hb = document.querySelector('.nav-btn[data-view="history"]');
    if (hb) hb.classList.add("active");
    loadHistory();
  } else if (view === "detail") {
    loadDetail(param);
  }
}

// 进入新视图后自动聚焦第一个可操作元素
function autoFocusView() {
  setTimeout(() => {
    if (_navCycling > 0) { _navCycling--; return; }
    const view = document.querySelector(".view.active");
    if (!view) return;
    const first = view.querySelector(".video-card, .play-btn, .episode-btn, .section-more, .browse-tab");
    if (first) first.focus();
  }, 50);
}

async function loadHome() {
  const el = document.getElementById("view-home");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';
  try {
    const data = await F("/api/home");
    if (!data.sections || !data.sections.length) {
      el.innerHTML = '<div class="empty-view">No data</div>';
      return;
    }
    let html = "";
    for (const sec of data.sections) {
      html += '<div class="section"><div class="section-header"><div class="section-title">' + sec.name + '</div></div><div class="card-grid scroll-x">';
      for (const v of sec.videos) html += card(v);
      html += '</div></div>';
    }
    el.innerHTML = html;
    autoFocusView();
  } catch (e) {
    el.innerHTML = '<div class="error-view">Load failed<button class="retry-btn" onclick="loadHome()">Retry</button></div>';
  }
}

async function loadBrowse(type) {
  _browsePage = 1;
  _currentType = type;
  document.getElementById("view-browse").innerHTML = '<div id="browse-content"><div class="loading"><div class="spinner"></div>Loading...</div></div>';
  await loadBrowsePage();
}

async function loadBrowsePage(direction) {
  if (direction === "next") _browsePage++;
  else if (direction === "prev" && _browsePage > 1) _browsePage--;
  const el = document.getElementById("browse-content");
  try {
    const data = await F("/api/browse?type=" + _currentType + "&page=" + _browsePage);
    if (!data.results || !data.results.length) {
      if (direction === "next") _browsePage--;
      el.innerHTML = '<div class="empty-view">No data</div>';
      return;
    }
    let html = '<div class="card-grid">';
    for (const v of data.results) html += card(v);
    html += '</div>';
    html += '<div style="display:flex;justify-content:center;gap:16px;margin-top:24px">' +
      (_browsePage > 1 ? '<button class="nav-btn browse-prev" onclick="loadBrowsePage(\'prev\')">◀ 上一页</button>' : '') +
      '<span style="font-size:22px;color:var(--text-dim);padding:10px 16px">第 ' + _browsePage + ' 页</span>' +
      (data.results.length >= 30 ? '<button class="nav-btn browse-next" onclick="loadBrowsePage(\'next\')">下一页 ▶</button>' : '') +
      '</div>';
    el.innerHTML = html;
    autoFocusView();
  } catch (e) {
    el.innerHTML = '<div class="error-view">Load failed</div>';
  }
}

async function loadDetail(videoId) {
  const el = document.getElementById("view-detail");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';
  try {
    const v = await F("/api/video/" + videoId);
    if (!v || !v.id) { el.innerHTML = '<div class="empty-view">Not found</div>'; return; }

    let meta = [];
    const tn = { movie: "Movie", tv: "TV", variety: "Variety", anime: "Anime" };
    if (v.type) meta.push(tn[v.type] || v.type);
    if (v.year) meta.push(v.year);
    if (v.rating) meta.push("⭐ " + v.rating);

    let epHtml = "";
    if (v.episodes && v.episodes.length && v.type !== "movie") {
      epHtml = '<div style="font-size:22px;font-weight:bold;margin:16px 0 12px">Episodes</div><div class="episode-grid">';
      for (const ep of v.episodes) {
        epHtml += '<button class="episode-btn" onclick="openPlayerAndPlay(' + v.id + ',' + ep.episode_num + ')">' + (ep.episode_title || "Ep." + ep.episode_num) + '</button>';
      }
      epHtml += '</div>';
    }

    el.innerHTML =
      '<div class="detail-layout">' +
      '<div class="detail-poster"><img src="' + (v.cover || imageFallback(360, 480)) + '" onerror="this.src=\'' + imageFallback(360, 480) + '\'"></div>' +
      '<div class="detail-info">' +
      '<div class="detail-title">' + esc(v.title) + '</div>' +
      (meta.length ? '<div class="detail-meta">' + meta.join(" | ") + '</div>' : "") +
      (v.description ? '<div class="detail-desc">' + esc(v.description) + '</div>' : "") +
      '<button class="play-btn" onclick="openPlayerAndPlay(' + v.id + ')">▶ Play</button>' +
      epHtml + '</div></div>';
    autoFocusView();
  } catch (e) {
    el.innerHTML = '<div class="error-view">Load failed</div>';
  }
}

/* -- Player -- */


// Initial player setup + first play
async function openPlayerAndPlay(videoId, episode) {
  // Switch to player view
  document.querySelectorAll(".view").forEach(v => { v.classList.remove("active"); v.classList.add("hidden"); });
  const el = document.getElementById("view-player");
  el.classList.remove("hidden");
  el.classList.add("active");
  _currentView = "player";
  _playId = videoId;
  _playEp = episode || 1;

  // Show loading
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';

  // Fetch episodes list
  try {
    const v = await F("/api/video/" + videoId);
    if (v && v.episodes) _playEps = v.episodes;
  } catch(e) { _playEps = []; }

  // Build player container (preserved across episode switches)
  const total = _playEps.length;
  const epIdx = _playEps.findIndex(e => e.episode_num === _playEp);
  const hasPrev = epIdx > 0;
  const hasNext = epIdx >= 0 && epIdx < total - 1;

  el.innerHTML =
    '<div class="player-bar" id="player-bar">' +
    '  <button class="player-nav-btn" id="btn-prev" onclick="switchEpisode(\'prev\')">◀ 上一集</button>' +
    '  <span class="player-nav-title" id="player-title">Loading...</span>' +
    '  <button class="player-nav-btn" id="btn-next" onclick="switchEpisode(\'next\')">下一集 ▶</button>' +
    '  <button class="player-close-btn" onclick="stopPlayerFromClose()">✕</button>' +
    '</div>' +
    '<video id="tv-video" controls autoplay playsinline preload="auto"></video>';

  const video = document.getElementById("tv-video");
  video.volume = 0.2;

  // Actually play
  await loadAndPlayUrl(videoId, episode);

  // Update button states
  updatePlayerButtons();

  // Start progress saver
  startHistoryTimer(video);

  // Auto next on ended
  video.addEventListener("ended", onVideoEnded);

  // Try fullscreen (user gesture context - this works)
  tryFullscreen(video);
}

// Switch episode without destroying/recreating the video element
async function switchEpisode(dir) {
  const idx = _playEps.findIndex(e => e.episode_num === _playEp);
  let nextEp;
  if (dir === "next" && idx >= 0 && idx < _playEps.length - 1) {
    nextEp = _playEps[idx + 1].episode_num;
  } else if (dir === "prev" && idx > 0) {
    nextEp = _playEps[idx - 1].episode_num;
  } else {
    return;
  }
  _playEp = nextEp;
  updatePlayerButtons();
  document.getElementById("player-title").textContent = "Loading...";
  await loadAndPlayUrl(_playId, _playEp);
  updatePlayerButtons();

  const video = document.getElementById("tv-video");
  if (video) {
    // Don't focus video — that keeps native controls permanently visible.
    // Keyboard seeking works via the document keydown handler instead.
    if (video.requestFullscreen) {
      video.requestFullscreen().catch(() => {});
    } else if (video.webkitRequestFullscreen) {
      video.webkitRequestFullscreen();
    }
  }
}

// Load URL and swap video src (preserves fullscreen)
async function loadAndPlayUrl(videoId, episode) {
  let url = "/api/video/" + videoId + "/play";
  if (episode) url += "?episode=" + episode;

  try {
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!data.success) return;

    document.getElementById("player-title").textContent = data.episode_title || ("Ep." + episode);

    const video = document.getElementById("tv-video");
    if (!video) return;

    // Destroy old HLS
    if (_hlsInstance) {
      _hlsInstance.destroy();
      _hlsInstance = null;
    }

    // Load new source
    if (typeof Hls !== "undefined" && Hls.isSupported() && data.play_url.indexOf(".m3u8") > 0) {
      const hls = new Hls();
      _hlsInstance = hls;
      hls.loadSource(data.play_url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
    } else {
      video.src = data.play_url;
      video.play().catch(() => {});
    }
  } catch (e) {
    document.getElementById("player-title").textContent = "Play failed";
  }
}

function updatePlayerButtons() {
  const idx = _playEps.findIndex(e => e.episode_num === _playEp);
  const total = _playEps.length;
  const prev = document.getElementById("btn-prev");
  const next = document.getElementById("btn-next");
  if (prev) prev.disabled = idx <= 0;
  if (next) next.disabled = idx < 0 || idx >= total - 1;
}

function onVideoEnded() {
  const idx = _playEps.findIndex(e => e.episode_num === _playEp);
  if (idx >= 0 && idx < _playEps.length - 1) {
    setTimeout(() => switchEpisode("next"), 1500);
  }
}

function startHistoryTimer(video) {
  if (_playerTimer) clearInterval(_playerTimer);
  _playerTimer = setInterval(() => {
    saveProgress(video);
  }, 10000);
}

function saveProgress(video) {
  if (!video || video.paused || !video.currentTime) return;
  fetch("/api/history", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: _playId,
      episode_id: _playEp,
      progress_seconds: Math.floor(video.currentTime),
      total_seconds: Math.floor(video.duration || 0)
    })
  }).catch(() => {});
}

function tryFullscreen(el) {
  if (!el) return;
  if (el.requestFullscreen) {
    el.requestFullscreen().catch(() => {});
  } else if (el.webkitRequestFullscreen) {
    el.webkitRequestFullscreen();
  }
}

function stopPlayerInternal(saveProgressNow) {
  if (_playerTimer) { clearInterval(_playerTimer); _playerTimer = null; }
  const video = document.getElementById("tv-video");
  if (saveProgressNow && video) saveProgress(video);
  if (video) { video.pause(); video.src = ""; video.load(); video.remove(); }
  if (_hlsInstance) { _hlsInstance.destroy(); _hlsInstance = null; }
  _playEps = [];
}

function stopPlayerFromClose() {
  stopPlayerInternal(true);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else if (document.webkitFullscreenElement) document.webkitExitFullscreen();
  if (_playId) navigateTo("detail", _playId);
  else navigateTo("home");
}

/* -- Search -- */
function showSearch() {
  document.getElementById("search-overlay").classList.remove("hidden");
  document.getElementById("search-results").innerHTML = "";
  document.getElementById("search-hint").classList.remove("hidden");
  const i = document.getElementById("search-input");
  i.value = ""; i.focus();
  _searchFocused = true;
}
function hideSearch() { document.getElementById("search-overlay").classList.add("hidden"); _searchFocused = false; }
function onSearchInput(val) {
  clearTimeout(_searchTimer);
  document.getElementById("search-hint").classList.add("hidden");
  document.getElementById("search-results").innerHTML = '<div class="loading"><div class="spinner"></div>Searching...</div>';
  _searchPage = 1;
  _searchTimer = setTimeout(() => doSearch(val, 1), 500);
}
async function doSearch(q, page) {
  try {
    const data = await F("/api/search?q=" + encodeURIComponent(q) + "&page=" + page);
    const el = document.getElementById("search-results");
    if (!data.results || !data.results.length) { el.innerHTML = '<div class="empty-view">No results</div>'; return; }
    let html = "";
    for (const v of data.results) html += card(v);
    el.innerHTML = html;
  } catch (e) {}
}

/* -- Utilities -- */
function imageFallback(w, h) {
  w = w || 240; h = h || 320;
  return 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22' + w + '%22 height=%22' + h + '%22%3E%3Crect fill=%22%23222%22/%3E%3C/svg%3E';
}

async function F(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function card(v) {
  const badge = { movie: "Movie", tv: "TV", variety: "Variety", anime: "Anime" }[v.type] || "";
  const fb = imageFallback();
  return '<div class="video-card" tabindex="0" onclick="hideSearch();navigateTo(\'detail\',' + v.id + ')">' +
    '<img class="card-img" src="' + (v.cover || fb) + '" loading="lazy" onerror="this.src=\'' + fb + '\'">' +
    '<div class="card-info"><div class="card-title">' + esc(v.title) + '</div>' +
    '<div class="card-sub">' + (badge ? '<span class="card-badge">' + badge + "</span>" : "") + (v.year ? "<span>" + v.year + "</span>" : "") + (v.rating ? '<span>⭐' + v.rating + "</span>" : "") + "</div></div></div>";
}

/* -- History -- */
async function loadHistory() {
  const el = document.getElementById("view-history");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';
  try {
    const data = await F("/api/history?limit=100");
    if (!data || !data.length) { el.innerHTML = '<div class="empty-view">暂无观看记录</div>'; return; }
    let html = '<div class="card-grid">';
    for (const h of data) {
      const label = h.episode_id ? "第" + h.episode_id + "集" : "电影";
      const onClick = "hideSearch();navigateTo('detail'," + h.video_id + ")";
      const fb = imageFallback();
      html += '<div class="video-card" tabindex="0" onclick="' + onClick + '">' +
        '<img class="card-img" src="' + (h.cover || fb) + '" loading="lazy" onerror="this.src=\'' + fb + '\'">' +
        '<div class="card-info"><div class="card-title">' + esc(h.title || '') + '</div>' +
        '<div class="card-sub"><span class="card-badge">' + label + '</span>' +
        (h.progress_seconds ? '<span>' + Math.round(h.progress_seconds / 60) + 'min</span>' : '') +
        '</div></div></div>';
    }
    html += '</div>';
    el.innerHTML = html;
    autoFocusView();
  } catch(e) {
    el.innerHTML = '<div class="error-view">加载失败</div>';
  }
}

/* -- Keyboard -- */
document.addEventListener("keydown", function(e) {
  if (_searchFocused) {
    if (e.key === "Escape") { hideSearch(); e.preventDefault(); }
    return;
  }

  // ── Player view ──
  if (_currentView === "player") {
    if (e.key === "Escape" || e.key === "Backspace") {
      e.preventDefault();
      if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
      if (document.webkitFullscreenElement) { document.webkitExitFullscreen(); return; }
      stopPlayerInternal(true);
      if (_playId) navigateTo("detail", _playId);
      else navigateTo("home");
      return;
    }
    // Arrow keys on buttons → navigate buttons
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const cur = document.activeElement;
      if (cur && cur.closest(".player-bar")) {
        e.preventDefault();
        const btns = Array.from(document.querySelectorAll(".player-nav-btn:not([disabled]), .player-close-btn"));
        const idx = btns.indexOf(cur);
        if (e.key === "ArrowLeft" && idx > 0) btns[idx - 1].focus();
        if (e.key === "ArrowRight" && idx < btns.length - 1) btns[idx + 1].focus();
        return;
      }
      // Not on a button → seek video
      e.preventDefault();
      const video = document.getElementById("tv-video");
      if (video && video.duration) {
        video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + (e.key === "ArrowLeft" ? -10 : 10)));
      }
      return;
    }
    return;
  }

  // ── Normal views (home / browse / detail / history) ──
  const cur = document.activeElement;
  const onNav = cur && cur.closest("#nav");

  // Left/Right: on nav → cycle tabs, on content → navigate content
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    if (onNav) {
      cycleNav(e.key === "ArrowLeft" ? -1 : 1);
    } else {
      moveFocus(e.key === "ArrowLeft" ? "left" : "right");
    }
    return;
  }

  // Up/Down
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    e.preventDefault();
    if (onNav) {
      if (e.key === "ArrowDown") focusFirstInView();
      // Up on nav → no-op (already at top)
      return;
    }
    if (e.key === "ArrowDown") {
      moveFocus("down");
    } else {
      // ArrowUp from content: moveFocus already includes nav buttons
      // as candidates, so at the top row it naturally jumps to nav
      moveFocus("up");
    }
    return;
  }

  switch (e.key) {
    case "Enter":      if (cur) cur.click(); break;
    case "Escape": case "Backspace": e.preventDefault(); if (_currentView === "detail") navigateTo("home"); break;
    case "f": case "F": showSearch(); break;
  }
});

function cycleNav(dir) {
  const btns = document.querySelectorAll("#nav .nav-btn");
  if (!btns.length) return;
  _navCycling++;   // 阻止 autoFocusView 抢焦点(计数器)
  let cur = document.querySelector("#nav .nav-btn.active") || btns[0];
  let i = Array.from(btns).indexOf(cur);
  let next = (i + dir + btns.length) % btns.length;
  let btn = btns[next];
  btn.focus();
  let view = btn.getAttribute("data-view");
  let type = btn.getAttribute("data-type");
  if (view) navigateTo(view, type);
}

function focusFirstInView() {
  const view = document.querySelector(".view.active");
  if (!view) return;
  const first = view.querySelector(".video-card, .play-btn, .episode-btn, .section-more, .browse-tab, .browse-prev, .browse-next");
  if (first) first.focus();
}

function moveFocus(dir) {
  const view = document.querySelector(".view.active");
  if (!view) return;
  const items = Array.from(view.querySelectorAll(
    ".video-card, .play-btn, .episode-btn, .section-more, .browse-tab, .browse-prev, .browse-next"
  ));
  if (dir === "up") {
    const navItems = Array.from(document.querySelectorAll("#nav .nav-btn, .search-btn"));
    items.unshift(...navItems);
  }
  if (!items.length) return;

  let idx = items.indexOf(document.activeElement);
  if (idx < 0) { items[0].focus(); return; }

  const r = document.activeElement.getBoundingClientRect();
  let best = -1, bestDist = Infinity;
  for (let i = 0; i < items.length; i++) {
    if (i === idx) continue;
    const rr = items[i].getBoundingClientRect();
    let dx, dy, ok = false;
    if (dir === "down")  { dy = rr.top - r.bottom; dx = Math.abs(rr.left - r.left);   ok = dy >= -10; }
    if (dir === "up")    { dy = r.top - rr.bottom;   dx = Math.abs(rr.left - r.left);   ok = dy >= -10; }
    if (dir === "left")  { dx = r.left - rr.right;   dy = Math.abs(rr.top - r.top);     ok = dx >= -10; }
    if (dir === "right") { dx = rr.left - r.right;   dy = Math.abs(rr.top - r.top);     ok = dx >= -10; }
    if (!ok) continue;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < bestDist) { best = i; bestDist = d; }
  }
  if (best >= 0) {
    // Moving up onto a nav button? Redirect to the currently active tab
    if (dir === "up" && items[best].closest("#nav")) {
      const active = document.querySelector("#nav .nav-btn.active");
      if (active) { active.focus(); return; }
    }
    items[best].focus();
  }
}

/* -- Fullscreen exit → no-op, don't refocus video (that keeps controls visible) -- */

/* -- 遥控器适配: 监听 popstate 做内部导航 + heartbeat 防栈空 -- */
// 每次导航后更新 URL 方便调试 (纯 informativ, 实际靠 popstate + heartbeat)
var _navOrig = navigateTo;
navigateTo = function(view, param) {
  _navOrig(view, param);
  history.replaceState({v: view, p: param}, '', location.pathname + '#' + view + (param ? '/' + param : ''));
};

// popstate: 遥控回退键触发 → 应用内后退
window.addEventListener("popstate", function(e) {
  // 1. 立即压回一个状态, 防止浏览器导航离开
  var ref = {v: _currentView, p: (navigateTo._lastParam)};
  window.history.pushState(ref, '', window.location.href);
  // 2. 执行应用内后退
  if (_currentView === "player") {
    stopPlayerInternal(true);
    if (_playId) navigateTo("detail", _playId);
    else navigateTo("home");
  } else if (_currentView === "detail") {
    navigateTo("home");
  }
});

// heartbeat: 每 500ms 压入一个状态, 确保 history 栈永不空
setInterval(function() {
  var v = _currentView;
  if (v) {
    window.history.pushState({heartbeat: Date.now()}, '', window.location.href);
  }
}, 500);

// 右键菜单屏蔽 (蓝牙设置键)
window.addEventListener("contextmenu", function(e) { e.preventDefault(); });

/* -- Start -- */
window.location.hash = '#home';
navigateTo("home");

(async function() {
  try {
    const st = await F("/api/crawl/status");
    document.getElementById("status").textContent = st.progress || "";
  } catch (e) {}
})();
