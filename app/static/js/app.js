/* TV Media Center */
let _currentView = "home";
let _currentType = "";
let _searchTimer = null;
let _searchFocused = false;
let _searchResultsActive = false; // 搜索结果浏览模式
let _browsePage = 1;
let _searchPage = 1;
let _hlsInstance = null;
let _playerTimer = null;
let _playId = 0;       // current video id
let _playEp = 0;       // current episode number
let _playEps = [];     // episode list [{num, title}]
let _navCycling = 0; // 导航栏切换Tab时阻止autoFocusView抢焦点(计数器,支持连续快速切换)
let _currentUrl = "";          // 当前播放地址
let _currentSourceName = "";   // 当前播放源名称
let _diagVisible = false;      // 诊断面板是否显示
let _diagTimer = null;         // 诊断面板刷新定时器
let _bufferSince = 0;          // 本次缓冲开始时间戳
let _speedSamples = [];        // 网速滑动窗口
let _lastSpeed = 0;            // 最近平均网速 (bytes/s)
let _loadTimer = null;         // 播放加载超时定时器
let _hlsRetryCount = 0;        // HLS 错误重试次数
let _playFailed = false;       // 播放失败（等待 Enter 重试）
let _hlsBytes = 0;             // 当前 HLS 源累计下载字节
let _startSeconds = 0;         // 续播起始秒数
let _heroData = null;          // 当前首页 Hero 数据

function esc(s) {
  if (!s) return "";
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function navigateTo(view, param) {
  // Hide search overlay when navigating
  hideSearch();
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
    const first = view.querySelector(".hero-card, .video-card, .play-btn, .episode-btn, .section-more, .browse-tab");
    if (first) focusWithScroll(first);
  }, 50);
}

async function loadHome() {
  const el = document.getElementById("view-home");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>加载中…</div>';
  try {
    const data = await F("/api/home");
    if (!data.sections || !data.sections.length) {
      el.innerHTML = '<div class="empty-view">暂无内容</div>';
      return;
    }
    _heroData = data.hero || null;
    let html = "";
    if (_heroData) html += heroHtml(_heroData);
    for (const sec of data.sections) html += sectionHtml(sec, 999);
    el.innerHTML = html;
    // 按当前分辨率测量一行能放几列，再按列数重排（保证单行满宽不换行）
    const cols = measureCols(document.querySelector("#view-home .card-grid"));
    if (cols > 0) {
      html = "";
      if (_heroData) html += heroHtml(_heroData);
      for (const sec of data.sections) html += sectionHtml(sec, cols);
      el.innerHTML = html;
    }
    autoFocusView();
  } catch (e) {
    el.innerHTML = '<div class="error-view">加载失败<button class="retry-btn" onclick="loadHome()">重试</button></div>';
  }
}

function heroHtml(h) {
  const pct = h.total_seconds > 0 ? Math.max(0, Math.min(100, Math.round(h.progress_seconds / h.total_seconds * 100))) : 0;
  let sub = "最近更新";
  if (h.kind === "continue") {
    sub = h.episode_num ? "第" + h.episode_num + "集" : "电影";
    if (h.episode_title) sub += " · " + h.episode_title;
    sub += " · 已看 " + pct + "%";
  }
  const action = h.kind === "continue" ? "继续观看" : "查看详情";
  return '<div class="hero-card" tabindex="0" onclick="heroClick(' + h.video_id + ')">' +
    '<div class="hero-bg" style="background-image:url(\'' + escAttr(h.cover) + '\')"></div>' +
    '<div class="hero-mask"></div>' +
    '<div class="hero-info">' +
    '<div class="hero-badge">' + (h.kind === "continue" ? "继续观看" : "最近更新") + '</div>' +
    '<div class="hero-title">' + esc(h.title) + '</div>' +
    '<div class="hero-sub">' + esc(sub) + '</div>' +
    (h.kind === "continue" && h.total_seconds > 0
      ? '<div class="hero-progress"><div class="hero-progress-fill" style="width:' + pct + '%"></div></div>'
      : "") +
    '<button class="hero-btn">' + action + '</button>' +
    '</div></div>';
}

function heroClick(videoId) {
  const h = _heroData || {};
  if (h.kind === "continue" && h.video_id === videoId) {
    openPlayerAndPlay(videoId, h.episode_num || undefined, h.progress_seconds || 0);
  } else {
    navigateTo("detail", videoId);
  }
}

function sectionHtml(sec, cols) {
  const items = (sec.videos || []).slice(0, cols || 20);
  const hideMore = sec.type === "recommend" || sec.type === "hot" || sec.type === "score";
  let html = '<div class="section">' +
    '<div class="section-header">' +
    '<div class="section-title">' + esc(sec.name) + '</div>' +
    (hideMore ? "" : '<button class="section-more" onclick="navigateTo(\'browse\',\'' + sec.type + '\')">查看全部 ›</button>') +
    '</div><div class="card-grid">';
  for (const v of items) html += card(v);
  html += '</div></div>';
  return html;
}

function measureCols(grid) {
  if (!grid) return 0;
  const cards = grid.querySelectorAll(".video-card");
  if (!cards.length) return 0;
  const firstTop = cards[0].getBoundingClientRect().top;
  let cols = 1;
  for (let i = 1; i < cards.length; i++) {
    if (cards[i].getBoundingClientRect().top > firstTop + 4) break;
    cols++;
  }
  return cols;
}

function fitGridToRow(grid) {
  if (!grid) return;
  const cols = measureCols(grid);
  if (cols <= 0) return;
  const cards = grid.querySelectorAll(".video-card");
  for (let i = cols; i < cards.length; i++) cards[i].remove();
}

function toggleDesc() {
  const d = document.getElementById("detail-desc");
  const b = document.getElementById("desc-toggle");
  if (!d || !b) return;
  const expanded = d.classList.toggle("expanded");
  b.textContent = expanded ? "收起" : "展开";
}

function focusWithScroll(el) {
  if (!el) return;
  el.focus();
  try { el.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (e) {}
}

async function loadBrowse(type) {
  _browsePage = 1;
  _currentType = type;
  document.getElementById("view-browse").innerHTML = '<div id="browse-content"><div class="loading"><div class="spinner"></div>加载中…</div></div>';
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
      el.innerHTML = '<div class="empty-view">暂无内容</div>';
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
    el.innerHTML = '<div class="error-view">加载失败</div>';
  }
}

async function loadDetail(videoId) {
  const el = document.getElementById("view-detail");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>加载中…</div>';
  try {
    const v = await F("/api/video/" + videoId);
    if (!v || !v.id) { el.innerHTML = '<div class="empty-view">未找到该影片</div>'; return; }

    let meta = [];
    const tn = { movie: "电影", tv: "剧集", variety: "综艺", anime: "动漫" };
    if (v.type) meta.push(tn[v.type] || v.type);
    if (v.year) meta.push(v.year);
    if (v.rating && v.rating > 0) meta.push("⭐ " + Number(v.rating).toFixed(1));

    let epHtml = "";
    if (v.episodes && v.episodes.length && v.type !== "movie") {
      epHtml = '<div class="episodes-title">剧集</div><div class="episode-grid">';
      for (const ep of v.episodes) {
        epHtml += '<button class="episode-btn" onclick="openPlayerAndPlay(' + v.id + ',' + ep.episode_num + ')">' + (ep.episode_title || "第" + ep.episode_num + "集") + '</button>';
      }
      epHtml += '</div>';
    }

    let mainHtml =
      '<div class="detail-layout">' +
      '<div class="detail-poster">' +
      '<div class="detail-poster-ph">' + esc(v.title) + '</div>' +
      '<img src="' + escAttr(v.cover || "") + '" onerror="this.style.display=\'none\'">' +
      '</div>' +
      '<div class="detail-info">' +
      '<div class="detail-title">' + esc(v.title) + '</div>' +
      (meta.length ? '<div class="detail-meta">' + meta.join(" | ") + '</div>' : "") +
      (v.description ? '<div class="detail-desc" id="detail-desc">' + esc(v.description) + '</div><button class="desc-toggle" id="desc-toggle" onclick="toggleDesc()">展开</button>' : "") +
      '<button class="play-btn" onclick="openPlayerAndPlay(' + v.id + ')">▶ 播放</button>' +
      epHtml + '</div></div>';

    let relatedHtml = "";
    if (v.related && v.related.length) {
      relatedHtml = '<div class="section"><div class="section-header"><div class="section-title">猜你喜欢</div></div><div class="card-grid">';
      for (const rv of v.related) relatedHtml += card(rv);
      relatedHtml += '</div></div>';
    }
    el.innerHTML = mainHtml + relatedHtml;
    fitGridToRow(document.querySelector("#view-detail .card-grid"));
    autoFocusView();
  } catch (e) {
    el.innerHTML = '<div class="error-view">加载失败</div>';
  }
}

/* -- Player -- */


// Initial player setup + first play
/* -- 缓冲诊断 -- */
const BUFFER_LOG_KEY = "tv_buffer_log";
const SPEED_SAMPLES_MAX = 5;

function formatSpeed(bps) {
  if (!bps || bps <= 0) return "—";
  if (bps >= 1048576) return (bps / 1048576).toFixed(1) + " MB/s";
  if (bps >= 1024) return (bps / 1024).toFixed(0) + " KB/s";
  return bps.toFixed(0) + " B/s";
}

function recordSpeedSample(bytes, now) {
  const prev = _speedSamples[_speedSamples.length - 1];
  if (!prev) { _speedSamples.push({ bytes: bytes, now: now }); return; }
  const dt = (now - prev.now) / 1000;
  if (dt <= 0.2) return;
  const bps = Math.max(0, (bytes - prev.bytes) / dt);
  _speedSamples.push({ bytes: bytes, now: now, bps: bps });
  if (_speedSamples.length > SPEED_SAMPLES_MAX) _speedSamples.shift();
  const recent = _speedSamples.filter(s => s.bps !== undefined);
  _lastSpeed = recent.length ? recent.reduce((a, s) => a + s.bps, 0) / recent.length : 0;
}

function getBufferLog() {
  try { return JSON.parse(localStorage.getItem(BUFFER_LOG_KEY) || "[]"); } catch (e) { return []; }
}

function saveBufferLog(log) {
  try { localStorage.setItem(BUFFER_LOG_KEY, JSON.stringify(log.slice(-50))); } catch (e) {}
}

function recordBufferEvent(durationSec) {
  const log = getBufferLog();
  let host = "";
  try { host = new URL(_currentUrl).host; } catch (e) {}
  log.push({
    t: Date.now(),
    url: _currentUrl,
    host: host,
    source: _currentSourceName,
    speed: Math.round(_lastSpeed),
    duration: Math.round(durationSec),
  });
  saveBufferLog(log);
}

function ensureDiagElements() {
  let osd = document.getElementById("buffer-osd");
  if (!osd) {
    osd = document.createElement("div");
    osd.id = "buffer-osd";
    osd.className = "buffer-osd hidden";
    osd.innerHTML = '<span class="buffer-osd-label">缓冲中…</span><span class="buffer-osd-speed" id="buffer-osd-speed"></span>';
    document.body.appendChild(osd);
  }
  let panel = document.getElementById("diag-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "diag-panel";
    panel.className = "diag-panel hidden";
    panel.innerHTML =
      '<div class="diag-title">播放诊断</div>' +
      '<div class="diag-row"><span>播放源</span><span id="diag-host">—</span></div>' +
      '<div class="diag-row"><span>网速</span><span id="diag-speed">—</span></div>' +
      '<div class="diag-row"><span>缓冲提前</span><span id="diag-buffer">—</span></div>' +
      '<div class="diag-row"><span>清晰度 / 码率</span><span id="diag-level">—</span></div>' +
      '<div class="diag-row"><span>进度</span><span id="diag-progress">—</span></div>' +
      '<div class="diag-title">最近缓冲</div>' +
      '<div id="diag-events" class="diag-events">暂无缓冲记录</div>';
    document.body.appendChild(panel);
  }
  // 全屏的是 #player-stage，OSD/面板必须挂到它下面，全屏时才可见
  const stage = document.getElementById("player-stage");
  if (stage) {
    stage.appendChild(osd);
    stage.appendChild(panel);
  }
}

function showBufferOSD(text) {
  ensureDiagElements();
  document.getElementById("buffer-osd").classList.remove("hidden");
  document.getElementById("buffer-osd-speed").textContent = text !== undefined ? text : formatSpeed(_lastSpeed);
}

function hideBufferOSD() {
  const el = document.getElementById("buffer-osd");
  if (el) el.classList.add("hidden");
}

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return m + ":" + String(s).padStart(2, "0");
}

function updateDiagPanel() {
  ensureDiagElements();
  const video = document.getElementById("tv-video");
  let host = "—";
  try { host = new URL(_currentUrl).host; } catch (e) {}
  document.getElementById("diag-host").textContent = host;
  document.getElementById("diag-speed").textContent = formatSpeed(_lastSpeed);
  let ahead = 0;
  if (video && video.buffered && video.buffered.length) {
    ahead = video.buffered.end(video.buffered.length - 1) - video.currentTime;
  }
  document.getElementById("diag-buffer").textContent = ahead > 0 ? ahead.toFixed(1) + " 秒" : "—";
  let levelText = "—";
  if (_hlsInstance && _hlsInstance.levels && _hlsInstance.levels.length) {
    const lv = _hlsInstance.levels[Math.max(0, _hlsInstance.currentLevel)];
    if (lv) {
      levelText = (lv.height ? lv.height + "p" : "?") + (lv.bitrate ? " · " + (lv.bitrate / 1000).toFixed(0) + " kbps" : "");
    }
  }
  document.getElementById("diag-level").textContent = levelText;
  document.getElementById("diag-progress").textContent = video ? fmtTime(video.currentTime) + " / " + fmtTime(video.duration) : "—";
  const events = getBufferLog().slice(-5).reverse();
  document.getElementById("diag-events").textContent = events.length
    ? events.map(ev => new Date(ev.t).toLocaleTimeString("zh-CN", { hour12: false }) + "  " + (ev.host || "?") + "  " + formatSpeed(ev.speed) + " 缓冲" + ev.duration + "s").join("\n")
    : "暂无缓冲记录";
}

function toggleDiagPanel() {
  ensureDiagElements();
  _diagVisible = !_diagVisible;
  const panel = document.getElementById("diag-panel");
  if (_diagVisible) {
    updateDiagPanel();
    panel.classList.remove("hidden");
    if (_diagTimer) clearInterval(_diagTimer);
    _diagTimer = setInterval(updateDiagPanel, 1000);
  } else {
    panel.classList.add("hidden");
    if (_diagTimer) { clearInterval(_diagTimer); _diagTimer = null; }
  }
}

function bindBufferEvents(video) {
  video.addEventListener("loadstart", () => showBufferOSD("加载中…"));
  video.addEventListener("waiting", () => {
    if (!_bufferSince) _bufferSince = Date.now();
    showBufferOSD();
  });
  video.addEventListener("stalled", () => {
    if (!_bufferSince) _bufferSince = Date.now();
    showBufferOSD();
  });
  const endBuffer = () => {
    hideBufferOSD();
    clearLoadTimer();
    if (_bufferSince) {
      recordBufferEvent((Date.now() - _bufferSince) / 1000);
      _bufferSince = 0;
    }
  };
  video.addEventListener("playing", endBuffer);
  video.addEventListener("canplay", endBuffer);
}

function clearLoadTimer() {
  if (_loadTimer) { clearTimeout(_loadTimer); _loadTimer = null; }
}

function startLoadTimer(video) {
  clearLoadTimer();
  _loadTimer = setTimeout(() => {
    _loadTimer = null;
    if (!video || !video.currentSrc || (!video.paused && !video.ended)) return;
    if (_hlsRetryCount < 2) {
      _hlsRetryCount++;
      showBufferOSD("加载超时，自动重试…");
      loadAndPlayUrl(_playId, _playEp);
    } else {
      _playFailed = true;
      showBufferOSD("加载超时，按 Enter 重试");
    }
  }, 20000);
}

async function retryPlay() {
  _playFailed = false;
  _hlsRetryCount = 0;
  hideBufferOSD();
  await loadAndPlayUrl(_playId, _playEp);
}

async function openPlayerAndPlay(videoId, episode, startSeconds) {
  // Switch to player view
  document.querySelectorAll(".view").forEach(v => { v.classList.remove("active"); v.classList.add("hidden"); });
  const el = document.getElementById("view-player");
  el.classList.remove("hidden");
  el.classList.add("active");
  _currentView = "player";
  _playId = videoId;
  _playEp = episode || 1;
  _startSeconds = startSeconds || 0;
  _playFailed = false;
  _hlsRetryCount = 0;
  clearLoadTimer();

  // Show loading
  el.innerHTML = '<div class="loading"><div class="spinner"></div>加载中…</div>';

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
    '  <span class="player-nav-title" id="player-title">加载中…</span>' +
    '  <button class="player-nav-btn" id="btn-next" onclick="switchEpisode(\'next\')">下一集 ▶</button>' +
    '  <button class="player-nav-btn" id="btn-fs" onclick="toggleFullscreen()">⛶</button>' +
    '  <button class="player-nav-btn" id="btn-diag" onclick="toggleDiagPanel()">ℹ</button>' +
    '  <button class="player-close-btn" onclick="stopPlayerFromClose()">✕</button>' +
    '</div>' +
    '<div id="player-stage">' +
    '<video id="tv-video" controls controlsList="nofullscreen" autoplay playsinline preload="auto"></video>' +
    '</div>';

  const video = document.getElementById("tv-video");
  video.volume = 0.2;
  ensureDiagElements();
  bindBufferEvents(video);

  // Actually play
  await loadAndPlayUrl(videoId, episode);

  // Update button states
  updatePlayerButtons();

  // Start progress saver
  startHistoryTimer(video);

  // Auto next on ended
  video.addEventListener("ended", onVideoEnded);

  // Try fullscreen (user gesture context - this works)
  // 全屏容器而不是 video 本身，保证 OSD/诊断面板在全屏时可见
  tryFullscreen(document.getElementById("player-stage") || video);
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
  document.getElementById("player-title").textContent = "加载中…";
  _playFailed = false;
  _hlsRetryCount = 0;
  _startSeconds = 0;
  await loadAndPlayUrl(_playId, _playEp);
  updatePlayerButtons();

  const stage = document.getElementById("player-stage");
  if (stage) {
    // Don't focus video — that keeps native controls permanently visible.
    // Keyboard seeking works via the document keydown handler instead.
    if (stage.requestFullscreen) {
      stage.requestFullscreen().catch(() => {});
    } else if (stage.webkitRequestFullscreen) {
      stage.webkitRequestFullscreen();
    }
  }
}

// Load URL and swap video src (preserves fullscreen)
async function loadAndPlayUrl(videoId, episode) {
  const params = [];
  if (episode) params.push("episode=" + episode);
  if (_startSeconds > 0) params.push("start_seconds=" + Math.floor(_startSeconds));
  const url = "/api/video/" + videoId + "/play" + (params.length ? "?" + params.join("&") : "");
  clearLoadTimer();
  hideBufferOSD();

  try {
    const res = await fetch(url, { method: "POST" });
    const data = await res.json();
    if (!data.success) {
      _playFailed = true;
      showBufferOSD("无法获取播放地址，按 Enter 重试");
      return;
    }
    _playFailed = false;

    document.getElementById("player-title").textContent = data.episode_title || (episode ? "第" + episode + "集" : "");
    _currentUrl = data.play_url || "";
    _currentSourceName = data.source || "";
    _speedSamples = [];
    _lastSpeed = 0;
    _bufferSince = 0;
    _hlsBytes = 0;

    const video = document.getElementById("tv-video");
    if (!video) return;

    // Destroy old HLS
    if (_hlsInstance) {
      _hlsInstance.destroy();
      _hlsInstance = null;
    }

    // Load new source
    if (typeof Hls !== "undefined" && Hls.isSupported() && data.play_url.indexOf(".m3u8") > 0) {
      const hls = new Hls({
        maxBufferLength: 60,
        maxMaxBufferLength: 300,
        backBufferLength: 30,
        enableWorker: true,
      });
      _hlsInstance = hls;
      hls.loadSource(data.play_url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
      hls.on(Hls.Events.FRAG_LOADED, (_evt, data) => {
        if (data && data.stats && data.stats.loaded) {
          _hlsBytes += data.stats.loaded;
          recordSpeedSample(_hlsBytes, performance.now());
          const osd = document.getElementById("buffer-osd");
          if (osd && !osd.classList.contains("hidden")) {
            document.getElementById("buffer-osd-speed").textContent = formatSpeed(_lastSpeed);
          }
        }
        if (_diagVisible) updateDiagPanel();
      });
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (!data || !data.fatal) return;
        clearLoadTimer();
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          try { hls.recoverMediaError(); } catch (e) {}
          return;
        }
        if (_hlsRetryCount < 3) {
          _hlsRetryCount++;
          showBufferOSD("网络波动，正在重试…");
          setTimeout(() => { try { hls.startLoad(); } catch (e) {} }, 1500);
        } else {
          _playFailed = true;
          showBufferOSD("播放失败，按 Enter 重试");
        }
      });
    } else {
      video.src = data.play_url;
      video.play().catch(() => {});
    }
    // 续播：加载完成后 seek 到上次进度
    if (_startSeconds > 0) {
      const doSeek = () => {
        try { video.currentTime = Math.min(_startSeconds, video.duration || _startSeconds); } catch (e) {}
      };
      if (video.readyState >= 1) doSeek();
      else video.addEventListener("loadedmetadata", doSeek, { once: true });
    }
    startLoadTimer(video);
  } catch (e) {
    _playFailed = true;
    showBufferOSD("网络请求失败，按 Enter 重试");
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

function toggleFullscreen() {
  const stage = document.getElementById("player-stage");
  if (!stage) return;
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  } else if (document.webkitFullscreenElement) {
    document.webkitExitFullscreen();
  } else if (stage.requestFullscreen) {
    stage.requestFullscreen().catch(() => {});
  } else if (stage.webkitRequestFullscreen) {
    stage.webkitRequestFullscreen();
  }
}

function stopPlayerInternal(saveProgressNow) {
  if (_playerTimer) { clearInterval(_playerTimer); _playerTimer = null; }
  clearLoadTimer();
  hideBufferOSD();
  _bufferSince = 0;
  _speedSamples = [];
  _lastSpeed = 0;
  _hlsBytes = 0;
  _playFailed = false;
  _hlsRetryCount = 0;
  if (_diagVisible) { _diagVisible = false; const p = document.getElementById("diag-panel"); if (p) p.classList.add("hidden"); }
  if (_diagTimer) { clearInterval(_diagTimer); _diagTimer = null; }
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
  document.getElementById("search-results").innerHTML = '<div class="loading"><div class="spinner"></div>搜索中…</div>';
  _searchPage = 1;
  _searchTimer = setTimeout(() => doSearch(val, 1), 500);
}
async function doSearch(q, page) {
  try {
    const data = await F("/api/search?q=" + encodeURIComponent(q) + "&page=" + page);
    const el = document.getElementById("search-results");
    if (!data.results || !data.results.length) { el.innerHTML = '<div class="empty-view">暂无结果</div>'; return; }
    let html = "";
    for (const v of data.results) html += card(v);
    el.innerHTML = html;
    // 结果出现后让第一个结果可被方向键定位
    setTimeout(function() {
      const first = el.querySelector(".video-card");
      if (first) first.setAttribute("data-search-result", "true");
    }, 100);
  } catch (e) {}
}

/* -- Utilities -- */

async function F(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function card(v) {
  const badge = { movie: "电影", tv: "剧集", variety: "综艺", anime: "动漫" }[v.type] || "";
  const score = (v.douban_score && v.douban_score > 0) ? v.douban_score : v.rating;
  return '<div class="video-card" tabindex="0" onclick="hideSearch();navigateTo(\'detail\',' + v.id + ')">' +
    '<div class="card-img-wrap">' +
    '<div class="card-placeholder">' + esc(v.title) + '</div>' +
    '<img class="card-img" src="' + escAttr(v.cover || "") + '" loading="lazy" onerror="this.style.display=\'none\'">' +
    '</div>' +
    '<div class="card-info"><div class="card-title">' + esc(v.title) + '</div>' +
    '<div class="card-sub">' + (badge ? '<span class="card-badge">' + badge + "</span>" : "") + (v.year ? "<span>" + v.year + "</span>" : "") + (score && score > 0 ? '<span>⭐' + Number(score).toFixed(1) + "</span>" : "") + "</div></div></div>";
}

/* -- History -- */
async function loadHistory() {
  const el = document.getElementById("view-history");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>加载中…</div>';
  try {
    const data = await F("/api/history?limit=100");
    if (!data || !data.length) { el.innerHTML = '<div class="empty-view">暂无观看记录</div>'; return; }
    let html = '<div class="card-grid">';
    for (const h of data) {
      const label = h.episode_id ? "第" + h.episode_id + "集" : "电影";
      const onClick = "hideSearch();navigateTo('detail'," + h.video_id + ")";
      html += '<div class="video-card" tabindex="0" onclick="' + onClick + '">' +
        '<div class="card-img-wrap">' +
        '<div class="card-placeholder">' + esc(h.title || '') + '</div>' +
        '<img class="card-img" src="' + escAttr(h.cover || "") + '" loading="lazy" onerror="this.style.display=\'none\'">' +
        '</div>' +
        '<div class="card-info"><div class="card-title">' + esc(h.title || '') + '</div>' +
        '<div class="card-sub"><span class="card-badge">' + label + '</span>' +
        (h.progress_seconds ? '<span>' + Math.round(h.progress_seconds / 60) + '分钟</span>' : '') +
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
    // 回退键（Backspace）在输入框内用于删除字符，不拦截
    if (e.key === "Backspace") return;
    if (e.key === "Escape") {
      e.preventDefault();
      const inp = document.getElementById("search-input");
      if (inp && inp.value) {
        // 遥控回退键映射为 Esc：优先删除最后一个字符，删空后再按才关闭
        inp.value = inp.value.slice(0, -1);
        onSearchInput(inp.value);
      } else {
        hideSearch();
      }
      return;
    }
    // 下键: 焦点移出搜索框进入结果列表 (遮罩保持打开, 用方向键浏览, Enter进入详情)
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const first = document.querySelector("#search-results .video-card");
      if (first) {
        _searchFocused = false;
        _searchResultsActive = true;
        focusWithScroll(first);
      }
      return;
    }
    // 回车: 执行搜索
    if (e.key === "Enter") {
      const inp = document.getElementById("search-input");
      if (inp && inp.value.trim()) { doSearch(inp.value.trim(), 1); }
      return;
    }
    return;
  }

  // ── Player view ──
  if (_currentView === "player") {
    if (e.key === "Escape" || e.key === "Backspace") {
      e.preventDefault();
      if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
      if (document.webkitFullscreenElement) { document.webkitExitFullscreen(); return; }
      // 诊断面板打开时先关面板，再按一次才退出播放
      if (_diagVisible) { toggleDiagPanel(); return; }
      stopPlayerInternal(true);
      if (_playId) navigateTo("detail", _playId);
      else navigateTo("home");
      return;
    }
    // 上键: 开关诊断面板
    if (e.key === "ArrowUp") {
      e.preventDefault();
      toggleDiagPanel();
      return;
    }
    // 空格 / 遥控 OK 键: 播放/暂停
    if (e.key === " " || e.key === "Spacebar" || e.code === "Space") {
      const cur = document.activeElement;
      if (cur && cur.closest && cur.closest(".player-bar")) return;
      e.preventDefault();
      const video = document.getElementById("tv-video");
      if (video) {
        if (video.paused) video.play().catch(() => {});
        else video.pause();
      }
      return;
    }
    // 回车 / 遥控 OK 键: 按钮聚焦时交给按钮，否则失败重试或播放/暂停
    if (e.key === "Enter") {
      const cur = document.activeElement;
      if (cur && cur.closest && cur.closest(".player-bar")) return;
      e.preventDefault();
      const video = document.getElementById("tv-video");
      if (_playFailed) {
        retryPlay();
      } else if (video) {
        if (video.paused) video.play().catch(() => {});
        else video.pause();
      }
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

  // ── Search results browsing mode (焦点在搜索结果卡片中) ──
  if (_searchResultsActive) {
    if (e.key === "Escape") {
      e.preventDefault();
      // 回到搜索框继续编辑，不清空已输入的关键词
      _searchResultsActive = false;
      _searchFocused = true;
      document.getElementById("search-input").focus();
      return;
    }
    const cards = Array.from(document.querySelectorAll("#search-results .video-card"));
    if (!cards.length) { _searchResultsActive = false; return; }
    const curIdx = cards.indexOf(document.activeElement);
    
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      if (curIdx < cards.length - 1) focusWithScroll(cards[curIdx + 1]);
      return;
    }
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      if (curIdx > 0) { focusWithScroll(cards[curIdx - 1]); return; }
      // 到顶部了 → 回到搜索框
      _searchResultsActive = false;
      _searchFocused = true;
      document.getElementById("search-input").focus();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      hideSearch();
      _searchResultsActive = false;
      if (curIdx >= 0 && cards[curIdx]) {
        const onclick = cards[curIdx].getAttribute("onclick");
        if (onclick) eval(onclick);
      }
      return;
    }
    return; // 拦截所有其他键
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
  const first = view.querySelector(".hero-card, .video-card, .play-btn, .episode-btn, .section-more, .browse-tab, .browse-prev, .browse-next");
  if (first) focusWithScroll(first);
}

function moveFocus(dir) {
  const view = document.querySelector(".view.active");
  if (!view) return;
  const items = Array.from(view.querySelectorAll(
    ".hero-card, .video-card, .play-btn, .episode-btn, .section-more, .browse-tab, .browse-prev, .browse-next"
  ));
  if (dir === "up") {
    const navItems = Array.from(document.querySelectorAll("#nav .nav-btn, .search-btn"));
    items.unshift(...navItems);
  }
  if (!items.length) return;

  let idx = items.indexOf(document.activeElement);
  if (idx < 0) { focusWithScroll(items[0]); return; }

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
    focusWithScroll(items[best]);
  }
}

/* -- 遥控器设置键 (鼠标右键) → 呼出搜索 -- */
window.addEventListener("contextmenu", function(e) {
  e.preventDefault();
  showSearch();
});

/* -- 回退保护: 阻止页面意外离开 -- */
window.addEventListener("beforeunload", function(e) {
  e.preventDefault();
  e.returnValue = '';
});

/* -- 遥控器按键检测 & 拦截 -- */
// 按 F9 显示最近的按键记录
var _lastKeys = [];
document.addEventListener("keydown", function(e) {
  _lastKeys.push(e.key + " code=" + e.code);
  if (_lastKeys.length > 20) _lastKeys.shift();
  // 按 F9 弹出最近按键
  if (e.key === "F9") { alert("Recent keys:\n" + _lastKeys.join("\n")); }

  // 拦截可能的回退键（搜索框聚焦时不拦截，保留删除字符能力）
  if (e.key === "Backspace" || e.key === "BrowserBack" || e.code === "BrowserBack" || e.which === 8 || e.which === 166) {
    if (_searchFocused) return;
    e.preventDefault();
    if (_searchResultsActive) {
      // 结果列表中的回退：回到搜索框继续编辑
      _searchResultsActive = false;
      _searchFocused = true;
      const inp = document.getElementById("search-input");
      if (inp) inp.focus();
      return;
    }
    if (_currentView === "player") {
      if (document.fullscreenElement) { document.exitFullscreen(); return; }
      if (_diagVisible) { toggleDiagPanel(); return; }
      stopPlayerInternal(true);
      if (_playId) navigateTo("detail", _playId); else navigateTo("home");
    } else if (_currentView === "detail") {
      navigateTo("home");
    }
  }
}, true);

/* -- Start -- */
window.location.hash = '#home';
navigateTo("home");

(async function() {
  try {
    const st = await F("/api/crawl/status");
    document.getElementById("status").textContent = st.progress || "";
  } catch (e) {}
})();
