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

function esc(s) {
  if (!s) return "";
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function navigateTo(view, param) {
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

async function loadBrowsePage() {
  const el = document.getElementById("browse-content");
  try {
    const data = await F("/api/browse?type=" + _currentType + "&page=" + _browsePage);
    if (!data.results || !data.results.length) { el.innerHTML = '<div class="empty-view">No data</div>'; return; }
    let html = '<div class="card-grid">';
    for (const v of data.results) html += card(v);
    html += '</div>';
    el.innerHTML = html;
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
        epHtml += '<button class="episode-btn" onclick="playVideo(' + v.id + ',' + ep.episode_num + ')">' + (ep.episode_title || "Ep." + ep.episode_num) + '</button>';
      }
      epHtml += '</div>';
    }

    el.innerHTML =
      '<div class="detail-layout">' +
      '<div class="detail-poster"><img src="' + (v.cover || "") + '" onerror="this.src=\'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22360%22 height=%22480%22><rect fill=%22%23222%22/></svg>\'"></div>' +
      '<div class="detail-info">' +
      '<div class="detail-title">' + esc(v.title) + '</div>' +
      (meta.length ? '<div class="detail-meta">' + meta.join(" | ") + '</div>' : "") +
      (v.description ? '<div class="detail-desc">' + esc(v.description) + '</div>' : "") +
      '<button class="play-btn" onclick="playVideo(' + v.id + ')">▶ Play</button>' +
      epHtml + '</div></div>';
  } catch (e) {
    el.innerHTML = '<div class="error-view">Load failed</div>';
  }
}

/* -- Player -- */
async function playVideo(videoId, episode) {
  const el = document.getElementById("view-player");
  document.querySelectorAll(".view").forEach(v => { v.classList.remove("active"); v.classList.add("hidden"); });
  el.classList.remove("hidden");
  el.classList.add("active");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';

  _playId = videoId;
  _playEp = episode || 1;

  // 获取剧集列表
  try {
    const v = await F("/api/video/" + videoId);
    if (v && v.episodes) _playEps = v.episodes;
  } catch(e) { _playEps = []; }

  let url = "/api/video/" + videoId + "/play";
  if (episode) url += "?episode=" + episode;

  try {
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!data.success) { el.innerHTML = '<div class="error-view">Play failed</div>'; return; }

    const total = _playEps.length;
    const epIdx = _playEps.findIndex(e => e.episode_num === _playEp);
    const hasPrev = epIdx > 0;
    const hasNext = epIdx >= 0 && epIdx < total - 1;

    el.innerHTML =
      '<div class="player-bar">' +
      '  <button class="player-nav-btn" id="btn-prev" ' + (hasPrev ? 'onclick="playPrev()"' : 'disabled') + '>◀ 上一集</button>' +
      '  <span class="player-nav-title">' + esc(data.episode_title || ("Ep." + (_playEp))) + '</span>' +
      '  <button class="player-nav-btn" id="btn-next" ' + (hasNext ? 'onclick="playNext()"' : 'disabled') + '>下一集 ▶</button>' +
      '  <button class="player-close-btn" onclick="stopPlayer()">✕</button>' +
      '</div>' +
      '<video id="tv-video" controls autoplay playsinline preload="auto"></video>';

    const video = document.getElementById("tv-video");
    video.volume = 0.2;

    if (_hlsInstance) _hlsInstance.destroy();
    if (typeof Hls !== "undefined" && Hls.isSupported() && data.play_url.indexOf(".m3u8") > 0) {
      const hls = new Hls();
      _hlsInstance = hls;
      hls.loadSource(data.play_url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play());
    } else {
      video.src = data.play_url;
    }

    if (_playerTimer) clearInterval(_playerTimer);
    _playerTimer = setInterval(() => {
      if (video && !video.paused && video.currentTime > 0) {
        fetch("/api/history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video_id: videoId, episode_id: _playEp, progress_seconds: video.currentTime, total_seconds: video.duration || 0 })
        }).catch(() => {});
      }
    }, 10000);

    // 自动下一集
    video.addEventListener("ended", () => {
      if (hasNext) {
        setTimeout(() => { playNext(); }, 1500);
      }
    });

    setTimeout(() => {
      const vv = document.getElementById("tv-video");
      if (vv) { vv.focus(); if (vv.requestFullscreen) vv.requestFullscreen().then(()=>vv.focus()).catch(()=>{}); else if (vv.webkitRequestFullscreen) vv.webkitRequestFullscreen(); }
    }, 200);
  } catch (e) {
    el.innerHTML = '<div class="error-view">' + e.message + '</div>';
  }
}

function playNext() {
  const idx = _playEps.findIndex(e => e.episode_num === _playEp);
  if (idx >= 0 && idx < _playEps.length - 1) {
    playVideo(_playId, _playEps[idx + 1].episode_num);
  }
}

function playPrev() {
  const idx = _playEps.findIndex(e => e.episode_num === _playEp);
  if (idx > 0) {
    playVideo(_playId, _playEps[idx - 1].episode_num);
  }
}

function stopPlayer() {
  if (_playerTimer) { clearInterval(_playerTimer); _playerTimer = null; }
  const v = document.getElementById("tv-video");
  if (v) { v.pause(); v.src = ""; v.load(); v.remove(); }
  if (_hlsInstance) { _hlsInstance.destroy(); _hlsInstance = null; }
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
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
async function F(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function card(v) {
  const badge = { movie: "Movie", tv: "TV", variety: "Variety", anime: "Anime" }[v.type] || "";
  return '<div class="video-card" tabindex="0" onclick="hideSearch();navigateTo(\'detail\',' + v.id + ')">' +
    '<img class="card-img" src="' + (v.cover || "") + '" loading="lazy" onerror="this.src=\'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22240%22 height=%22320%22><rect fill=%22%23222%22/></svg>\'">' +
    '<div class="card-info"><div class="card-title">' + esc(v.title) + '</div>' +
    '<div class="card-sub">' + (badge ? '<span class="card-badge">' + badge + "</span>" : "") + (v.year ? "<span>" + v.year + "</span>" : "") + (v.rating ? '<span>⭐' + v.rating + "</span>" : "") + "</div></div></div>";
}

/* -- History -- */
function loadHistory() {
  const el = document.getElementById("view-history");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';
  F("/api/history?limit=100").then(function(data) {
    if (!data || !data.length) { el.innerHTML = '<div class="empty-view">暂无观看记录</div>'; return; }
    var html = '<div class="card-grid">';
    data.forEach(function(h) {
      var label = h.episode_id ? "第" + h.episode_id + "集" : "电影";
      var onclick = "hideSearch();navigateTo('detail'," + h.video_id + ")";
      html += '<div class="video-card" tabindex="0" onclick="' + onclick + '">' +
        '<img class="card-img" src="' + (h.cover || "") + '" loading="lazy">' +
        '<div class="card-info"><div class="card-title">' + esc(h.title || '') + '</div>' +
        '<div class="card-sub"><span class="card-badge">' + label + '</span>' +
        (h.progress_seconds ? '<span>' + Math.round(h.progress_seconds / 60) + 'min</span>' : '') +
        '</div></div></div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }).catch(function() { el.innerHTML = '<div class="error-view">加载失败</div>'; });
}

/* -- Keyboard -- */
document.addEventListener("keydown", function(e) {
  if (_searchFocused) {
    if (e.key === "Escape") { hideSearch(); e.preventDefault(); }
    return;
  }

  if (_currentView === "player") {
    if (e.key === "Escape" || e.key === "Backspace") {
      stopPlayer();
      e.preventDefault();
      return;
    }
    // 方向键在播放器按钮间导航
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      var btns = document.querySelectorAll(".player-nav-btn:not([disabled]), .player-close-btn");
      var cur = document.activeElement;
      var idx = Array.from(btns).indexOf(cur);
      if (idx < 0) idx = -1;
      if (e.key === "ArrowLeft") { btns[Math.max(0, idx - 1)].focus(); }
      if (e.key === "ArrowRight") { btns[Math.min(btns.length - 1, idx + 1)].focus(); }
      return;
    }
    return; // 其他键放行给video
  }

  const a = document.activeElement;
  switch (e.key) {
    case "ArrowUp": e.preventDefault(); moveFocus("up"); break;
    case "ArrowDown": e.preventDefault(); moveFocus("down"); break;
    case "ArrowLeft": e.preventDefault(); if (a && a.closest(".card-grid.scroll-x")) moveFocus("left"); else cycleNav(-1); break;
    case "ArrowRight": e.preventDefault(); if (a && a.closest(".card-grid.scroll-x")) moveFocus("right"); else cycleNav(1); break;
    case "Enter": if (a) a.click(); break;
    case "Escape": case "Backspace": e.preventDefault(); if (_currentView === "detail") navigateTo("home"); break;
    case "f": case "F": showSearch(); break;
  }
});

function cycleNav(dir) {
  const btns = document.querySelectorAll(".nav-btn");
  let i = Array.from(btns).indexOf(document.querySelector(".nav-btn.active"));
  btns[(i + dir + btns.length) % btns.length].focus();
}

function moveFocus(dir) {
  const items = document.querySelectorAll(".video-card, .nav-btn, .search-btn, .play-btn, .episode-btn, .section-more, .player-nav-btn, .player-close-btn");
  if (!items.length) return;
  let idx = Array.from(items).indexOf(document.activeElement);
  if (idx < 0) { items[0].focus(); return; }
  const r = document.activeElement.getBoundingClientRect();
  let best = -1, bestDist = Infinity;
  for (let i = 0; i < items.length; i++) {
    if (i === idx) continue;
    const rr = items[i].getBoundingClientRect();
    let dx, dy, ok = false;
    if (dir === "down") { dy = rr.top - r.bottom; dx = Math.abs(rr.left - r.left); ok = dy >= -10; }
    if (dir === "up") { dy = r.top - rr.bottom; dx = Math.abs(rr.left - r.left); ok = dy >= -10; }
    if (dir === "left") { dx = r.left - rr.right; dy = Math.abs(rr.top - r.top); ok = dx >= -10; }
    if (dir === "right") { dx = rr.left - r.right; dy = Math.abs(rr.top - r.top); ok = dx >= -10; }
    if (!ok) continue;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < bestDist) { best = i; bestDist = d; }
  }
  if (best >= 0) items[best].focus();
}

/* -- Fullscreen exit = go back to player view (not stop) -- */
document.addEventListener("fullscreenchange", function() {
  if (_currentView === "player" && !document.fullscreenElement) {
    // 聚焦回播放器界面
    const video = document.getElementById("tv-video");
    if (video) video.focus();
  }
});
document.addEventListener("webkitfullscreenchange", function() {
  if (_currentView === "player" && !document.webkitFullscreenElement) {
    const video = document.getElementById("tv-video");
    if (video) video.focus();
  }
});

/* -- Start -- */
navigateTo("home");

(async function() {
  try {
    const st = await F("/api/crawl/status");
    document.getElementById("status").textContent = st.progress || "";
  } catch (e) {}
})();
