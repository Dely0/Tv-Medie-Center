/* TV Media Center */
let _currentView = "home";
let _currentType = "";
let _currentGenre = "";
let _currentArea = "";
let _currentYear = "";
let _searchTimer = null;
let _searchFocused = false;
let _searchResultsActive = false; // 搜索结果浏览模式
let _browsePage = 1;
let _browseReqSeq = 0;   // 浏览请求序号：丢弃过期响应，防止快速切筛选被旧结果覆盖
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
let _probeInfo = null;         // 源站测速结果 {ttfb_ms, speed_mbs}
let _altSources = [];          // 备用源列表 [{source, play_url}]
let _altIndex = -1;            // 当前备用源索引
let _lines = [];               // 播放链候选线路（play-lines）
let _lineIndex = -1;           // 当前线路下标
let _linesLoaded = false;      // 本次播放会话是否已拉取播放链
let _linesPromise = null;      // 播放链拉取中的 Promise（去重 + 供按钮等待）
let _linesRefreshTimer = null; // 播放链延迟刷新定时器（等待后台跨源补充）
let _autoLineSwitched = false; // 本次播放会话是否已自动切到更快线路
let _linesRetried = false;     // 是否已做过第二次延迟刷新
let _stallTimer = null;        // 卡顿自动换线定时器
let _srcStatusAt = 0;          // 源状态最近刷新时间

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
  _currentGenre = "";
  _currentArea = "";
  _currentYear = "";
  document.getElementById("view-browse").innerHTML = '<div id="browse-content"><div class="loading"><div class="spinner"></div>加载中…</div></div>';
  await loadBrowsePage();
}

async function loadBrowsePage(direction) {
  const seq = ++_browseReqSeq;
  if (direction === "next") _browsePage++;
  else if (direction === "prev" && _browsePage > 1) _browsePage--;
  const el = document.getElementById("browse-content");
  try {
    const data = await F("/api/browse?type=" + _currentType + "&page=" + _browsePage +
      "&genre=" + encodeURIComponent(_currentGenre) +
      "&area=" + encodeURIComponent(_currentArea) +
      "&year=" + encodeURIComponent(_currentYear));
    if (seq !== _browseReqSeq) return; // 已有更新的请求，丢弃本次结果
    let tags = [], areas = [], years = [];
    try {
      const gd = await F("/api/genres?type=" + _currentType);
      tags = gd.tags || gd.genres || [];
      areas = gd.areas || [];
      years = gd.years || [];
    } catch (e) {}
    if (seq !== _browseReqSeq) return;

    // 成人页：顶部单独展示成人观看历史（与普通历史隔离）
    let adultHistoryHtml = "";
    if (_currentType === "adult") {
      try {
        const hd = await F("/api/history?adult=1&limit=10");
        const hitems = (hd && hd.items) || [];
        if (hitems.length) {
          adultHistoryHtml = '<div class="section"><div class="section-header"><div class="section-title">成人最近观看</div></div><div class="card-grid">' +
            hitems.map(h => card({ id: h.video_id, title: h.title, cover: h.cover, type: h.type })).join("") +
            '</div></div>';
        }
      } catch (e) {}
    }
    let html = adultHistoryHtml +
      renderFilterGroup("标签", tags, _currentGenre, "tag") +
      renderFilterGroup("地区", areas, _currentArea, "area") +
      renderFilterGroup("年份", years, _currentYear, "year");

    // 先绑定筛选 Tab 点击（即使结果为空也要能切换筛选）
    const bindTabs = () => {
      document.querySelectorAll("#browse-content .browse-tab").forEach(tab => {
        tab.onclick = () => selectFilter(tab.getAttribute("data-kind"), tab.getAttribute("data-value") || "");
      });
    };

    if (!data.results || !data.results.length) {
      if (direction === "next") _browsePage--;
      const hint = data.syncing
        ? '<div class="empty-view">成人内容首次同步中（约 1~2 分钟），页面会自动刷新…</div>'
        : '<div class="empty-view">暂无内容</div>';
      el.innerHTML = html + hint;
      bindTabs(); // 结果为空时也要能切换筛选
      if (data.syncing && _currentType === "adult" && _browsePage === 1) {
        // 同步完成后自动刷新
        setTimeout(() => {
          if (_currentType === "adult" && _browsePage === 1) loadBrowsePage();
        }, 20000);
      }
      return;
    }
    html += '<div class="card-grid">';
    for (const v of data.results) html += card(v);
    html += '</div>';
    html += '<div style="display:flex;justify-content:center;gap:16px;margin-top:24px">' +
      (_browsePage > 1 ? '<button class="nav-btn browse-prev" onclick="loadBrowsePage(\'prev\')">◀ 上一页</button>' : '') +
      '<span style="font-size:22px;color:var(--text-dim);padding:10px 16px">第 ' + _browsePage + ' 页</span>' +
      (data.results.length >= 30 ? '<button class="nav-btn browse-next" onclick="loadBrowsePage(\'next\')">下一页 ▶</button>' : '') +
      '</div>';
    el.innerHTML = html;
    bindTabs();
    autoFocusView();
  } catch (e) {
    el.innerHTML = '<div class="error-view">加载失败</div>';
  }
}

function renderFilterGroup(label, items, activeKey, kind) {
  let html = '<div class="browse-filter-group"><div class="browse-filter-label">' + esc(label) + '</div><div class="browse-tabs">' +
    '<button class="browse-tab' + (activeKey === "" ? " active" : "") + '" data-kind="' + kind + '" data-value="">全部</button>';
  for (const g of items || []) {
    const key = g.key !== undefined ? g.key : g.genre;
    const lbl = g.label !== undefined ? g.label : (g.genre !== undefined ? g.genre : key);
    html += '<button class="browse-tab' + (key === activeKey ? " active" : "") + '" data-kind="' + kind + '" data-value="' + escAttr(key) + '">' +
      esc(lbl) + '<span class="browse-tab-count">' + (g.count || 0) + '</span></button>';
  }
  return html + '</div></div>';
}

function selectFilter(kind, value) {
  if (kind === "area") _currentArea = value || "";
  else if (kind === "year") _currentYear = value || "";
  else _currentGenre = value || "";
  _browsePage = 1;
  loadBrowsePage();
  // 重渲染后把焦点留在所选 Tab 上
  setTimeout(() => {
    document.querySelectorAll("#browse-content .browse-tab").forEach(tab => {
      if (tab.getAttribute("data-kind") === kind && (tab.getAttribute("data-value") || "") === value) tab.focus();
    });
  }, 80);
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
    if (v.source) meta.push("来源 " + shortSource(v.source));

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

// 实时网速：优先分片统计，缺失时用 hls.js 内置带宽估算（bit/s → B/s）
function currentSpeed() {
  if (_lastSpeed > 0) return _lastSpeed;
  try {
    if (_hlsInstance && typeof _hlsInstance.bandwidthEstimate === "number" && _hlsInstance.bandwidthEstimate > 0) {
      return _hlsInstance.bandwidthEstimate / 8;
    }
  } catch (e) {}
  return 0;
}

function osdSpeedText() {
  const sp = currentSpeed();
  if (sp > 0) return formatSpeed(sp);
  if (_probeInfo) {
    const parts = [];
    if (_probeInfo.speed_mbs && _probeInfo.speed_mbs > 0) {
      parts.push("源站 " + formatSpeed(Math.round(_probeInfo.speed_mbs * 1048576)));
    }
    if (_probeInfo.ttfb_ms) {
      parts.push("响应 " + (_probeInfo.ttfb_ms / 1000).toFixed(1) + "s");
    }
    if (parts.length) return parts.join(" · ");
  }
  return "";
}

async function probeSource(url, referer) {
  if (!url) return;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch("/api/probe?url=" + encodeURIComponent(url) + "&referer=" + encodeURIComponent(referer || ""));
      const data = await res.json();
      _probeInfo = data || null;
      const osd = document.getElementById("buffer-osd");
      if (osd && !osd.classList.contains("hidden")) {
        document.getElementById("buffer-osd-speed").textContent = osdSpeedText();
      }
      if (_diagVisible) updateDiagPanel();
      if (data && (data.ttfb_ms || data.speed_mbs)) return; // 拿到有效数据即停
    } catch (e) {}
    if (attempt === 0) await new Promise(r => setTimeout(r, 5000));
  }
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
      '<div class="diag-row"><span>源站测速</span><span id="diag-probe">—</span></div>' +
      '<div class="diag-row"><span>缓冲提前</span><span id="diag-buffer">—</span></div>' +
      '<div class="diag-row"><span>清晰度 / 码率</span><span id="diag-level">—</span></div>' +
      '<div class="diag-row"><span>进度</span><span id="diag-progress">—</span></div>' +
      '<div class="diag-row"><span>播放线路</span><span id="diag-line">—</span></div>' +
      '<div class="diag-title">最近缓冲</div>' +
      '<div id="diag-events" class="diag-events">暂无缓冲记录</div>' +
      '<div class="diag-title">源状态</div>' +
      '<div id="diag-sources" class="diag-events">—</div>';
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
  document.getElementById("buffer-osd-speed").textContent = text !== undefined ? text : osdSpeedText();
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
  document.getElementById("diag-speed").textContent = formatSpeed(currentSpeed());
  let probeText = "—";
  if (_probeInfo) {
    const parts = [];
    if (_probeInfo.speed_mbs && _probeInfo.speed_mbs > 0) parts.push(formatSpeed(Math.round(_probeInfo.speed_mbs * 1048576)));
    if (_probeInfo.ttfb_ms) parts.push("响应 " + (_probeInfo.ttfb_ms / 1000).toFixed(1) + "s");
    if (parts.length) probeText = parts.join(" · ");
  }
  document.getElementById("diag-probe").textContent = probeText;
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
  const lineEl = document.getElementById("diag-line");
  if (lineEl) {
    lineEl.textContent = _lines.length
      ? (Math.max(0, _lineIndex + 1) + "/" + _lines.length + " · " + (_currentSourceName || ""))
      : (_currentSourceName || "—");
  }
  const events = getBufferLog().slice(-5).reverse();
  document.getElementById("diag-events").textContent = events.length
    ? events.map(ev => new Date(ev.t).toLocaleTimeString("zh-CN", { hour12: false }) + "  " + (ev.host || "?") + "  " + formatSpeed(ev.speed) + " 缓冲" + ev.duration + "s").join("\n")
    : "暂无缓冲记录";
  if (Date.now() - _srcStatusAt > 30000) refreshSourceStatus();
}

// 源健康状态（诊断面板底部，30 秒内不重复请求）
async function refreshSourceStatus() {
  _srcStatusAt = Date.now();
  const el = document.getElementById("diag-sources");
  if (!el) return;
  try {
    const [st, srcs] = await Promise.all([
      fetch("/api/ops/status").then(r => r.json()),
      fetch("/api/sources").then(r => r.json()),
    ]);
    const states = {};
    (st.sources || []).forEach(s => { states[s.name] = s; });
    const lines = (srcs.sources || [])
      .filter(s => s.type === "maccms" || s.type === "drpy")
      .map(s => {
        const h = states[s.name] || {};
        const stTxt = h.state === "dead" ? "已隔离" : (h.state === "slow" ? "慢" : (h.state === "ok" ? "正常" : "未测"));
        const lat = h.latency_ms ? h.latency_ms + "ms" : "";
        const err = h.last_error ? " " + h.last_error.slice(0, 40) : "";
        return s.name + " [" + stTxt + "] " + lat + err;
      });
    el.textContent = lines.length ? lines.join("\n") : "暂无源状态数据（启动后自动检查）";
  } catch (e) {
    el.textContent = "源状态获取失败";
  }
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
    startStallTimer();
    showBufferOSD();
  });
  video.addEventListener("stalled", () => {
    if (!_bufferSince) _bufferSince = Date.now();
    startStallTimer();
    showBufferOSD();
  });
  const endBuffer = () => {
    hideBufferOSD();
    clearLoadTimer();
    clearStallTimer();
    if (_bufferSince) {
      recordBufferEvent((Date.now() - _bufferSince) / 1000);
      _bufferSince = 0;
    }
  };
  video.addEventListener("playing", endBuffer);
  video.addEventListener("canplay", endBuffer);
}

// 卡顿超过 12 秒且播放链有备用线路时，自动切换线路
function startStallTimer() {
  clearStallTimer();
  _stallTimer = setTimeout(() => {
    _stallTimer = null;
    const video = document.getElementById("tv-video");
    if (!video || video.paused || video.ended) return;
    if (!_bufferSince) return;
    if (Date.now() - _bufferSince < 12000) return;
    nextLine(_playId, _playEp).then(switched => {
      if (!switched) {
        // 无备用线路：保留现有兜底逻辑
        if (_hlsRetryCount >= 3) trySwitchSource(_playId, _playEp);
      }
    });
  }, 12000);
}

function clearStallTimer() {
  if (_stallTimer) { clearTimeout(_stallTimer); _stallTimer = null; }
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
      nextLine(_playId, _playEp).then(switched => {
        if (!switched) trySwitchSource(_playId, _playEp);
      });
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
  _hlsBytes = 0;
  _speedSamples = [];
  _lastSpeed = 0;
  _probeInfo = null;
  _altSources = [];
  _altIndex = -1;
  _lines = [];
  _lineIndex = -1;
  _linesLoaded = false;
  _linesPromise = null;
  _autoLineSwitched = false;
  _linesRetried = false;
  clearLineRefreshTimer();
  clearStallTimer();
  clearLoadTimer();

  // 立即构建播放器并同步请求全屏（保持用户手势的同步调用栈，保证全屏激活有效）
  el.innerHTML =
    '<div class="player-bar" id="player-bar">' +
    '  <button class="player-nav-btn" id="btn-prev" onclick="switchEpisode(\'prev\')">◀ 上一集</button>' +
    '  <span class="player-nav-title" id="player-title">加载中…</span>' +
    '  <button class="player-nav-btn" id="btn-next" onclick="switchEpisode(\'next\')">下一集 ▶</button>' +
    '  <button class="player-nav-btn" id="btn-line" onclick="cycleLine()">线路</button>' +
    '  <button class="player-nav-btn" id="btn-fs" onclick="toggleFullscreen()">⛶</button>' +
    '  <button class="player-nav-btn" id="btn-diag" onclick="toggleDiagPanel()">ℹ</button>' +
    '  <button class="player-close-btn" onclick="stopPlayerFromClose()">✕</button>' +
    '</div>' +
    '<div id="player-stage">' +
    '<video id="tv-video" controls controlsList="nofullscreen" autoplay playsinline preload="auto"></video>' +
    '</div>';
  tryFullscreen(document.getElementById("player-stage"));

  const video = document.getElementById("tv-video");
  video.volume = 0.2;
  ensureDiagElements();
  bindBufferEvents(video);

  // Fetch episodes list
  try {
    const v = await F("/api/video/" + videoId);
    if (v && v.episodes) _playEps = v.episodes;
  } catch(e) { _playEps = []; }
  updatePlayerButtons();

  // Actually play
  await loadAndPlayUrl(videoId, episode);

  // Start progress saver
  startHistoryTimer(video);

  // Auto next on ended
  video.addEventListener("ended", onVideoEnded);
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
  _probeInfo = null;
  _altSources = [];
  _altIndex = -1;
  _lines = [];
  _lineIndex = -1;
  _linesLoaded = false;
  _linesPromise = null;
  _autoLineSwitched = false;
  _linesRetried = false;
  clearLineRefreshTimer();
  clearStallTimer();
  // 同步请求全屏（保留按键手势激活），未在全屏时进入全屏
  const stage = document.getElementById("player-stage");
  if (stage && !document.fullscreenElement && !document.webkitFullscreenElement) {
    tryFullscreen(stage);
  }
  await loadAndPlayUrl(_playId, _playEp);
  updatePlayerButtons();
}

// Load URL and swap video src (preserves fullscreen)
async function loadAndPlayUrl(videoId, episode, overrideUrl, overrideSource) {
  const params = [];
  if (episode) params.push("episode=" + episode);
  if (_startSeconds > 0) params.push("start_seconds=" + Math.floor(_startSeconds));
  const url = "/api/video/" + videoId + "/play" + (params.length ? "?" + params.join("&") : "");
  clearLoadTimer();
  hideBufferOSD();

  try {
    let data = null;
    if (overrideUrl) {
      data = {
        success: true,
        play_url: overrideUrl,
        source: overrideSource || "",
        episode_title: episode ? "第" + episode + "集" : "",
      };
    } else {
      // 先播当前源（秒开），后台多源测速优选，慢源自动切换
      const res = await fetch(url, { method: "POST" });
      data = await res.json();
    }
    if (!data.success) {
      _playFailed = true;
      showBufferOSD("无法获取播放地址，按 Enter 重试");
      return;
    }
    _playFailed = false;

    // drpy 源统一走本地代理（同源请求，规避浏览器 CORS/防盗链导致的卡顿/失败）
    if (data.use_proxy && data.play_url && !overrideUrl) {
      const ref = data.referer ? "&ref=" + encodeURIComponent(data.referer) : "";
      const isM3u8 = data.play_url.toLowerCase().indexOf(".m3u8") > 0;
      data.play_url = (isM3u8 ? "/api/hls-proxy?url=" : "/api/media-proxy?url=")
        + encodeURIComponent(data.play_url) + ref;
    }

    document.getElementById("player-title").textContent = data.episode_title || (episode ? "第" + episode + "集" : "");
    _currentUrl = data.play_url || "";
    _currentSourceName = data.source || "";
    _speedSamples = [];
    _lastSpeed = 0;
    _bufferSince = 0;
    _hlsBytes = 0;
    _probeInfo = null;

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
        maxBufferLength: 90,
        maxMaxBufferLength: 300,
        backBufferLength: 30,
        enableWorker: true,
        startLevel: 0,
        abrEwmaDefaultEstimate: 500000,
        abrEwmaFastVoD: 3,
        abrEwmaSlowVoD: 8,
        maxStarvationDelay: 6,
        maxLoadingDelay: 6,
      });
      _hlsInstance = hls;
      hls.loadSource(data.play_url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        // 从最低码率档起步：慢网先开播，带宽充足后 ABR 自动升档
        try {
          if (hls.levels && hls.levels.length > 1) {
            hls.currentLevel = 0;
            hls.nextLevel = 0;
          }
        } catch (e) {}
        video.play().catch(() => {});
        // 首次播放若还没进入全屏，尽量再尝试一次（仍在用户手势窗口内）
        ensureFullscreen();
      });
      hls.on(Hls.Events.FRAG_LOADED, (_evt, data) => {
        if (data && data.stats && data.stats.loaded) {
          _hlsBytes += data.stats.loaded;
        } else if (hls.stats && hls.stats.length) {
          _hlsBytes = hls.stats.length;
        }
        recordSpeedSample(_hlsBytes, performance.now());
        // 分片级瞬时速度兜底：第一个分片完成即可显示网速
        if (_lastSpeed <= 0 && data && data.stats && data.stats.loaded && data.stats.loading > 100) {
          _lastSpeed = data.stats.loaded / (data.stats.loading / 1000);
        }
        const osd = document.getElementById("buffer-osd");
        if (osd && !osd.classList.contains("hidden")) {
          document.getElementById("buffer-osd-speed").textContent = osdSpeedText();
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
          nextLine(_playId, _playEp).then(switched => {
            if (!switched) trySwitchSource(_playId, _playEp);
          });
        }
      });
    } else {
      video.src = data.play_url;
      video.play().catch(() => {});
      ensureFullscreen();
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
    // 异步源站测速（拉清单+首分片），不阻塞播放
    probeSource(data.play_url, data.referer || "");
    // 播放链：预取全部候选线路（后台），失败/卡顿时自动切换
    if (!overrideUrl) {
      if (!_linesLoaded) fetchPlayLines(videoId, episode);
      if (!_lines.length && !_linesLoaded) tryBestSource(videoId, episode, data.play_url);
    } else {
      updateLineButton();
    }
  } catch (e) {
    _playFailed = true;
    showBufferOSD("网络请求失败，按 Enter 重试");
  }
}

// 多源测速优选：不阻塞播放，发现明显更快的线路时自动切换
async function tryBestSource(videoId, episode, currentUrl) {
  try {
    const ac = new AbortController();
    const tm = setTimeout(() => ac.abort(), 12000);
    const bs = await fetch("/api/video/" + videoId + "/best-source" +
      (episode ? "?episode=" + episode : ""), { signal: ac.signal }).then(r => r.json());
    clearTimeout(tm);
    if (!bs || !bs.best || bs.best.error || !bs.best.play_url) return;
    // 备用源按测速排序，播放失败时依次切换
    _altSources = (bs.alternatives || [])
      .filter(a => a.play_url && a.play_url !== bs.best.play_url && !a.error)
      .map(a => ({ source: a.source, play_url: a.play_url }));
    _altIndex = -1;
    // 最优源就是当前源时不切换
    if (bs.best.play_url === currentUrl) return;
    if (bs.best.source === _currentSourceName) return;
    const video = document.getElementById("tv-video");
    const buffering = _bufferSince > 0 || !video || video.readyState < 2;
    const curSpeed = _lastSpeed;
    const better = (bs.best.speed_kbs || 0) > 120 &&
      (!curSpeed || (bs.best.speed_kbs > curSpeed * 1.5));
    if (buffering || better) {
      _hlsRetryCount = 0;
      showBufferOSD("已切换到更快线路 " + (bs.best.source || ""));
      await loadAndPlayUrl(videoId, episode, bs.best.play_url, bs.best.source);
    }
  } catch (e) {}
}

// 播放失败时自动查找并切换到备用源
async function trySwitchSource(videoId, episode) {
  // 已有备用源列表且还没用完：直接切下一个
  if (_altIndex >= 0 && _altIndex < _altSources.length - 1) {
    _altIndex++;
    _hlsRetryCount = 0;
    const alt = _altSources[_altIndex];
    showBufferOSD("已切换到备用源 " + (alt.source || ""));
    await loadAndPlayUrl(videoId, episode, alt.play_url, alt.source);
    return;
  }
  // 首次失败：向后端请求备用源列表
  if (_altIndex < 0) {
    showBufferOSD("当前源不可用，正在查找备用源…");
    try {
      const q = episode ? "?episode=" + episode : "";
      const res = await fetch("/api/video/" + videoId + "/alternates" + q);
      const data = await res.json();
      _altSources = ((data && data.alternates) || []).filter(a => a && a.play_url);
      if (_altSources.length) {
        _altIndex = 0;
        _hlsRetryCount = 0;
        const alt = _altSources[0];
        showBufferOSD("已切换到备用源 " + (alt.source || ""));
        await loadAndPlayUrl(videoId, episode, alt.play_url, alt.source);
        return;
      }
    } catch (e) {}
  }
  _playFailed = true;
  showBufferOSD("播放失败，按 Enter 重试");
}

/* -- 播放链：预取候选线路 + 失败/卡顿自动切换 + 手动切换 -- */

function linePlayUrl(line) {
  if (!line || !line.play_url) return "";
  if (!line.use_proxy) return line.play_url;
  const p = [];
  if (line.headers) {
    if (line.headers.referer) p.push("ref=" + encodeURIComponent(line.headers.referer));
    if (line.headers.ua) p.push("ua=" + encodeURIComponent(line.headers.ua));
    if (line.headers.origin) p.push("origin=" + encodeURIComponent(line.headers.origin));
    if (line.headers.cookie) p.push("cookie=" + encodeURIComponent(line.headers.cookie));
  }
  const qs = p.length ? "&" + p.join("&") : "";
  const isM3u8 = line.play_url.toLowerCase().indexOf(".m3u8") > 0;
  return (isM3u8 ? "/api/hls-proxy?url=" : "/api/media-proxy?url=")
    + encodeURIComponent(line.play_url) + qs;
}

async function fetchPlayLines(videoId, episode, forceRefresh) {
  if (_linesLoaded && !forceRefresh) return;
  clearLineRefreshTimer();
  // 已有正在拉取的请求则复用，避免重复请求
  if (_linesPromise && !forceRefresh) return _linesPromise;
  _linesPromise = (async () => {
    try {
      const res = await fetch("/api/video/" + videoId + "/play-lines" +
        (episode ? "?episode=" + episode : ""));
      const data = await res.json();
      _linesLoaded = true;
      _lines = ((data && data.lines) || []).filter(l => l && l.play_url);
      _lineIndex = -1;
      for (let i = 0; i < _lines.length; i++) {
        if (_lines[i].current || _lines[i].play_url === _currentUrl) { _lineIndex = i; break; }
      }
      updateLineButton();
      // 播放链不足时，继续用旧的 best-source 后台补充（跨源搜索候选）
      if (_lines.length <= 1 && !_altSources.length && !_playFailed) {
        tryBestSource(videoId, episode, _currentUrl);
      }
      // 当前源明显更慢时，自动切到播放链里最快的线路（每会话只自动切一次）
      if (!_autoLineSwitched && _lines.length > 1 && !_playFailed) {
        const best = _lines.find(l => l && l.speed_kbs && !l.error);
        if (best && best.play_url && best.play_url !== _currentUrl) {
          const curSpeed = _lastSpeed;
          const better = best.speed_kbs > 150 && (!curSpeed || best.speed_kbs > curSpeed * 1.5);
          if (better) {
            _autoLineSwitched = true;
            showBufferOSD("已自动切换到更快线路 " + (best.source || ""));
            loadAndPlayUrl(videoId, episode, linePlayUrl(best), best.source);
            return;
          }
        }
      }
      // 后台跨源补充需要几秒，稍后再拉一次让新线路进入列表
      if (!forceRefresh) {
        _linesRefreshTimer = setTimeout(() => {
          _linesRefreshTimer = null;
          fetchPlayLines(videoId, episode, true);
        }, 14000);
      } else if (!_linesRetried && _lines.length <= 3) {
        // 跨源补充可能超过 14 秒，20 秒后再试一次
        _linesRetried = true;
        _linesRefreshTimer = setTimeout(() => {
          _linesRefreshTimer = null;
          fetchPlayLines(videoId, episode, true);
        }, 20000);
      }
    } catch (e) {
      // 播放链获取失败时回退到旧的 best-source 兜底
      tryBestSource(videoId, episode, _currentUrl);
    } finally {
      _linesPromise = null;
    }
  })();
  return _linesPromise;
}

function clearLineRefreshTimer() {
  if (_linesRefreshTimer) { clearTimeout(_linesRefreshTimer); _linesRefreshTimer = null; }
}

function updateLineButton() {
  const btn = document.getElementById("btn-line");
  if (!btn) return;
  btn.textContent = _lines.length > 1
    ? "线路 " + Math.max(0, _lineIndex + 1) + "/" + _lines.length
    : "线路";
}

// 手动切换线路（播放条“线路”按钮）
async function cycleLine() {
  // 首次进入时播放链可能还在后台测速（最多 8-9 秒），先等它完成，避免误报“没有线路”
  if (!_lines.length && _linesPromise) {
    showBufferOSD("正在获取线路…");
    try {
      await Promise.race([
        _linesPromise,
        new Promise(r => setTimeout(r, 10000)),
      ]);
    } catch (e) {}
  }
  // 线路不足时强制刷新一次（后台可能正在跨源补充）
  if (_lines.length <= 1) {
    showBufferOSD("正在查找更多线路…");
    try {
      await Promise.race([
        fetchPlayLines(_playId, _playEp, true),
        new Promise(r => setTimeout(r, 12000)),
      ]);
    } catch (e) {}
  }
  if (!_lines.length) {
    showBufferOSD("暂无其他线路，正在查找备用源…");
    try {
      await trySwitchSource(_playId, _playEp);
    } finally {
      setTimeout(hideBufferOSD, 2500);
    }
    return;
  }
  let idx = _lineIndex < 0 ? -1 : _lineIndex;
  for (let i = 1; i <= _lines.length; i++) {
    const j = (idx + i) % _lines.length;
    const line = _lines[j];
    if (!line || line.error) continue;
    if (line.play_url === _currentUrl) continue;
    _lineIndex = j;
    _hlsRetryCount = 0;
    showBufferOSD("已切换线路 " + (line.source || "未知") +
      (line.speed_kbs ? " · " + Math.round(line.speed_kbs) + " KB/s" : ""));
    await loadAndPlayUrl(_playId, _playEp, linePlayUrl(line), line.source);
    return;
  }
  showBufferOSD("播放链中没有其他可用线路");
  setTimeout(hideBufferOSD, 2500);
}

// 失败/卡顿自动换线：返回 true 表示已切换
async function nextLine(videoId, episode) {
  if (!_lines.length) return false;
  let idx = _lineIndex < 0 ? -1 : _lineIndex;
  for (let i = 1; i <= _lines.length; i++) {
    const j = (idx + i) % _lines.length;
    const line = _lines[j];
    if (!line || line.error) continue;
    if (line.play_url === _currentUrl) continue;
    _lineIndex = j;
    _hlsRetryCount = 0;
    showBufferOSD("当前线路不可用，切换到 " + (line.source || "备用线路"));
    await loadAndPlayUrl(videoId, episode, linePlayUrl(line), line.source);
    return true;
  }
  return false;
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

function ensureFullscreen() {
  const stage = document.getElementById("player-stage");
  if (stage && !document.fullscreenElement && !document.webkitFullscreenElement) {
    tryFullscreen(stage);
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
  _probeInfo = null;
  _playFailed = false;
  _hlsRetryCount = 0;
  _lines = [];
  _lineIndex = -1;
  _linesLoaded = false;
  _linesPromise = null;
  _autoLineSwitched = false;
  _linesRetried = false;
  clearLineRefreshTimer();
  clearStallTimer();
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
    const hint = document.getElementById("search-hint");
    if (!data.results || !data.results.length) {
      el.innerHTML = '<div class="empty-view">暂无结果</div>';
      if (hint) hint.textContent = "";
      return;
    }
    if (hint) hint.textContent = "共 " + (data.total || data.results.length) + " 条结果 · 方向键浏览 · Enter 打开";
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
  const src = shortSource(v.source);
  const tip = (v.title || "") + (src ? "  ·  " + src : "");
  return '<div class="video-card" tabindex="0" title="' + escAttr(tip) + '" onclick="hideSearch();navigateTo(\'detail\',' + v.id + ')">' +
    '<div class="card-img-wrap">' +
    '<div class="card-placeholder">' + esc(v.title) + '</div>' +
    '<img class="card-img" src="' + escAttr(v.cover || "") + '" loading="lazy" onerror="this.style.display=\'none\'">' +
    '</div>' +
    '<div class="card-info"><div class="card-title">' + esc(v.title) + '</div>' +
    '<div class="card-sub">' + (badge ? '<span class="card-badge">' + badge + "</span>" : "") + (v.year ? "<span>" + v.year + "</span>" : "") + (score && score > 0 ? '<span>⭐' + Number(score).toFixed(1) + "</span>" : "") + (src ? '<span class="card-source">' + esc(src) + "</span>" : "") + "</div></div></div>";
}

// 来源名精简：去掉引擎后缀，如 荐片[优](DS) -> 荐片[优]
function shortSource(s) {
  if (!s) return "";
  return String(s).replace(/\s*\((DS|cat|hipy|DR2|py)\)\s*$/, "").trim();
}

/* -- History -- */
async function loadHistory() {
  const el = document.getElementById("view-history");
  el.innerHTML = '<div class="loading"><div class="spinner"></div>加载中…</div>';
  try {
    const data = await F("/api/history?limit=100");
    const items = (data && data.items) || [];
    if (!items.length) { el.innerHTML = '<div class="empty-view">暂无观看记录</div>'; return; }
    let html = '<div class="card-grid">';
    for (const h of items) {
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
    // 回退键（物理 Backspace / 遥控 BrowserBack / AHK 映射后的 Esc）
    // 在搜索框内统一用于删除字符，不依赖浏览器默认行为与 AHK 是否运行
    if (e.key === "Backspace" || e.key === "BrowserBack" || e.code === "BrowserBack" ||
        e.which === 8 || e.which === 166 || e.key === "Escape") {
      e.preventDefault();
      const inp = document.getElementById("search-input");
      if (inp && inp.value) {
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
    // 上键: 全屏时开关诊断面板；非全屏时把焦点移入播放器按钮栏（可再进全屏）
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        toggleDiagPanel();
        return;
      }
      const cur = document.activeElement;
      if (cur && cur.closest && cur.closest(".player-bar")) {
        // 已在按钮栏：上键取消焦点回到视频
        const video = document.getElementById("tv-video");
        if (video) video.blur();
        document.body.focus();
      } else {
        const btns = Array.from(document.querySelectorAll(".player-nav-btn:not([disabled]), .player-close-btn"));
        if (btns.length) focusWithScroll(btns[0]);
      }
      return;
    }
    // 下键: 非全屏时若焦点在按钮栏则移回视频
    if (e.key === "ArrowDown") {
      const cur = document.activeElement;
      if (!(document.fullscreenElement || document.webkitFullscreenElement) &&
          cur && cur.closest && cur.closest(".player-bar")) {
        e.preventDefault();
        const video = document.getElementById("tv-video");
        if (video) video.blur();
        document.body.focus();
      }
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
    case "Enter":      e.preventDefault(); if (cur) cur.click(); break;
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

/* -- 遥控器设置键 (鼠标右键) → 呼出搜索（播放中不呼出，避免覆盖播放器按钮） -- */
window.addEventListener("contextmenu", function(e) {
  e.preventDefault();
  if (_currentView === "player") return;
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
    if (_searchFocused) {
      // 搜索框内：捕获阶段先阻止浏览器默认导航（后退/离开页面），
      // 删除字符逻辑由冒泡阶段的 _searchFocused 分支统一处理
      e.preventDefault();
      return;
    }
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
  // 成人内容开关：开启时在导航末尾追加“成人”页面
  try {
    const cfg = await F("/api/config");
    if (cfg && cfg.adult_enabled) {
      const nav = document.getElementById("nav");
      if (nav && !document.querySelector('#nav .nav-btn[data-type="adult"]')) {
        const btn = document.createElement("button");
        btn.className = "nav-btn";
        btn.setAttribute("data-view", "browse");
        btn.setAttribute("data-type", "adult");
        btn.textContent = "🔞 成人";
        btn.onclick = () => navigateTo("browse", "adult");
        nav.appendChild(btn);
      }
    }
  } catch (e) {}
})();
