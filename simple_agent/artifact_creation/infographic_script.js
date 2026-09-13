/* ---- client-side rendering from window.VOCAB ------------------------- */
var DRIVE_AUDIO = {
  1:"130D_2EErw-q5F3Lr8j9ARN8Xc33zIszz",2:"1QLdmWucHRWsbhpZfx_KkSGH7c6A6GvD_",
  3:"1XYPIKjZDh_uaPwcVyuZVj1RyBEtvgLTL",4:"1zYn-MYhqDxTu76URbIXagQiQ-cabNcmH",
  5:"145nNHdU505XyZUd-YV1gNYTUGlMSgyB5",6:"1CKePE8r_iFUa8L00o_khSvu-9E3ZNCXJ",
  7:"1zPUfaZVcW9C7ismIF9VzX-gK6wRb30tR",8:"1MOPbLPNXlDLyoGNpbBaBSeRXY45ZMGlt",
  9:"1At-Th7OPxJDd9__cDDFNzm-K1WvDak32",10:"1DnCr9MicQbTVdiQ_f_MAqiAagdIo6G7D",
  11:"1tZ9T1mWdGLmF1EhVTuq-lNMFgeTdwKk2",12:"1igDKVkzrj2iLr_Pac1i5rpgRLaEUHt5S",
  13:"10e52d-aQwe5FYjS52KE1IFYzvfoL_j1G",14:"1zOS_tL0tsFFI57O15wjiZuWX18R3Qq1-",
  15:"1tX-5dn3O1cg7sXjtmlcKgcQQt5wBghVp",16:"1BPS4quimdoWOhy0a1l-wZmrdtqeY2On4"
};
function _esc(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function _plain(t){return (t||'').replace(/\[[぀-ヿ゠-ヿー]+\]/g,'');}
function _ruby(t){
  return (t||'').replace(/([㐀-鿿豈-﫿々〆〇ヶ]+)\[([぀-ヿ゠-ヿー]+)\]/g,
    function(_,k,r){return '<ruby>'+_esc(k)+'<rt>'+_esc(r)+'</rt></ruby>';});
}
function _renderEntry(r){
  var search=[_plain(r.w),r.r,r.m.toLowerCase(),r.fr,_plain(r.s[0][0])].join(' ').toLowerCase();
  var sentences=r.s.map(function(s,i){
    return '<div class="ex"><span class="ex-no">'+(i+1)+'</span>'+
      '<span class="ex-jp">'+_ruby(s[0])+'</span>'+
      '<span class="ex-en">'+_esc(s[1])+'</span></div>';
  }).join('');
  var note=r.n?'<div class="more"><p class="nt">'+_esc(r.n)+'</p></div>':'';
  var cue=r.n?'<span class="e-cue">+</span>':'';
  return '<article class="e'+(r.n?' has-note':'')+'" data-w="'+_esc(r.w)+'" data-s="'+_esc(search)+'">'+
    '<button class="pip" type="button" aria-label="I know this - advance" title="I know this - advance"></button>'+
    '<div class="e-body"><div class="e-head">'+
    '<span class="w">'+_ruby(r.wf)+'</span>'+
    '<span class="rd">'+_esc(r.r)+'</span>'+
    '<button class="say" type="button" data-w="'+_esc(r.w)+'" aria-label="pronounce '+_esc(r.w)+'" title="say it">&#9654;</button>'+
    '<button class="draw" type="button" data-w="'+_esc(r.w)+'" aria-label="stroke order for '+_esc(r.w)+'" title="stroke order">&#9998;</button>'+
    '<span class="pos">'+_esc(r.p)+'</span>'+
    '</div><p class="mn">'+_esc(r.m)+'</p>'+
    '<p class="pt"><span class="pt-k">型</span>'+_esc(r.fr)+'</p>'+
    '<div class="ex-grid">'+sentences+'</div>'+note+'</div>'+
    '<span class="e-tags" aria-hidden="true"><span class="e-due">due</span>'+cue+'</span></article>';
}
function _renderDay(day,items){
  var TYPE_ORDER=['verb','noun','adj','adv'],counts={};
  items.forEach(function(r){counts[r.t]=(counts[r.t]||0)+1;});
  var mix=TYPE_ORDER.filter(function(t){return counts[t];}).map(function(t){return counts[t]+' '+t;}).join(' · ');
  var fid=DRIVE_AUDIO[day]||'', dd=String(day).padStart(2,'0');
  return '<section class="day" id="day'+dd+'" data-day="'+day+'">'+
    '<div class="day-head"><span class="day-no">DAY '+dd+'</span>'+
    '<span class="day-meta">'+items.length+' words &nbsp;/&nbsp; '+mix+'</span>'+
    '<span class="day-tally" data-day-tally="'+day+'"></span>'+
    '<button class="day-audio" type="button" data-day="'+day+'" data-fid="'+fid+
    '" aria-expanded="false" aria-controls="dp'+day+'">&#9654; full track</button>'+
    '</div><div class="day-player" id="dp'+day+'" hidden></div>'+
    '<div class="grid">'+items.map(_renderEntry).join('')+'</div></section>';
}
(function(){
  var byDay={},days=[];
  (window.VOCAB||[]).forEach(function(r){if(!byDay[r.d]){byDay[r.d]=[];days.push(r.d);}byDay[r.d].push(r);});
  days.sort(function(a,b){return a-b;});
  var main=document.querySelector('main');
  main.innerHTML='<p class="nores" id="nores">No word matches that.</p>'+
    days.map(function(d){return _renderDay(d,byDay[d]);}).join('');
})();

(function () {
  var body = document.body;
  var LS = {
    get: function (k, d) { try { var v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  };

  /* ---- audio: per-word clips (base64 from window.WA) + per-day tracks (Drive) --- */
  var WA = window.WA || {};
  var _wa = new Audio();
  var _wasrc = null;
  function playWord(w, btn) {
    var src = WA[w];
    if (!src) return false;
    try { _wa.pause(); } catch (e) {}
    // src is either a https:// Blob URL or a legacy data:audio/mpeg;base64,... URI
    _wa.src = src.startsWith("http") ? src : "data:audio/mpeg;base64," + src;
    _wasrc = w;
    document.querySelectorAll(".say.playing").forEach(function (s) { s.classList.remove("playing"); });
    if (btn) {
      btn.classList.add("playing");
      _wa.onended = _wa.onpause = function () { btn.classList.remove("playing"); };
    }
    _wa.play().catch(function () { if (btn) btn.classList.remove("playing"); });
    return true;
  }
  document.addEventListener("click", function (ev) {
    var s = ev.target.closest(".say");
    if (!s) return;
    ev.stopPropagation();           // don't trigger card-expand
    playWord(s.dataset.w, s);
  });

  var _dayData = {};               // day -> "data:audio/mpeg;base64,..." (session cache)
  var _mcpP = null;
  function getMcp() {
    if (_mcpP) return _mcpP;
    _mcpP = (window.claude && window.claude.use)
      ? window.claude.use("mcp").catch(function () { return null; })
      : Promise.resolve(null);
    return _mcpP;
  }
  function driveLink(fid) {
    return "https://drive.google.com/file/d/" + fid + "/view";
  }
  function mountAudio(box, url) {
    box.innerHTML = "";
    var a = document.createElement("audio");
    a.controls = true; a.preload = "metadata"; a.src = url; a.autoplay = true;
    box.appendChild(a);
  }
  function fallback(box, fid, code) {
    box.innerHTML = '<a class="dp-link" href="' + driveLink(fid) +
      '" target="_blank" rel="noopener">open this track in Google Drive &#8599;</a>' +
      (code ? ' <span class="dp-err">(' + code + ')</span>' : '');
  }
  function pickB64(res) {
    var p = res && res.payload;
    if (p && typeof p === "object" && (p.content || p.data)) return p.content || p.data;
    var blocks = res && res.content;
    if (Array.isArray(blocks)) {
      for (var i = 0; i < blocks.length; i++) {
        var t = blocks[i] && blocks[i].text;
        if (typeof t === "string") {
          try { var o = JSON.parse(t); if (o && (o.content || o.data)) return o.content || o.data; }
          catch (e) {}
        }
      }
    }
    return null;
  }
  async function loadDayTrack(day, fid, box) {
    if (_dayData[day]) { mountAudio(box, _dayData[day]); return; }
    box.innerHTML = '<span class="dp-load">fetching track from Drive&hellip;</span>';
    var mcp = await getMcp();
    if (!mcp) { fallback(box, fid); return; }
    try {
      var res = await mcp.callTool("{DRIVE_CONNECTOR}", "download_file_content",
        { fileId: fid }, { cache: { staleTime: 300000, gcTime: 86400000 } });
      var b64 = pickB64(res);
      if (!b64) throw { code: "empty_response" };
      _dayData[day] = "data:audio/mpeg;base64," + b64;
      mountAudio(box, _dayData[day]);
    } catch (err) {
      fallback(box, fid, err && err.code);
    }
  }
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest(".day-audio");
    if (!b) return;
    var box = document.getElementById("dp" + b.dataset.day);
    var open = b.getAttribute("aria-expanded") === "true";
    if (open) { box.hidden = true; b.setAttribute("aria-expanded", "false"); return; }
    b.setAttribute("aria-expanded", "true"); box.hidden = false;
    loadDayTrack(b.dataset.day, b.dataset.fid, box);
  });

  var entries = Array.prototype.slice.call(document.querySelectorAll(".e"));
  var days = Array.prototype.slice.call(document.querySelectorAll(".day"));
  var byWord = {};
  entries.forEach(function (e) { byWord[e.dataset.w] = e; });
  var TOTAL = entries.length;

  /* ---- SRS core ----------------------------------------------------- */
  var BOX_DAYS = [0, 1, 2, 4, 8, 16, 35];      // days until next due, per box
  var MATURE = 5;
  var NEW_PER_SESSION = 15;
  var srs = LS.get("n3srs", {});             // word -> box b, due date, reps, lapses

  function todayStr(offset) {
    var d = new Date(); d.setHours(0, 0, 0, 0);
    if (offset) d.setDate(d.getDate() + offset);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  var TODAY = todayStr(0);

  function isDue(w) {
    var s = srs[w];
    return !!s && (s.b === 0 || s.due <= TODAY);
  }
  function grade(w, g) {                       // g: "again" | "good" | "easy"
    var s = srs[w] || { b: 0, reps: 0, lapses: 0 };
    if (g === "again") { if (s.b > 0) s.lapses++; s.b = 0; s.due = TODAY; }
    else {
      s.b = Math.min(6, s.b + (g === "easy" ? 2 : 1));
      if (g === "easy" && s.b < 3) s.b = 3;
      s.due = todayStr(BOX_DAYS[s.b]);
    }
    s.reps++;
    srs[w] = s;
    LS.set("n3srs", srs);
    paint();
  }

  function paint() {
    var mat = 0, lrn = 0, due = 0;
    entries.forEach(function (e) {
      var s = srs[e.dataset.w];
      var st = !s ? "new" : (s.b >= MATURE ? "mature" : "learning");
      e.classList.toggle("st-new", st === "new");
      e.classList.toggle("st-learning", st === "learning");
      e.classList.toggle("st-mature", st === "mature");
      var d = isDue(e.dataset.w);
      e.classList.toggle("is-due", d);
      if (st === "mature") mat++; else if (st === "learning") lrn++;
      if (d) due++;
      e.querySelector(".pip").title = s
        ? ("box " + s.b + (d ? " · due" : " · due " + s.due)) : "new - click to mark seen";
    });
    set("s-mat", mat); set("s-lrn", lrn); set("s-new", TOTAL - mat - lrn); set("s-due", due);
    document.getElementById("stat-fill").style.width = (mat / TOTAL * 100) + "%";
    var q = queueSize();
    var btn = document.getElementById("review-open");
    btn.disabled = q === 0;
    btn.textContent = q ? "Review " + q : "Review — clear";
    days.forEach(function (dy) {
      var es = dy.querySelectorAll(".e");
      var m = dy.querySelectorAll(".e.st-mature").length;
      dy.querySelector(".day-tally").textContent = m ? m + "/" + es.length : "";
    });
    refreshDays();
  }
  function set(id, v) { document.getElementById(id).textContent = v; }
  function refreshDays() {
    var f = document.getElementById("filter").value;
    var anyVisible = false;
    days.forEach(function (d) {
      var vis = Array.prototype.some.call(d.querySelectorAll(".e"), function (e) {
        if (e.classList.contains("hit")) return false;
        if (f === "all") return true;
        return e.classList.contains(f === "due" ? "is-due" : "st-" + f);
      });
      d.classList.toggle("empty", !vis);
      if (vis) anyVisible = true;
    });
    var searching = !!document.getElementById("q").value.trim();
    if (!anyVisible && !searching && f !== "all") {
      nores.textContent = f === "due" ? "Nothing due right now — come back later or learn new words in Review."
        : "No " + f + " words yet.";
      nores.classList.add("show");
    } else if (!searching) {
      nores.classList.remove("show");
    }
  }

  /* ---- review session --------------------------------------------- */
  function buildQueue() {
    var dueList = [], newList = [];
    entries.forEach(function (e) {
      var w = e.dataset.w;
      if (isDue(w)) dueList.push(w);
      else if (!srs[w]) newList.push(w);
    });
    shuffle(dueList);
    return dueList.concat(newList.slice(0, NEW_PER_SESSION));
  }
  function queueSize() { return buildQueue().length; }
  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  var rv = document.getElementById("rv");
  var stage = document.getElementById("rv-stage");
  var rvCount = document.getElementById("rv-count");
  var rvAuto = document.getElementById("rv-auto");
  var queue = [], qi = 0, done = 0, revealed = false;

  function openReview() {
    queue = buildQueue(); qi = 0; done = 0;
    if (!queue.length) return;
    rv.hidden = false;
    document.getElementById("rv-close").focus();
    showCard();
  }
  function closeReview() { rv.hidden = true; paint(); }

  function showCard() {
    revealed = false;
    if (qi >= queue.length) {
      rvCount.textContent = done + " reviewed";
      var left = buildQueue().length;
      stage.innerHTML = '<div class="rv-done"><p>キリがいい。 Session done.</p>' +
        '<span>' + done + ' reviewed' + (left ? ' · ' + left + ' still in the queue' : ' · queue clear') +
        '</span></div>' +
        '<div class="rv-actions">' +
        (left ? '<button class="g-good" id="rv-more">keep going</button>' : '') +
        '<button class="reveal" id="rv-again-close">done</button></div>';
      var more = document.getElementById("rv-more");
      if (more) more.onclick = function () { openReview(); };
      document.getElementById("rv-again-close").onclick = closeReview;
      document.getElementById("rv-again-close").focus();
      return;
    }
    var w = queue[qi];
    var e = byWord[w];
    rvCount.textContent = (qi + 1) + " / " + queue.length;
    stage.innerHTML = "";
    var front = document.createElement("div");
    front.className = "rv-front";
    front.textContent = w;
    if (WA[w]) {
      var sb = document.createElement("button");
      sb.className = "say"; sb.type = "button"; sb.dataset.w = w;
      sb.setAttribute("aria-label", "pronounce " + w); sb.title = "say it";
      sb.innerHTML = "&#9654;";
      front.appendChild(sb);
      if (rvAuto && rvAuto.checked) setTimeout(function () { playWord(w, sb); }, 120);
    }
    var hint = document.createElement("div");
    hint.className = "rv-hint";
    hint.textContent = srs[w] ? ("box " + srs[w].b) : "new word";
    var back = document.createElement("div");
    back.className = "rv-back"; back.hidden = true;
    back.appendChild(e.querySelector(".e-body").cloneNode(true));
    var actions = document.createElement("div");
    actions.className = "rv-actions";
    actions.innerHTML = '<button class="reveal" data-a="reveal">reveal &nbsp;·&nbsp; space</button>';
    stage.append(front, hint, back, actions);
    actions.querySelector("[data-a=reveal]").focus();
    actions.addEventListener("click", onAction);
  }
  function reveal() {
    if (revealed) return;
    var bk = stage.querySelector(".rv-back");
    if (!bk) return;
    revealed = true;
    bk.hidden = false;
    var h = stage.querySelector(".rv-hint"); if (h) h.remove();
    var a = stage.querySelector(".rv-actions");
    a.innerHTML =
      '<button class="g-again" data-a="again">Again <small>1</small></button>' +
      '<button class="g-good" data-a="good">Good <small>2</small></button>' +
      '<button class="g-easy" data-a="easy">Easy <small>3</small></button>';
    a.querySelector("[data-a=good]").focus();
  }
  function onAction(ev) {
    var b = ev.target.closest("button"); if (!b) return;
    var a = b.dataset.a;
    if (a === "reveal") { reveal(); return; }
    var w = queue[qi];
    grade(w, a);
    done++;
    if (a === "again") queue.splice(Math.min(queue.length, qi + 3), 0, w);
    qi++;
    showCard();
  }

  document.getElementById("review-open").addEventListener("click", openReview);
  document.getElementById("rv-close").addEventListener("click", closeReview);
  rv.addEventListener("click", function (ev) { if (ev.target === rv) closeReview(); });
  document.addEventListener("keydown", function (ev) {
    if (!so.hidden) {
      if (ev.key === "Escape") soClose();
      else if (ev.key === " ") { ev.preventDefault(); soTogglePlay(); }
      else if (ev.key === "r" || ev.key === "R") soReplay();
      else if (ev.key === "ArrowRight") { ev.preventDefault(); soStep(1); }
      else if (ev.key === "ArrowLeft") { ev.preventDefault(); soStep(-1); }
      else if (ev.key === "a" || ev.key === "A") document.getElementById("so-all").click();
      else if (ev.key === "n" || ev.key === "N") document.getElementById("so-num").click();
      return;
    }
    if (!rv.hidden) {
      if (ev.key === "Escape") closeReview();
      else if (ev.key === " ") { ev.preventDefault(); reveal(); }
      else if (ev.key === "p" || ev.key === "P") { if (queue[qi]) playWord(queue[qi]); }
      else if (revealed && "123".indexOf(ev.key) !== -1) {
        ["again", "good", "easy"][+ev.key - 1] && grade0(["again", "good", "easy"][+ev.key - 1]);
      }
      return;
    }
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      if (ev.key === "Escape" && document.activeElement === q) { q.value = ""; runSearch(); q.blur(); }
      return;
    }
    if (ev.key === "/") { ev.preventDefault(); q.focus(); }
    else if (ev.key === "r" || ev.key === "R") { openReview(); }
    else if (ev.key === "p" || ev.key === "P") {
      var oc = document.querySelector(".e.open");
      if (oc) playWord(oc.dataset.w, oc.querySelector(".say"));
    }
    else if (ev.key === "w" || ev.key === "W") {
      var dc = document.querySelector(".e.open") || entries.find(function (e) { return !e.classList.contains("hit"); });
      if (dc) soOpen(dc.dataset.w, dc.querySelector(".draw"));
    }
  });
  function grade0(a) { var w = queue[qi]; grade(w, a); done++;
    if (a === "again") queue.splice(Math.min(queue.length, qi + 3), 0, w); qi++; showCard(); }

  /* ---- browse: pip = quick promote, click card = expand ------------ */
  entries.forEach(function (e) {
    e.querySelector(".pip").addEventListener("click", function (ev) {
      ev.stopPropagation();
      grade(e.dataset.w, "good");
    });
    e.addEventListener("click", function (ev) {
      if (ev.target.closest(".pip") || ev.target.closest(".say") || ev.target.closest(".draw")) return;
      if (!e.classList.contains("has-note")) return;
      if (!window.getSelection().isCollapsed) return;
      e.classList.toggle("open");
    });
  });

  /* ---- search ----------------------------------------------------- */
  var q = document.getElementById("q");
  var qx = document.getElementById("q-x");
  var nores = document.getElementById("nores");
  function runSearch() {
    var t = q.value.trim().toLowerCase();
    qx.hidden = !t;
    var shown = 0;
    entries.forEach(function (e) {
      var hit = !t || e.dataset.s.indexOf(t) !== -1;
      e.classList.toggle("hit", !hit);
      if (hit) shown++;
    });
    refreshDays();
    nores.classList.toggle("show", !!t && shown === 0);
  }
  q.addEventListener("input", runSearch);
  qx.addEventListener("click", function () { q.value = ""; runSearch(); q.focus(); });

  /* ---- filter + visibility toggles ------------------------------------ */
  var pref = LS.get("n3ref", {});
  if (rvAuto) {
    rvAuto.checked = pref.rvAuto !== false && !!Object.keys(WA).length;
    rvAuto.disabled = !Object.keys(WA).length;
    rvAuto.addEventListener("change", function () {
      pref.rvAuto = rvAuto.checked; LS.set("n3ref", pref);
    });
  }
  var filter = document.getElementById("filter");
  filter.value = pref.filter || "all";
  body.dataset.filter = filter.value;
  filter.addEventListener("change", function () {
    body.dataset.filter = filter.value; pref.filter = filter.value; LS.set("n3ref", pref);
    refreshDays();
  });

  var defs = { "t-furi": "no-furi", "t-mn": "no-mn", "t-pt": "no-pt" };
  Object.keys(defs).forEach(function (id) {
    var btn = document.getElementById(id);
    setToggle(btn, !!pref[id]);
    btn.addEventListener("click", function () {
      setToggle(btn, btn.getAttribute("aria-pressed") !== "true");
      Object.keys(defs).forEach(function (i) {
        pref[i] = document.getElementById(i).getAttribute("aria-pressed") === "true";
      });
      LS.set("n3ref", pref);
    });
  });
  function setToggle(btn, on) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    body.classList.toggle(defs[btn.id], on);
  }

  /* ---- backup / restore --------------------------------------------- */
  var io = document.getElementById("io");
  document.getElementById("io-export").onclick = function () { io.value = JSON.stringify(srs); };
  document.getElementById("io-import").onclick = function () {
    try {
      var obj = JSON.parse(io.value);
      if (obj && typeof obj === "object") {
        srs = obj; LS.set("n3srs", srs); paint();
        io.value = "restored " + Object.keys(srs).length + " words";
      }
    } catch (e) { io.value = "could not parse that JSON"; }
  };
  document.getElementById("io-reset").onclick = function () {
    if (confirm("Clear all spaced-repetition progress on this device?")) {
      srs = {}; LS.set("n3srs", srs); paint(); io.value = "";
    }
  };

  /* ---- stroke-order animation (embedded KanjiVG paths) ------------- */
  var KVG = window.KVG || {};
  var haveKvg = !!Object.keys(KVG).length;
  var SVGNS = "http://www.w3.org/2000/svg";
  var so = document.getElementById("so");
  var soChars = document.getElementById("so-chars");
  var soCard = document.querySelector(".so-card");
  var soHint = document.getElementById("so-hint");
  var soPlayBtn = document.getElementById("so-play");
  var soAllBtn = document.getElementById("so-all");
  var soNumBtn = document.getElementById("so-num");

  var soRet = null;
  var soTracks = [];        // per char: { paths:[el], nums:[el], lens:[num], count }
  var soK = 0;              // strokes revealed so far (0..soMax)
  var soMax = 0;
  var soResumeK = 0;
  var soTimer = null;
  var soPlaying = false;
  var curDur = 500;
  var SPEEDS = [0.5, 1, 1.5, 2];
  var soSpeed = 1;

  function svgEl(name, attrs) {
    var el = document.createElementNS(SVGNS, name);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function buildTrack(ch) {
    var wrap = document.createElement("div");
    wrap.className = "so-char";
    var d = KVG[ch];
    if (!d || !d.s || !d.s.length) {
      wrap.className = "so-char nodata";
      wrap.textContent = ch;
      var note = document.createElement("span");
      note.textContent = "no stroke data";
      wrap.appendChild(note);
      soChars.appendChild(wrap);
      return null;
    }
    var svg = svgEl("svg", { viewBox: "0 0 109 109" });
    var ghost = svgEl("g", { "class": "ghost" });
    var ink = svgEl("g", { "class": "ink" });
    var nums = svgEl("g", { "class": "nums" });
    var paths = [];
    d.s.forEach(function (dd) {
      ghost.appendChild(svgEl("path", { d: dd }));
      var p = svgEl("path", { d: dd });
      ink.appendChild(p);
      paths.push(p);
    });
    (d.n || []).forEach(function (xy, i) {
      var t = svgEl("text", { x: xy[0], y: xy[1] });
      t.textContent = String(i + 1);
      t.style.opacity = "0";
      nums.appendChild(t);
    });
    svg.appendChild(ghost); svg.appendChild(ink); svg.appendChild(nums);
    wrap.appendChild(svg);
    var cap = document.createElement("div");
    cap.className = "cap";
    cap.textContent = d.s.length + (d.s.length === 1 ? " stroke" : " strokes");
    wrap.appendChild(cap);
    soChars.appendChild(wrap);
    return { paths: paths, nums: nums.children, count: d.s.length, lens: [] };
  }

  function soMeasure() {
    soTracks.forEach(function (tr) {
      tr.lens = tr.paths.map(function (p) {
        var L = 100;
        try { L = p.getTotalLength() || 100; } catch (e) {}
        p.style.strokeDasharray = L + " " + (L + 1);
        p.style.strokeDashoffset = String(L);
        return L;
      });
    });
  }

  function computeDur(idx) {             // idx = 0-based stroke about to draw
    var L = 0;
    soTracks.forEach(function (tr) { if (idx < tr.count) L = Math.max(L, tr.lens[idx] || 0); });
    return Math.max(240, Math.min(1300, L * 7 / soSpeed));
  }

  function showStroke(i, tr, animate) {
    var p = tr.paths[i];
    if (!p) return;
    if (animate) {
      p.style.transition = "none";
      p.style.strokeDashoffset = String(tr.lens[i]);
      p.getBoundingClientRect();          // reflow so the transition runs from full
      p.style.transition = "stroke-dashoffset " + curDur + "ms linear";
      p.style.strokeDashoffset = "0";
      p.classList.add("lead");
      setTimeout(function () { p.classList.remove("lead"); }, curDur + 40);
    } else {
      p.style.transition = "none";
      p.style.strokeDashoffset = "0";
      p.classList.remove("lead");
    }
    if (tr.nums[i]) tr.nums[i].style.opacity = "1";
  }
  function hideStroke(i, tr) {
    var p = tr.paths[i];
    if (!p) return;
    p.style.transition = "none";
    p.style.strokeDashoffset = String(tr.lens[i]);
    p.classList.remove("lead");
    if (tr.nums[i]) tr.nums[i].style.opacity = "0";
  }

  function updateHint() {
    soHint.innerHTML = "<b>" + soK + "</b> / " + soMax + (soK >= soMax && soMax ? " &#10003;" : "");
  }
  function seek(k, animate) {
    k = Math.max(0, Math.min(soMax, k));
    var prev = soK;
    soK = k;
    soTracks.forEach(function (tr) {
      for (var i = 0; i < tr.count; i++) {
        if (i < k) showStroke(i, tr, animate && i >= prev && i === k - 1);
        else hideStroke(i, tr);
      }
    });
    updateHint();
  }

  function soTick() {
    if (soK >= soMax) { soSetPlaying(false); return; }
    curDur = computeDur(soK);
    seek(soK + 1, true);
    soTimer = setTimeout(soTick, curDur + 180 / soSpeed);
  }
  function soSetPlaying(on) {
    soPlaying = on;
    clearTimeout(soTimer); soTimer = null;
    soPlayBtn.setAttribute("aria-pressed", on ? "true" : "false");
    soPlayBtn.innerHTML = on ? "&#10073;&#10073; pause" : "&#9654; play";
    if (on) {
      if (soAllBtn.getAttribute("aria-pressed") === "true") soAllBtn.click();
      if (soK >= soMax) seek(0, false);
      soTick();
    }
  }
  function soTogglePlay() { soSetPlaying(!soPlaying); }
  function soReplay() { clearTimeout(soTimer); seek(0, false); soSetPlaying(true); }
  function soStep(dir) {
    soSetPlaying(false);
    if (soAllBtn.getAttribute("aria-pressed") === "true") return;
    if (dir > 0) { curDur = Math.min(computeDur(soK), 480); seek(soK + 1, true); }
    else seek(soK - 1, false);
  }

  function soOpen(w, ret) {
    if (!haveKvg) return;
    soRet = ret || null;
    clearTimeout(soTimer); soTimer = null; soPlaying = false;
    document.getElementById("so-w").textContent = w;
    var e = byWord[w];
    document.getElementById("so-rd").textContent = e ? e.querySelector(".rd").textContent : "";
    soChars.innerHTML = "";
    soTracks = [];
    Array.from(w).forEach(function (ch) {
      var tr = buildTrack(ch);
      if (tr) soTracks.push(tr);
    });
    soMax = soTracks.reduce(function (m, tr) { return Math.max(m, tr.count); }, 0);
    soResumeK = 0;
    soAllBtn.setAttribute("aria-pressed", "false");
    so.hidden = false;
    document.documentElement.classList.add("so-lock");
    document.body.classList.add("so-lock");
    requestAnimationFrame(function () {
      soMeasure();
      seek(0, false);
      if (soMax) soSetPlaying(true); else updateHint();
    });
    document.getElementById("so-close").focus();
  }
  function soClose() {
    so.hidden = true;
    clearTimeout(soTimer); soTimer = null; soPlaying = false;
    document.documentElement.classList.remove("so-lock");
    document.body.classList.remove("so-lock");
    if (soRet && soRet.focus) soRet.focus();
  }

  document.getElementById("so-close").addEventListener("click", soClose);
  document.getElementById("so-replay").addEventListener("click", soReplay);
  document.getElementById("so-play").addEventListener("click", soTogglePlay);
  document.getElementById("so-fwd").addEventListener("click", function () { soStep(1); });
  document.getElementById("so-back").addEventListener("click", function () { soStep(-1); });
  document.getElementById("so-speed").addEventListener("click", function () {
    soSpeed = SPEEDS[(SPEEDS.indexOf(soSpeed) + 1) % SPEEDS.length];
    this.innerHTML = "speed &times;" + soSpeed;
  });
  soAllBtn.addEventListener("click", function () {
    var on = this.getAttribute("aria-pressed") !== "true";
    this.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) { soResumeK = soK; soSetPlaying(false); seek(soMax, false); }
    else { seek(soResumeK, false); }
  });
  function soApplyNums(on) {
    soNumBtn.setAttribute("aria-pressed", on ? "true" : "false");
    soCard.classList.toggle("no-nums", !on);
  }
  soApplyNums(pref.soNums !== false);
  soNumBtn.addEventListener("click", function () {
    var on = soCard.classList.contains("no-nums");
    soApplyNums(on); pref.soNums = on; LS.set("n3ref", pref);
  });
  so.addEventListener("click", function (ev) { if (ev.target === so) soClose(); });

  document.addEventListener("click", function (ev) {
    var d = ev.target.closest(".draw");
    if (!d) return;
    ev.stopPropagation();
    soOpen(d.dataset.w, d);
  });

  if (!haveKvg) {
    Array.prototype.forEach.call(document.querySelectorAll(".draw"), function (b) {
      b.disabled = true; b.title = "stroke data not built";
    });
  }

  paint();
})();
