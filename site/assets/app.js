/* 2026 Midterm Forecast - shared front end.
   Plain ES5-compatible JS, no libraries. Every page embeds the slice of
   forecast data it needs as PAGE_DATA and calls the renderers it uses. */
(function (global) {
"use strict";

var NS = "http://www.w3.org/2000/svg";
var BIN_LABELS = ["Safe R", "Likely R", "Lean R", "Toss-up", "Lean D", "Likely D", "Safe D"];
var BIN_RANGES = ["under 5%", "5 to 20%", "20 to 40%", "40 to 60%", "60 to 80%", "80 to 95%", "95%+"];
var RATING_NAME = {solid: "Solid", likely: "Likely", lean: "Lean", tossup: "Toss-up"};
var STATE_NAMES = {AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",CT:"Connecticut",DE:"Delaware",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",MS:"Mississippi",MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",NH:"New Hampshire",NJ:"New Jersey",NM:"New Mexico",NY:"New York",NC:"North Carolina",ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",OR:"Oregon",PA:"Pennsylvania",RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",TN:"Tennessee",TX:"Texas",UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",WV:"West Virginia",WI:"Wisconsin",WY:"Wyoming"};
var ELECTION = new Date("2026-11-03T12:00:00Z");
var renderers = [];

function $(id) { return document.getElementById(id); }
function el(tag, attrs, parent) {
  var n = document.createElementNS(NS, tag);
  for (var k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function binOf(p) { return p < 0.05 ? 0 : p < 0.20 ? 1 : p < 0.40 ? 2 : p <= 0.60 ? 3 : p < 0.80 ? 4 : p < 0.95 ? 5 : 6; }
function binColor(p) { return css("--c" + binOf(p)); }
function pct(p) { if (p < 0.005) return "<1%"; if (p > 0.995) return ">99%"; return Math.round(p * 100) + "%"; }
function pct1(p) { if (p < 0.0005) return "<0.1%"; return (p * 100).toFixed(1) + "%"; }
function signed(x, digits) { return (x > 0 ? "+" : "") + (+x).toFixed(digits == null ? 1 : digits); }
function margin(x) { return (x >= 0 ? "D+" : "R+") + Math.abs(x).toFixed(1); }
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
  return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
function fmtDate(iso, withTime) {
  var opts = {month: "short", day: "numeric", year: "numeric", timeZone: "America/New_York"};
  if (withTime) { opts.hour = "numeric"; opts.minute = "2-digit"; }
  return new Date(iso).toLocaleString("en-US", opts) + (withTime ? " ET" : "");
}
function luminance(hex) {
  var h = hex.replace("#", ""), i, out = 0, w = [0.2126, 0.7152, 0.0722];
  for (i = 0; i < 3; i++) {
    var c = parseInt(h.substr(i * 2, 2), 16) / 255;
    out += w[i] * (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  }
  return out;
}
function inkOn(hex) { return luminance(hex) > 0.32 ? "#0b0b0b" : "#ffffff"; }
function headlineFor(pD, chamber, holder) {
  var p = Math.max(pD, 1 - pD), dFav = pD >= 0.5;
  var party = dFav ? "Democrats" : "Republicans";
  var verb = (dFav ? "D" : "R") === holder ? "keep" : "win";
  if (p < 0.55) return chamber + " control is a toss-up, " + party + " by a nose";
  var q = p < 0.65 ? "slightly favored" : p < 0.8 ? "favored" : p < 0.93 ? "clearly favored" : "very likely";
  return party + " " + q + " to " + verb + " the " + chamber;
}

/* ------------------------------------------------------------- tooltip */
var tip;
function showTip(html, ev) {
  if (!tip) return;
  tip.innerHTML = html; tip.style.opacity = 1;
  var pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  var x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip() { if (tip) tip.style.opacity = 0; }
function bindTip(node, htmlFn, focusable) {
  node.addEventListener("mousemove", function (ev) { showTip(htmlFn(), ev); });
  node.addEventListener("mouseleave", hideTip);
  if (focusable) {
    node.setAttribute("tabindex", "0");
    node.addEventListener("focus", function () {
      var r = node.getBoundingClientRect();
      showTip(htmlFn(), {clientX: r.left + r.width / 2, clientY: r.top + r.height / 2});
    });
    node.addEventListener("blur", hideTip);
  }
}
function swatch(color) { return '<span class="tsw" style="background:' + color + '"></span>'; }
function legendBins(id, extra) {
  var L = $(id); if (!L) return;
  L.innerHTML = "";
  for (var i = 6; i >= 0; i--) {
    var s = document.createElement("span");
    s.innerHTML = '<i class="sw" style="background:' + css("--c" + i) + '"></i>' + BIN_LABELS[i] +
      ' <span style="color:var(--muted)">' + BIN_RANGES[i] + "</span>";
    L.appendChild(s);
  }
  if (extra) { var e = document.createElement("span"); e.innerHTML = extra; L.appendChild(e); }
}

/* ------------------------------------------------------------ chambers */
function parliament(n, rows, r0, r1, cx, cy) {
  var radii = [], i, j;
  for (i = 0; i < rows; i++) radii.push(r0 + (r1 - r0) * (rows === 1 ? 0 : i / (rows - 1)));
  var sumR = radii.reduce(function (a, b) { return a + b; }, 0);
  var counts = radii.map(function (r) { return Math.round(n * r / sumR); });
  var diff = n - counts.reduce(function (a, b) { return a + b; }, 0);
  for (i = rows - 1; diff !== 0; i = (i - 1 + rows) % rows) {
    counts[i] += Math.sign(diff); diff -= Math.sign(diff);
  }
  var pts = [];
  radii.forEach(function (r, row) {
    var k = counts[row];
    for (j = 0; j < k; j++) {
      var a = Math.PI - (k === 1 ? Math.PI / 2 : Math.PI * j / (k - 1));
      pts.push({x: cx + r * Math.cos(a), y: cy - r * Math.sin(a), a: a, row: row});
    }
  });
  pts.sort(function (p, q) { return (q.a - p.a) || (p.row - q.row); });
  return pts;
}

function renderHemicycle(svgId, seats, majority, dotR, rows, legendId, notUpLegend) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var W = 520, cx = W / 2, cy = 268, r1 = 244, r0 = rows > 6 ? 92 : 110;
  var pts = parliament(seats.length, rows, r0, r1, cx, cy);
  seats.forEach(function (s, i) {
    var p = pts[i];
    var c = s.notUp ? (s.party === "D" ? css("--c6") : css("--c0")) : binColor(s.p);
    var node = el("circle", {cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: s.notUp ? dotR - 0.9 : dotR,
      "class": "seat", fill: s.notUp ? "none" : c, stroke: s.notUp ? c : css("--surface"),
      "stroke-width": s.notUp ? 1.8 : 1}, svg);
    bindTip(node, function () { return s.tip; });
  });
  var a = (pts[majority - 1].a + pts[Math.min(majority, pts.length - 1)].a) / 2;
  el("line", {x1: cx + (r0 - 16) * Math.cos(a), y1: cy - (r0 - 16) * Math.sin(a),
    x2: cx + (r1 + 14) * Math.cos(a), y2: cy - (r1 + 14) * Math.sin(a),
    stroke: css("--ink"), "stroke-width": 1.5}, svg);
  el("text", {x: cx, y: cy - 6, "text-anchor": "middle",
    style: "font-size:13px;fill:var(--ink);font-weight:600"}, svg).textContent = majority + " for majority";
  var dCount = seats.filter(function (s) { return s.notUp ? s.party === "D" : s.p > 0.5; }).length;
  el("text", {x: 14, y: cy + 22, style: "font-size:12px"}, svg).textContent = "More likely D";
  el("text", {x: W - 14, y: cy + 22, "text-anchor": "end", style: "font-size:12px"}, svg).textContent = "More likely R";
  el("text", {x: cx, y: cy + 22, "text-anchor": "middle", style: "font-size:12px"}, svg).textContent =
    dCount + " seats lean D today · " + (seats.length - dCount) + " lean R";
  if (legendId) legendBins(legendId, notUpLegend);
}

function renderHistogram(svgId, hist, majority, xlabel, chamberName) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var W = 520, H = 170, m = {l: 38, r: 10, t: 22, b: 34};
  var entries = Object.keys(hist).map(function (k) { return [+k, hist[k]]; })
    .filter(function (e) { return e[1] >= 0.0005; }).sort(function (a, b) { return a[0] - b[0]; });
  if (!entries.length) return;
  var x0 = entries[0][0], x1 = entries[entries.length - 1][0], n = x1 - x0 + 1;
  var maxP = Math.max.apply(null, entries.map(function (e) { return e[1]; }));
  var bw = (W - m.l - m.r) / n;
  var yMax = Math.ceil(maxP * 50) / 50 || 0.02;
  var y = function (v) { return H - m.b - (v / yMax) * (H - m.t - m.b); };
  var x = function (k) { return m.l + (k - x0) * bw; };
  [0, yMax / 2, yMax].forEach(function (v) {
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), "class": v === 0 ? "axis-line" : "gridline"}, svg);
    el("text", {x: m.l - 6, y: y(v) + 3.5, "text-anchor": "end"}, svg).textContent = Math.round(v * 100) + "%";
  });
  var gap = bw > 5 ? 2 : bw > 2.5 ? 1 : 0;
  entries.forEach(function (e) {
    var k = e[0], v = e[1], h = H - m.b - y(v);
    var r = el("rect", {x: (x(k) + gap / 2).toFixed(2), y: y(v).toFixed(2),
      width: Math.max(bw - gap, 0.8).toFixed(2), height: Math.max(h, 0.5).toFixed(2),
      rx: Math.min(2, (bw - gap) / 2), fill: k >= majority ? css("--dem") : css("--rep")}, svg);
    bindTip(r, function () {
      return "<b>" + k + " Democratic seats</b><br>" + pct1(v) + " of simulations<br>" +
        (k >= majority ? "Democratic" : "Republican") + " " + chamberName + " majority";
    });
  });
  var mx = x(majority);
  el("line", {x1: mx, x2: mx, y1: m.t - 8, y2: H - m.b, stroke: css("--ink"), "stroke-width": 1.2}, svg);
  el("text", {x: mx + 4, y: m.t - 2, style: "fill:var(--ink);font-size:11px"}, svg).textContent =
    majority + "+ = Democratic control";
  var step = n > 60 ? 10 : n > 25 ? 5 : 2;
  for (var k = Math.ceil(x0 / step) * step; k <= x1; k += step) {
    el("text", {x: x(k) + bw / 2, y: H - m.b + 14, "text-anchor": "middle"}, svg).textContent = k;
  }
  el("text", {x: (W + m.l) / 2, y: H - 4, "text-anchor": "middle"}, svg).textContent =
    xlabel + " (share of 100,000 simulations)";
}

/* --------------------------------------------------------------- story */
/* Why the forecast moved. A driver that helped Democrats runs right in blue,
   one that helped Republicans runs left in red, on a zero line: the two
   directions ARE the two parties, so the diverging palette is literal here.
   One shared scale across every day, so bar lengths compare between cards. */
function driverBars(host, drivers, chamber, scale) {
  /* Built in HTML rather than SVG on purpose: this card is full width on a
     desktop and 400px on a phone, and an SVG that stretches to fit scales its
     text with it, so labels come out either huge or unreadable. */
  host.innerHTML = "";
  var shown = drivers.filter(function (d) { return Math.abs(d.points[chamber]) >= 0.02; });
  if (!shown.length) {
    host.innerHTML = '<div class="story-resid">No single input moved this chamber measurably.</div>';
    return;
  }
  shown.forEach(function (d) {
    var v = d.points[chamber], helpsDem = v >= 0;
    var pctOfHalf = Math.min(Math.abs(v) / scale, 1) * 50;
    var row = document.createElement("div");
    row.className = "dbar";
    row.innerHTML =
      '<div class="dbar-label">' + esc(d.label) + "</div>" +
      '<div class="dbar-track"><span class="dbar-zero"></span><span class="dbar-fill"></span></div>' +
      '<div class="dbar-val">' + signed(v, 1) + "</div>";
    var fill = row.querySelector(".dbar-fill");
    fill.style.background = helpsDem ? css("--dem") : css("--rep");
    fill.style.width = Math.max(pctOfHalf, 0.4) + "%";
    if (helpsDem) { fill.style.left = "50%"; } else { fill.style.right = "50%"; }
    row.querySelector(".dbar-val").style.color = helpsDem ? css("--dem") : css("--rep");
    bindTip(fill, function () {
      return "<b>" + esc(d.label) + "</b><br>" + signed(v, 2) + " points to the " +
        (helpsDem ? "Democrats" : "Republicans");
    }, true);
    host.appendChild(row);
  });
  var foot = document.createElement("div");
  foot.className = "dbar-foot";
  foot.innerHTML = "<span>helps Republicans</span><span>helps Democrats</span>";
  host.appendChild(foot);
}

var CHAMBER_WORD = {house: "House", senate: "Senate"};

function moveWord(v) {
  var a = Math.abs(v);
  if (a < 0.2) return "barely moved";
  return (v > 0 ? "rose " : "fell ") + a.toFixed(1) + " points";
}

function renderStory(containerId, days, chamber) {
  var box = $(containerId); if (!box) return;
  box.innerHTML = "";
  var scale = 0.5;
  days.forEach(function (d) {
    (d.change ? d.change.drivers : []).forEach(function (dr) {
      scale = Math.max(scale, Math.abs(dr.points[chamber]));
    });
  });

  days.forEach(function (d) {
    var card = document.createElement("div");
    card.className = "story";
    var ch = d.change;
    var total = ch ? ch.total[chamber] : null;
    var head = '<div class="story-head"><div><div class="story-date">' + esc(d.label) + "</div>" +
      '<div class="story-move">' +
        (total === null
          ? (d.first ? "The first run of the rebuilt model, so there is nothing to compare against yet."
                     : "This run predates the day-by-day explanation, so no breakdown was recorded.")
          : "Democrats' " + (CHAMBER_WORD[chamber] || chamber) + " chances " + moveWord(total) +
            ', to <b>' + Math.round(d[chamber] * 100) + "%</b>") +
      "</div></div>" +
      (total === null ? "" :
        '<div class="story-delta ' + (total >= 0 ? "up" : "down") + '">' + signed(total, 1) + " pt</div>") +
      "</div>";
    card.innerHTML = head;

    if (ch && ch.drivers.length) {
      var wrap = document.createElement("div");
      wrap.innerHTML = '<div class="story-sub">What moved it</div><div class="dbars"></div>';
      card.appendChild(wrap);
      driverBars(wrap.querySelector(".dbars"), ch.drivers, chamber, scale);
      if (Math.abs(ch.residual[chamber]) >= 0.15) {
        var note = document.createElement("div");
        note.className = "story-resid";
        note.textContent = "Inputs interacting with each other account for the remaining " +
          signed(ch.residual[chamber], 1) + " points.";
        card.appendChild(note);
      }
    }

    if (ch && ch.facts.length) {
      card.innerHTML += '<div class="story-sub">What changed in the data</div>' +
        '<ul class="story-facts">' + ch.facts.map(function (f) {
          return '<li class="k-' + esc(f.kind) + '">' + esc(f.text) + "</li>";
        }).join("") + "</ul>";
    }

    if (ch && ch.races && ch.races.length) {
      card.innerHTML += '<div class="story-sub">Races that moved</div>' +
        '<div class="story-races">' + ch.races.slice(0, 6).map(function (r) {
          var up = r.shift >= 0;
          return '<div class="story-race"><span class="rc-state">' +
            esc(STATE_NAMES[r.state] || r.state) + "</span>" +
            '<span class="rc-shift" style="color:' + (up ? css("--dem") : css("--rep")) + '">' +
              signed(r.shift, 1) + " pt</span>" +
            '<span class="rc-to">to ' + Math.round(r.to) + "% D</span>" +
            '<span class="rc-why">' + esc(r.why) + "</span></div>";
        }).join("") + "</div>";
    }
    box.appendChild(card);
  });
}

/* ------------------------------------------------------------- markets */
/* Source identity (model / Kalshi / Polymarket) is carried by SHAPE and a
   direct label, never by hue: on this site blue means Democratic and red
   means Republican, so tinting a source would read as a party. */
var SRC_SHAPE = {model: "circle", kalshi: "square", polymarket: "triangle"};

function srcMark(svg, kind, cx, cy, size, solid) {
  var half = size / 2, node;
  if (kind === "square") {
    node = el("rect", {x: cx - half, y: cy - half, width: size, height: size, rx: 1.5}, svg);
  } else if (kind === "triangle") {
    node = el("polygon", {points: [cx + "," + (cy - half - 0.5),
      (cx + half + 0.5) + "," + (cy + half), (cx - half - 0.5) + "," + (cy + half)].join(" ")}, svg);
  } else {
    node = el("circle", {cx: cx, cy: cy, r: half + 0.5}, svg);
  }
  node.setAttribute("fill", solid ? css("--ink") : css("--surface"));
  node.setAttribute("stroke", css("--ink"));
  node.setAttribute("stroke-width", "2");
  return node;
}

function srcLegend(id, sources) {
  var L = $(id); if (!L) return;
  L.innerHTML = "";
  sources.forEach(function (s) {
    var span = document.createElement("span");
    span.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" ' +
      'style="vertical-align:-2px;margin-right:5px"><g></g></svg>' + esc(s.label);
    L.appendChild(span);
    srcMark(span.querySelector("g"), SRC_SHAPE[s.key] || "circle", 7, 7, 8, s.key === "model");
  });
}

/* Model vs each venue on one 0-100% axis, one row per chamber. The span
   between the extremes is drawn as a rule so the size of the disagreement
   is the thing you see first. */
function renderMarketCompare(svgId, rows, legendId) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var W = 520, rowH = 74, m = {l: 12, r: 12, t: 26, b: 24};
  var H = m.t + m.b + rows.length * rowH;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  var x = function (p) { return m.l + 34 + p * (W - m.l - m.r - 68); };

  [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
    el("line", {x1: x(v), x2: x(v), y1: m.t - 6, y2: H - m.b + 2,
      "class": v === 0.5 ? "axis-line" : "gridline"}, svg);
    el("text", {x: x(v), y: H - m.b + 16, "text-anchor": "middle"}, svg)
      .textContent = Math.round(v * 100) + "%";
  });
  el("text", {x: x(0.5), y: m.t - 12, "text-anchor": "middle",
    style: "fill:var(--muted);font-size:11px"}, svg).textContent = "coin flip";

  rows.forEach(function (row, i) {
    var yTop = m.t + i * rowH, yDot = yTop + 34;
    el("text", {x: m.l, y: yTop + 12, style: "fill:var(--ink);font-size:13px;font-weight:600"}, svg)
      .textContent = row.chamber;

    var present = row.points.filter(function (pt) { return pt.prob != null; });
    if (!present.length) return;
    var lo = Math.min.apply(null, present.map(function (pt) { return pt.prob; }));
    var hi = Math.max.apply(null, present.map(function (pt) { return pt.prob; }));
    if (hi > lo) {
      el("line", {x1: x(lo), x2: x(hi), y1: yDot, y2: yDot,
        stroke: css("--axis"), "stroke-width": 2, "stroke-linecap": "round"}, svg);
      el("text", {x: x((lo + hi) / 2), y: yDot - 13, "text-anchor": "middle",
        style: "fill:var(--muted);font-size:11px"}, svg)
        .textContent = Math.round((hi - lo) * 100) + " pt spread";
    }
    // Three sources a few points apart put their labels on top of each other,
    // so a label that would collide drops to a second line instead.
    var placed = [];
    present.slice().sort(function (a, b) { return a.prob - b.prob; }).forEach(function (pt) {
      var node = srcMark(svg, SRC_SHAPE[pt.key] || "circle", x(pt.prob), yDot, 11, pt.key === "model");
      bindTip(node, function () {
        return "<b>" + esc(pt.label) + "</b><br>" + pct(pt.prob) + " chance Democrats win the " +
          row.chamber.toLowerCase() +
          (pt.volume ? "<br>$" + Number(pt.volume).toLocaleString() + " traded" : "");
      }, true);
      var px = x(pt.prob), lane = 0;
      while (placed.some(function (q) { return q.lane === lane && Math.abs(q.x - px) < 34; })) lane++;
      placed.push({x: px, lane: lane});
      el("text", {x: px, y: yDot + 22 + lane * 13, "text-anchor": "middle",
        style: "fill:var(--ink-2);font-size:11px"}, svg).textContent = pct(pt.prob);
    });
  });
  srcLegend(legendId, rows[0].points.map(function (pt) { return {key: pt.key, label: pt.label}; }));
}

/* Paired bars over a shared set of buckets: model solid, market outlined.
   Distinguishable without color, which matters here because hue is spoken
   for by party. */
function renderPairedBars(svgId, buckets, legendId, valueLabel) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var n = buckets.length; if (!n) return;
  // Tilted tick labels need room below the axis, so the plot takes its
  // height from the element's own viewBox rather than assuming one.
  var vb = (svg.getAttribute("viewBox") || "0 0 520 210").split(/\s+/);
  var W = +vb[2] || 520, H = +vb[3] || 210;
  var m = {l: 36, r: 10, t: 16, b: n > 10 ? 66 : 46};
  var maxP = 0;
  buckets.forEach(function (b) { maxP = Math.max(maxP, b.model || 0, b.market || 0); });
  var yMax = Math.ceil(maxP * 20) / 20 || 0.05;
  var y = function (v) { return H - m.b - (v / yMax) * (H - m.t - m.b); };
  var slot = (W - m.l - m.r) / n;
  var bw = Math.max((slot - 6) / 2, 2);

  [0, yMax / 2, yMax].forEach(function (v) {
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v),
      "class": v === 0 ? "axis-line" : "gridline"}, svg);
    el("text", {x: m.l - 6, y: y(v) + 3.5, "text-anchor": "end"}, svg)
      .textContent = Math.round(v * 100) + "%";
  });

  buckets.forEach(function (b, i) {
    var left = m.l + i * slot + 3;
    [["model", left, true], ["market", left + bw + 2, false]].forEach(function (spec) {
      var key = spec[0], v = b[key];
      if (v == null) return;
      var h = Math.max(H - m.b - y(v), 0.8);
      var rect = el("rect", {x: spec[1].toFixed(2), y: y(v).toFixed(2),
        width: bw.toFixed(2), height: h.toFixed(2), rx: Math.min(3, bw / 2)}, svg);
      if (spec[2]) {
        rect.setAttribute("fill", css("--ink"));
      } else {
        rect.setAttribute("fill", css("--surface"));
        rect.setAttribute("stroke", css("--ink"));
        rect.setAttribute("stroke-width", "2");
      }
      bindTip(rect, function () {
        return "<b>" + esc(b.label) + "</b><br>" +
          (key === "model" ? "Model" : "Market") + ": " + pct1(v) +
          (b.model != null && b.market != null
            ? "<br><span style='opacity:.75'>Model " + pct1(b.model) + " · Market " + pct1(b.market) + "</span>"
            : "");
      }, true);
    });
    // Twelve seat buckets will not fit side by side at this width, so past
    // ten they tilt rather than overlap or get dropped.
    var cx = m.l + i * slot + slot / 2;
    var tick = el("text", {"font-size": "10px"}, svg);
    if (n > 10) {
      tick.setAttribute("x", cx); tick.setAttribute("y", H - m.b + 12);
      tick.setAttribute("text-anchor", "end");
      tick.setAttribute("transform", "rotate(-40 " + cx.toFixed(1) + " " + (H - m.b + 12) + ")");
    } else {
      tick.setAttribute("x", cx); tick.setAttribute("y", H - m.b + 15);
      tick.setAttribute("text-anchor", "middle");
    }
    tick.textContent = b.label;
  });
  el("text", {x: (W + m.l) / 2, y: H - 6, "text-anchor": "middle"}, svg).textContent = valueLabel;
  srcLegend(legendId, [{key: "model", label: "This model"}, {key: "kalshi", label: "Market"}]);
}

function money(v) {
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return "$" + Math.round(v / 1e3) + "K";
  return "$" + Math.round(v);
}

/* The popular-vote market's own bands. Party colour is the right encoding
   here, because each band IS a partisan margin. */
function renderMarginBins(svgId, bins, impliedMargin) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var W = 520, H = 150, m = {l: 34, r: 10, t: 14, b: 40};
  var n = bins.length; if (!n) return;
  var maxP = Math.max.apply(null, bins.map(function (b) { return b.prob; }));
  var yMax = Math.ceil(maxP * 20) / 20 || 0.05;
  var y = function (v) { return H - m.b - (v / yMax) * (H - m.t - m.b); };
  var slot = (W - m.l - m.r) / n;

  [0, yMax].forEach(function (v) {
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v),
      "class": v === 0 ? "axis-line" : "gridline"}, svg);
    el("text", {x: m.l - 6, y: y(v) + 3.5, "text-anchor": "end"}, svg)
      .textContent = Math.round(v * 100) + "%";
  });

  bins.forEach(function (b, i) {
    var v = b.prob, h = Math.max(H - m.b - y(v), 0.8);
    var isRep = /republican/i.test(b.label);
    var rect = el("rect", {x: (m.l + i * slot + 2).toFixed(2), y: y(v).toFixed(2),
      width: Math.max(slot - 4, 1).toFixed(2), height: h.toFixed(2), rx: 3,
      fill: isRep ? css("--rep") : css("--dem")}, svg);
    bindTip(rect, function () {
      return "<b>" + esc(b.label) + "</b><br>" + pct1(v) + " chance<br>" +
        "<span style='opacity:.75'>priced on Kalshi</span>";
    }, true);
    el("text", {x: m.l + i * slot + slot / 2, y: H - m.b + 13, "text-anchor": "middle",
      style: "font-size:9.5px"}, svg).textContent = b.short;
  });
  el("text", {x: (W + m.l) / 2, y: H - 6, "text-anchor": "middle"}, svg).textContent =
    "Democratic margin in the House popular vote, priced bands";
}

/* Compact overview row: the model's number, and the range the two exchanges
   are quoting, on one track per chamber. */
function renderMarketStrip(containerId, rows) {
  var box = $(containerId); if (!box) return;
  box.innerHTML = "";
  rows.forEach(function (r) {
    var agree = Math.abs(r.model - (r.low + r.high) / 2) < 0.05;
    var wrap = document.createElement("div");
    wrap.className = "mstrip";
    wrap.innerHTML =
      '<div class="mstrip-name">' + esc(r.chamber) + "</div>" +
      '<div class="mstrip-track"><div class="mstrip-band"></div>' +
        '<div class="mstrip-model"></div></div>' +
      '<div class="mstrip-read"><b>' + pct(r.model) + "</b> model · " +
        (r.low === r.high ? pct(r.low) : pct(r.low) + " to " + pct(r.high)) + " market</div>" +
      '<div class="mstrip-verdict">' + (agree ? "broadly agree" :
        Math.round(Math.abs(r.model - (r.low + r.high) / 2) * 100) + " pt apart") + "</div>";
    box.appendChild(wrap);
    var band = wrap.querySelector(".mstrip-band");
    band.style.left = (r.low * 100) + "%";
    band.style.width = Math.max((r.high - r.low) * 100, 1.2) + "%";
    wrap.querySelector(".mstrip-model").style.left = (r.model * 100) + "%";
  });
}

/* Attention split between the two candidates in a race. Party colour is
   correct here: the two halves of the bar ARE the two parties. */
function renderAttention(containerId, rows) {
  var box = $(containerId); if (!box) return;
  box.innerHTML = "";
  rows.forEach(function (r) {
    var demPct = r.dem_share * 100;
    var wrap = document.createElement("div");
    wrap.className = "attn";
    wrap.innerHTML =
      '<div class="attn-name">' + esc(r.state_name) + (r.special ? " <span>(special)</span>" : "") + "</div>" +
      '<div class="attn-bar"><div class="attn-d"></div><div class="attn-r"></div></div>' +
      '<div class="attn-read">' +
        '<span class="attn-side">' + swatch(css("--dem")) + esc(r.dem_last) + " " + Math.round(demPct) + "%</span>" +
        '<span class="attn-side">' + swatch(css("--rep")) + esc(r.rep_last) + " " + Math.round(100 - demPct) + "%</span>" +
      "</div>" +
      '<div class="attn-total">' + Number(r.total_daily).toLocaleString() + "/day" +
        (r.surge_note ? ' <span class="attn-surge">' + esc(r.surge_note) + "</span>" : "") + "</div>";
    box.appendChild(wrap);

    var d = wrap.querySelector(".attn-d"), rr = wrap.querySelector(".attn-r");
    d.style.width = demPct + "%";
    d.style.background = css("--dem");
    rr.style.width = (100 - demPct) + "%";
    rr.style.background = css("--rep");
    bindTip(d, function () {
      return "<b>" + esc(r.dem) + "</b><br>" + Number(r.dem_views).toLocaleString() +
        " views a day<br>" + r.dem_surge + "x their own baseline";
    }, true);
    bindTip(rr, function () {
      return "<b>" + esc(r.rep) + "</b><br>" + Number(r.rep_views).toLocaleString() +
        " views a day<br>" + r.rep_surge + "x their own baseline";
    }, true);
  });
}

function renderMarketRaces(containerId, rows) {
  var box = $(containerId); if (!box) return;
  var html = '<table class="grid"><thead><tr><th>Race</th><th class="num">Model</th>' +
    '<th class="num">Market</th><th class="num">Gap</th><th class="num">Traded</th></tr></thead><tbody>';
  rows.forEach(function (r) {
    var gap = r.market - r.model;
    var big = Math.abs(gap) >= 0.10;
    var name = (STATE_NAMES[r.state] || r.state) + (r.special ? " (special)" : "");
    html += "<tr><td>" + esc(name) + "</td>" +
      '<td class="num">' + pct(r.model) + "</td>" +
      '<td class="num">' + pct(r.market) + "</td>" +
      '<td class="num" style="color:' + (big ? "var(--ink)" : "var(--muted)") +
        (big ? ";font-weight:600" : "") + '">' + signed(gap * 100, 0) + " pt</td>" +
      '<td class="num" style="color:var(--muted)">' + money(r.volume) + "</td></tr>";
  });
  box.innerHTML = html + "</tbody></table>";
}

/* --------------------------------------------------------------- races */
function senateTip(r) {
  var cands = [];
  if (r.dem_candidate) cands.push(swatch(css("--dem")) + esc(r.dem_candidate) + (r.challenger_caucus === "independent" ? "" : " (D)"));
  if (r.rep_candidate) cands.push(swatch(css("--rep")) + esc(r.rep_candidate) + " (R)");
  var who = r.challenger_caucus === "independent" ? "Osborn (I)" : "Democrats";
  var atmo = r.atmospherics_adj ? "<br>News-momentum adjustment: " + signed(r.atmospherics_adj * 100, 0) + " pts to D chance" : "";
  // Where this race's expected margin came from: its polling, its rating, or a
  // blend, which is worth showing because the two often disagree.
  var poll = "";
  if (r.polling && r.polling.poll_margin != null) {
    poll = "<br>Polls " + margin(r.polling.poll_margin) + " · rating implies " +
      margin(r.polling.rating_margin) + "<br><span style='opacity:.8'>" +
      Math.round(r.polling.weight * 100) + "% of the baseline is the polling average</span>";
  }
  return "<b>" + STATE_NAMES[r.state] + (r.seat_id.indexOf("special") > -1 ? " (special)" : "") + "</b><br>" +
    (cands.join("<br>") || "Nominees to be decided") +
    "<br>Cook: " + RATING_NAME[r.rating] + (r.rating === "tossup" ? "" : " " + r.lean) + " · held by " + r.held_by +
    "<br><b>" + who + ": " + pct(r.dem_win_prob) + "</b> · Republicans: " + pct(1 - r.dem_win_prob) + poll + atmo +
    (r.notes ? "<br><span style='opacity:.8'>" + esc(r.notes) + "</span>" : "");
}

function renderSenateMap(svgId, senate, geo, legendId) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var byState = {};
  senate.seats.forEach(function (r) { byState[r.state] = r; });
  Object.keys(geo.states).forEach(function (code) {
    var g = geo.states[code], race = byState[code];
    var p = el("path", {d: g.d, "class": "state", fill: race ? binColor(race.dem_win_prob) : css("--notup")}, svg);
    p.setAttribute("aria-label", STATE_NAMES[code] + (race ? ": Democrats " + pct(race.dem_win_prob) : ": no Senate race"));
    bindTip(p, function () {
      return race ? senateTip(race) : "<b>" + STATE_NAMES[code] + "</b><br>No Senate race in 2026";
    }, !!race);
  });
  var SMALL = {NH: 1, MA: 1, RI: 1, CT: 1, NJ: 1, DE: 1, MD: 1, VT: 1};
  Object.keys(geo.states).forEach(function (code) {
    if (SMALL[code]) return;
    var g = geo.states[code], race = byState[code];
    el("text", {x: g.cx, y: g.cy + 4, "text-anchor": "middle", "class": "maplabel",
      style: race ? "" : "fill:var(--muted)"}, svg).textContent = code;
  });
  var chips = ["VT", "NH", "MA", "RI", "CT", "NJ", "DE", "MD"].map(function (c) {
    return {c: c, g: geo.states[c]};
  }).sort(function (a, b) { return a.g.cy - b.g.cy; });
  var yy = 150;
  chips.forEach(function (item) {
    var race = byState[item.c];
    var fill = race ? binColor(race.dem_win_prob) : css("--notup");
    var cy = Math.max(yy, item.g.cy - 20); yy = cy + 30;
    el("line", {x1: item.g.cx, y1: item.g.cy, x2: 928, y2: cy, stroke: css("--axis"), "stroke-width": 1}, svg);
    var r = el("rect", {x: 930, y: cy - 11, width: 44, height: 22, rx: 4, fill: fill,
      stroke: race ? "none" : css("--axis"), "class": "state"}, svg);
    bindTip(r, function () {
      return race ? senateTip(race) : "<b>" + STATE_NAMES[item.c] + "</b><br>No Senate race in 2026";
    }, !!race);
    el("text", {x: 952, y: cy + 4, "text-anchor": "middle",
      style: "font-size:11px;font-weight:600;pointer-events:none;fill:" + (race ? inkOn(fill) : css("--muted"))},
      svg).textContent = item.c;
  });
  if (legendId) legendBins(legendId, '<i class="sw" style="background:' + css("--notup") +
    ";border:1px solid " + css("--axis") + '"></i>No race');
}

function renderWatch(containerId, senate, limit) {
  var box = $(containerId); if (!box) return;
  var watch = senate.seats.filter(function (r) { return r.dem_win_prob > 0.03 && r.dem_win_prob < 0.97; })
    .sort(function (a, b) { return Math.abs(a.dem_win_prob - 0.5) - Math.abs(b.dem_win_prob - 0.5); });
  var shown = limit ? watch.slice(0, limit) : watch;
  box.innerHTML = shown.map(function (r) {
    var ind = r.challenger_caucus === "independent";
    var d = r.dem_candidate ? esc(r.dem_candidate) + (ind ? "" : " (D)") : "Democratic nominee";
    var rr = r.rep_candidate ? esc(r.rep_candidate) + " (R)" : "Republican nominee";
    var fav = r.dem_win_prob >= 0.5 ? (ind ? "Osborn" : "D") : "R";
    var atmo = r.atmospherics_adj ? " · news momentum " + signed(r.atmospherics_adj * 100, 0) + " pts D" : "";
    return '<div class="wcard"><div class="top"><span class="st">' + STATE_NAMES[r.state] +
      (r.seat_id.indexOf("special") > -1 ? " (special)" : "") + '</span><span class="p">' +
      pct(Math.max(r.dem_win_prob, 1 - r.dem_win_prob)) + " " + fav + '</span></div>' +
      '<span class="pbar" role="img" aria-label="Democratic chance ' + pct(r.dem_win_prob) + '"><i style="width:' +
      (r.dem_win_prob * 100) + '%"></i></span><div class="who"><b>' + d + "</b> vs <b>" + rr + "</b><br>Cook: " +
      RATING_NAME[r.rating] + (r.rating === "tossup" ? "" : " " + r.lean) + " · held by " + r.held_by + atmo +
      "</div></div>";
  }).join("");
}

function renderSenateTable(tableId, senate) {
  var t = $(tableId); if (!t) return;
  var rows = senate.seats.slice().sort(function (a, b) { return b.dem_win_prob - a.dem_win_prob; });
  t.innerHTML = "<thead><tr><th>State</th><th>Democrat</th><th>Republican</th><th>Held by</th>" +
    "<th>Cook rating</th><th class='num'>D win</th><th class='num'>Expected margin</th></tr></thead><tbody>" +
    rows.map(function (r) {
      return "<tr><td>" + STATE_NAMES[r.state] + (r.seat_id.indexOf("special") > -1 ? " (special)" : "") +
        "</td><td>" + esc(r.dem_candidate || "") + "</td><td>" + esc(r.rep_candidate || "") + "</td><td>" + r.held_by +
        "</td><td>" + RATING_NAME[r.rating] + (r.rating === "tossup" ? "" : " " + r.lean) +
        "</td><td class='num'>" + pct(r.dem_win_prob) + "</td><td class='num'>" + margin(r.expected_margin) + "</td></tr>";
    }).join("") + "</tbody>";
}

/* ---------------------------------------------------------- house maps */
function ratingText(code) {
  var m = {S: "Solid", L: "Likely", N: "Lean", T: "Toss-up"};
  if (!code) return "";
  return code.charAt(0) === "T" ? "Toss-up" : m[code.charAt(0)] + " " + code.charAt(1);
}
function houseTip(id, p, meta, ratings) {
  var m = meta[id] || {}, code = ratings[id];
  return "<b>" + id + "</b> · " + (STATE_NAMES[m.state] || "") + (m.redrawn ? " · new 2026 map" : "") + "<br>" +
    (m.incumbent ? esc(m.incumbent) + "<br>" : "") + "Cook: " + (ratingText(code) || "n/a") +
    (m.held_by ? " · held by " + m.held_by : "") +
    "<br><b>Democrats " + pct(p) + "</b> · Republicans " + pct(1 - p);
}

function renderHouseMap(svgId, house, hex, meta, legendId) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  svg.setAttribute("viewBox", "0 0 " + hex.width + " " + hex.height);
  var size = hex.hex_size * 0.965, i;
  var corners = [];
  for (i = 0; i < 6; i++) corners.push([Math.cos(Math.PI / 180 * (60 * i - 30)), Math.sin(Math.PI / 180 * (60 * i - 30))]);
  var byState = {};
  hex.districts.forEach(function (d) { (byState[d.state] = byState[d.state] || []).push(d); });
  Object.keys(byState).forEach(function (st) {
    var cells = byState[st], label = hex.labels[st];
    var order = cells.slice().sort(function (a, b) {
      return Math.hypot(a.x - label[0], a.y - label[1]) - Math.hypot(b.x - label[0], b.y - label[1]);
    });
    var ids = cells.map(function (c) { return c.id; }).sort(function (a, b) {
      return (house.district_probs[b] - house.district_probs[a]) || a.localeCompare(b, "en", {numeric: true});
    });
    order.forEach(function (cell, idx) {
      var id = ids[idx], p = house.district_probs[id];
      var pts = corners.map(function (c) {
        return (cell.x + c[0] * size).toFixed(1) + "," + (cell.y + c[1] * size).toFixed(1);
      }).join(" ");
      var h = el("polygon", {points: pts, "class": "hex", fill: p == null ? css("--notup") : binColor(p)}, svg);
      h.setAttribute("aria-label", id + ": Democrats " + pct(p));
      bindTip(h, function () { return houseTip(id, p, meta, house.district_ratings || {}); });
    });
  });
  el("path", {d: hex.borders, "class": "borders"}, svg);
  Object.keys(hex.labels).forEach(function (code) {
    var xy = hex.labels[code];
    el("text", {x: xy[0], y: xy[1] + 4, "text-anchor": "middle", "class": "maplabel"}, svg).textContent = code;
  });
  if (legendId) legendBins(legendId);
}

var houseFilter = "tossup";
function renderHouseTable(tabsId, tableId, house, meta) {
  var tabs = $(tabsId), table = $(tableId);
  if (!table) return;
  var ratings = house.district_ratings || {};
  var all = Object.keys(house.district_probs).map(function (id) {
    return {id: id, p: house.district_probs[id], m: meta[id] || {}, code: ratings[id] || ""};
  });
  var groups = {
    tossup: ["Toss-ups", function (r) { return r.code.charAt(0) === "T"; }],
    lean: ["Lean", function (r) { return r.code.charAt(0) === "N"; }],
    likely: ["Likely", function (r) { return r.code.charAt(0) === "L"; }],
    close: ["Closest by model", function (r) { return r.p >= 0.4 && r.p <= 0.6; }],
    all: ["All 435", function () { return true; }]
  };
  if (tabs) {
    tabs.innerHTML = "";
    Object.keys(groups).forEach(function (k) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = groups[k][0] + " (" + all.filter(groups[k][1]).length + ")";
      b.setAttribute("aria-pressed", k === houseFilter ? "true" : "false");
      b.addEventListener("click", function () { houseFilter = k; renderHouseTable(tabsId, tableId, house, meta); });
      tabs.appendChild(b);
    });
  }
  var rows = all.filter(groups[houseFilter][1]).sort(function (a, b) {
    return Math.abs(a.p - 0.5) - Math.abs(b.p - 0.5) || a.id.localeCompare(b.id, "en", {numeric: true});
  });
  table.innerHTML = "<thead><tr><th>District</th><th>Incumbent / holder</th><th>Cook rating</th><th>Held by</th>" +
    "<th class='num'>D win</th><th></th></tr></thead><tbody>" + rows.map(function (r) {
      return "<tr><td>" + r.id + (r.m.redrawn ? ' <span style="color:var(--muted)" title="New 2026 map">*</span>' : "") +
        "</td><td>" + esc(r.m.incumbent || "") + "</td><td>" + ratingText(r.code) + "</td><td>" + (r.m.held_by || "") +
        "</td><td class='num'>" + pct(r.p) + '</td><td><span class="pbar"><i style="width:' + (r.p * 100) +
        '%"></i></span></td></tr>';
    }).join("") + '</tbody><caption style="caption-side:bottom;text-align:left;padding-top:8px;color:var(--muted);font-size:12px">' +
    "* State voting on a new 2026 map. Blue share of each bar is the Democratic chance.</caption>";
}

/* ------------------------------------------------------------- economy */
function effectChip(scoreForPres) {
  if (scoreForPres == null) return '<span class="effect"><i class="sw" style="background:var(--c3)"></i>Context only</span>';
  if (scoreForPres <= -0.15) return '<span class="effect"><i class="sw" style="background:var(--dem)"></i>&#9660; Hurts GOP, helps Dems</span>';
  if (scoreForPres >= 0.15) return '<span class="effect"><i class="sw" style="background:var(--rep)"></i>&#9650; Helps GOP</span>';
  return '<span class="effect"><i class="sw" style="background:var(--c3)"></i>Roughly neutral</span>';
}
function sourceLinks(list) {
  return (list || []).map(function (s) {
    // Sources come in three shapes: {name, url}, a bare URL string, and a bare
    // outlet name typed by hand. Only the first two can be linked; the third
    // still gets named rather than rendered as a dead link.
    var name = (typeof s === "string") ? s : (s && s.name) || "";
    var url = (typeof s === "string") ? s : (s && s.url) || "";
    var isLink = /^https?:\/\//.test(url);
    if (isLink && name === url) { name = url.split("/")[2] || url; }
    if (!isLink) { return esc(name); }
    return '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(name) + "</a>";
  }).filter(Boolean).join(" · ");
}

function renderEnvironment(containerId, env) {
  var box = $(containerId); if (!box) return;
  var f = env.fundamentals, comps = f.economic_components;
  var W = 520, rowH = 24, m = {l: 150, r: 60, t: 8};
  var maxAbs = Math.max.apply(null, [0.6].concat(comps.map(function (c) { return Math.abs(c.contribution * 3); })));
  var H = m.t + comps.length * rowH + 26;
  var x0 = m.l + (W - m.l - m.r) / 2, sc = (W - m.l - m.r) / 2 / maxAbs;
  var bars = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="How each economic reading moves the Democratic margin">';
  bars += '<line x1="' + x0 + '" x2="' + x0 + '" y1="' + (m.t - 4) + '" y2="' + (H - 22) + '" class="axis-line"/>';
  comps.slice().sort(function (a, b) { return a.contribution - b.contribution; }).forEach(function (c, i) {
    var pts = -c.contribution * 3, y = m.t + i * rowH, w = Math.abs(pts) * sc;
    var x = pts >= 0 ? x0 : x0 - w;
    bars += '<text x="' + (m.l - 8) + '" y="' + (y + 15) + '" text-anchor="end" style="fill:var(--ink);font-size:12px">' + esc(c.label) + "</text>";
    bars += '<rect x="' + x.toFixed(1) + '" y="' + (y + 5) + '" width="' + Math.max(w, 1).toFixed(1) +
      '" height="14" rx="3" fill="' + (pts >= 0 ? css("--dem") : css("--rep")) + '"><title>' + esc(c.label) + ": " +
      signed(pts, 2) + " pts to Dem margin</title></rect>";
    bars += '<text x="' + (pts >= 0 ? x + w + 5 : x - 5) + '" y="' + (y + 16) + '" text-anchor="' +
      (pts >= 0 ? "start" : "end") + '">' + signed(pts, 2) + "</text>";
  });
  bars += '<text x="' + (x0 - 6) + '" y="' + (H - 6) + '" text-anchor="end">&#9664; helps GOP</text><text x="' +
    (x0 + 6) + '" y="' + (H - 6) + '">helps Dems &#9654;</text></svg>';

  box.innerHTML =
    '<div class="chamber-label">How the national environment is built</div>' +
    '<p class="lede" style="margin-top:6px">Both simulations shift every race by the national environment: a blend of what the polls say today and what the fundamentals say a midterm like this usually looks like. Polls carry more weight as Election Day nears (' +
    Math.round(env.poll_weight * 100) + "% now).</p>" +
    '<div class="eq"><div class="term"><b>' + margin(env.generic_ballot) + "</b><span>Generic ballot polls × " +
    Math.round(env.poll_weight * 100) + '%</span></div><span class="op">+</span><div class="term"><b>' +
    margin(f.prior_dem_margin) + "</b><span>Fundamentals × " + Math.round((1 - env.poll_weight) * 100) +
    '%</span></div><span class="op">=</span><div class="term" style="border-color:var(--ink)"><b>' +
    margin(env.dem_margin) + "</b><span>National environment</span></div></div>" +
    '<div class="grid2"><div><div class="chamber-label" style="margin-bottom:6px">Fundamentals, in points of Democratic margin</div>' +
    "<table><tbody>" +
    "<tr><td>Midterm penalty for the president's party</td><td class='num'>" + signed(f.midterm_baseline_pts) + "</td></tr>" +
    "<tr><td>Presidential approval (net " + signed(f.approval_net, 0) + ")</td><td class='num'>" + signed(f.approval_pts) + "</td></tr>" +
    "<tr><td>Economy (index " + signed(f.economic_index, 2) + " on a -1 to +1 scale)</td><td class='num'>" + signed(f.economic_pts) + "</td></tr>" +
    "<tr><td><b>Fundamentals total</b></td><td class='num'><b>" + margin(f.prior_dem_margin) + "</b></td></tr>" +
    '</tbody></table><p class="note">Since World War II the president\'s party has lost House seats in all but two midterms (1998 and 2002). Approval is the strongest single signal: presidents under 50% have lost about 37 seats on average (Gallup). The economic index nudges that by up to 3 points either way.</p></div>' +
    '<div><div class="chamber-label" style="margin-bottom:6px">What each economic reading does to the Democratic margin</div>' + bars + "</div></div>";
}

function renderIndicators(containerId, politicalId, display, env) {
  var byId = {};
  env.fundamentals.economic_components.forEach(function (c) { byId[c.id] = c.score; });
  var box = $(containerId);
  if (box) box.innerHTML = display.indicators.map(function (ind) {
    var chg = [ind.compare_display ? ind.compare_display + (ind.compare_label === "a year ago" ? " a year ago" : "") : "",
      ind.change_display].filter(Boolean).join(" · ");
    return '<div class="tile"><div class="lbl"><span>' + esc(ind.label) + "</span>" + effectChip(byId[ind.id]) + "</div>" +
      '<div class="val">' + esc(ind.display) + '</div><div class="chg">' + esc(chg) + "</div>" +
      '<div class="ctx">' + esc(ind.context || "") + '</div><div class="why">' + esc(ind.why || "") + "</div>" +
      '<div class="src">As of ' + esc(ind.as_of) + " · " + sourceLinks(ind.sources) + "</div></div>";
  }).join("");
  var pbox = $(politicalId);
  if (pbox) pbox.innerHTML = display.political.map(function (p) {
    var viz = "";
    if (p.id === "approval") {
      var a = p.approve, d = p.disapprove, u = Math.max(0, 100 - a - d);
      viz = '<div class="appbar" role="img" aria-label="Approve ' + a + '%, disapprove ' + d + '%">' +
        '<div style="width:' + a + '%;background:var(--rep)"></div><div style="width:' + u + '%;background:var(--c3)"></div>' +
        '<div style="flex:1;background:var(--axis)"></div></div>' +
        '<div class="legend" style="margin-top:4px"><span><i class="sw" style="background:var(--rep)"></i>Approve ' + a +
        '%</span><span><i class="sw" style="background:var(--axis)"></i>Disapprove ' + d + "%</span><span>Net " +
        signed(p.value_net, 0) + " (model input)</span></div>";
    }
    var chip = p.id === "approval" ? effectChip(p.value_net < -5 ? -1 : p.value_net > 5 ? 1 : 0) :
      '<span class="effect"><i class="sw" style="background:var(--dem)"></i>&#9660; Hurts GOP</span>';
    return '<div class="tile"><div class="lbl"><span>' + esc(p.label) + "</span>" + chip + "</div>" +
      '<div class="val">' + esc(p.display) + "</div>" + viz +
      '<div class="ctx">' + esc(p.context || "") + '</div><div class="why">' + esc(p.why || "") + "</div>" +
      '<div class="src">As of ' + esc(p.as_of) + " · " + sourceLinks(p.sources) + "</div></div>";
  }).join("");
}

/* --------------------------------------------------------------- trend */
function renderTrend(svgId, runs, key, seatKey) {
  var svg = $(svgId); if (!svg) return;
  clear(svg);
  var n = runs.length, W = 520, H = 220, m = {l: 40, r: 56, t: 14, b: 30};
  var t0 = new Date(runs[0].timestamp).getTime();
  var t1 = Math.max(new Date(runs[n - 1].timestamp).getTime(), t0 + 86400000 * 6);
  var xs = function (t) { return m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r); };
  var ys = function (p) { return H - m.b - p * (H - m.t - m.b); };
  [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
    el("line", {x1: m.l, x2: W - m.r, y1: ys(v), y2: ys(v), "class": v === 0 ? "axis-line" : "gridline",
      "stroke-width": v === 0.5 ? 1.5 : 1}, svg);
    el("text", {x: m.l - 6, y: ys(v) + 3.5, "text-anchor": "end"}, svg).textContent = Math.round(v * 100) + "%";
  });
  el("rect", {x: m.l, y: ys(1), width: W - m.l - m.r, height: ys(0.5) - ys(1), fill: css("--dem-wash")}, svg);
  el("rect", {x: m.l, y: ys(0.5), width: W - m.l - m.r, height: ys(0) - ys(0.5), fill: css("--rep-wash")}, svg);
  el("text", {x: m.l + 6, y: ys(1) + 14}, svg).textContent = "Democrats favored";
  el("text", {x: m.l + 6, y: ys(0) - 6}, svg).textContent = "Republicans favored";
  var pts = runs.map(function (r) { return [xs(new Date(r.timestamp).getTime()), ys(r[key]), r]; });
  if (pts.length > 1) {
    el("path", {d: "M" + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join("L"),
      fill: "none", stroke: css("--dem"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}, svg);
  }
  pts.forEach(function (p, i) {
    var last = i === pts.length - 1, r = p[2];
    el("circle", {cx: p[0], cy: p[1], r: last ? 5 : 3.5, fill: css("--dem"), stroke: css("--surface"), "stroke-width": 2}, svg);
    var hit = el("circle", {cx: p[0], cy: p[1], r: 12, fill: "transparent", style: "cursor:pointer"}, svg);
    bindTip(hit, function () {
      return "<b>" + fmtDate(r.timestamp, true) + "</b><br>Democrats " + pct(r[key]) + " · avg " +
        r[seatKey].toFixed(1) + " seats<br>Environment " + margin(r.environment);
    });
    if (last) {
      el("text", {x: p[0] + 8, y: p[1] + 4, style: "fill:var(--ink);font-weight:600;font-size:12px"}, svg).textContent = pct(r[key]);
    }
  });
  el("text", {x: m.l, y: H - 8}, svg).textContent = fmtDate(runs[0].timestamp);
  if (n > 1) el("text", {x: W - m.r, y: H - 8, "text-anchor": "end"}, svg).textContent = fmtDate(runs[n - 1].timestamp);
}

/* ---------------------------------------------------------------- page */
function renderHero(id, name, block, needD, total, extraFacts) {
  var box = $(id); if (!box) return;
  var pD = block.dem_control_prob, dem = block.mean_dem_seats;
  box.innerHTML = '<div class="chamber-label">' + name + "</div>" +
    '<div class="headline">' + headlineFor(pD, name, "R") + "</div>" +
    '<div class="odds"><div><div class="big">' + pct(pD) + '</div><div class="side">Democrats</div></div>' +
    '<div style="text-align:right"><div class="big">' + pct(1 - pD) + '</div><div class="side r">Republicans</div></div></div>' +
    '<div class="splitbar" role="img" aria-label="Democrats ' + pct(pD) + ", Republicans " + pct(1 - pD) + '">' +
    '<div style="width:' + (pD * 100) + '%;background:var(--dem)"></div><div style="flex:1;background:var(--rep)"></div></div>' +
    '<div class="facts"><span>Average outcome <b>D ' + Math.round(dem) + " · R " + Math.round(total - dem) +
    "</b></span><span>Democrats need <b>" + needD + "</b></span><span>80% of simulations: <b>" +
    block.percentiles["10"] + " to " + block.percentiles["90"] + "</b> D seats</span>" + (extraFacts || "") + "</div>";
}

function renderJoint(id, joint) {
  var box = $(id); if (!box) return;
  var items = [["Democrats win both", joint.dem_both, "var(--dem)"],
    ["Split: D House, R Senate", joint.dem_house_rep_senate, "var(--c3)"],
    ["Split: R House, D Senate", joint.rep_house_dem_senate, "var(--c3)"],
    ["Republicans keep both", joint.rep_both, "var(--rep)"]];
  box.innerHTML = items.map(function (it) {
    return '<div class="j"><div class="k"><i class="sw" style="background:' + it[2] + '"></i>' + it[0] +
      '</div><div class="v">' + pct(it[1]) + "</div></div>";
  }).join("");
}

function senateSeatDots(senate) {
  var out = [], i;
  for (i = 0; i < senate.not_up.D; i++) out.push({notUp: true, party: "D", p: 1, tip: "<b>Democratic seat not up in 2026</b>"});
  senate.seats.map(function (r) { return {p: r.dem_win_prob, tip: senateTip(r)}; })
    .sort(function (a, b) { return b.p - a.p; })
    .forEach(function (s) { out.push(s); });
  for (i = 0; i < senate.not_up.R; i++) out.push({notUp: true, party: "R", p: 0, tip: "<b>Republican seat not up in 2026</b>"});
  return out;
}
function houseSeatDots(house, meta) {
  return Object.keys(house.district_probs).map(function (id) {
    return {p: house.district_probs[id], tip: houseTip(id, house.district_probs[id], meta, house.district_ratings || {})};
  }).sort(function (a, b) { return b.p - a.p; });
}

function onRender(fn) { renderers.push(fn); fn(); }

/* --------------------------------------------------------- calibration */
/* Predicted against observed, with the diagonal drawn. One series, so no
   legend: the title names it. The marks are ink rather than blue or red on
   purpose, because "was the model right" is not a question about which party
   won, and a red dot here would read as a Republican one. Each dot carries a
   95% interval, because eight bins of seventy-five races cannot be read as
   precise and a bare dot invites exactly that mistake. */
function renderCalibration(svgId, curve, brier) {
  var svg = $(svgId); if (!svg || !curve || !curve.length) return;
  clear(svg);
  var W = 520, H = 330, m = {l: 44, r: 14, t: 14, b: 44};
  var lo = 0.45, hi = 1.0;
  var x = function (v) { return m.l + (v - lo) / (hi - lo) * (W - m.l - m.r); };
  var y = function (v) { return H - m.b - (v - lo) / (hi - lo) * (H - m.t - m.b); };

  [0.5, 0.6, 0.7, 0.8, 0.9, 1.0].forEach(function (v) {
    el("line", {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), "class": "gridline"}, svg);
    el("text", {x: m.l - 6, y: y(v) + 3.5, "text-anchor": "end"}, svg).textContent = Math.round(v * 100) + "%";
    el("text", {x: x(v), y: H - m.b + 15, "text-anchor": "middle"}, svg).textContent = Math.round(v * 100) + "%";
  });
  el("line", {x1: m.l, x2: W - m.r, y1: y(lo), y2: y(lo), "class": "axis-line"}, svg);
  el("line", {x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, "class": "axis-line"}, svg);

  el("line", {x1: x(lo), y1: y(lo), x2: x(hi), y2: y(hi), stroke: css("--axis"),
    "stroke-width": 1.5, "stroke-dasharray": "5 4"}, svg);
  /* Set along the line it names, in the one stretch of it no dot or whisker
     reaches. The angle is the plot's own diagonal, which is not 45 degrees
     unless the box happens to be square. */
  var ang = -Math.atan2(H - m.t - m.b, W - m.l - m.r) * 180 / Math.PI;
  var lx = x(0.70), ly = y(0.70) + 13;
  el("text", {x: lx, y: ly, transform: "rotate(" + ang.toFixed(1) + " " + lx + " " + ly + ")",
    style: "fill:var(--muted);font-size:11px"}, svg).textContent = "perfectly calibrated";

  curve.forEach(function (c) {
    var se = Math.sqrt(Math.max(c.observed * (1 - c.observed), 0.0001) / c.n) * 1.96;
    var top = Math.min(c.observed + se, 1), bot = Math.max(c.observed - se, 0);
    el("line", {x1: x(c.predicted), x2: x(c.predicted), y1: y(Math.max(top, lo)), y2: y(Math.max(bot, lo)),
      stroke: css("--axis"), "stroke-width": 2, "stroke-linecap": "round"}, svg);
  });
  curve.forEach(function (c) {
    var dot = el("circle", {cx: x(c.predicted), cy: y(Math.max(c.observed, lo)), r: 6,
      fill: css("--ink"), stroke: css("--surface"), "stroke-width": 2}, svg);
    bindTip(dot, function () {
      return "<b>" + c.n + " races</b><br>The model gave the favourite <b>" + pct(c.predicted) +
        "</b><br>The favourite actually won <b>" + pct(c.observed) + "</b>";
    });
  });

  el("text", {x: (W + m.l) / 2, y: H - 6, "text-anchor": "middle"}, svg)
    .textContent = "What the model said the favourite's chances were";
  el("text", {x: 12, y: (H - m.b + m.t) / 2, "text-anchor": "middle",
    transform: "rotate(-90 12 " + ((H - m.b + m.t) / 2) + ")"}, svg)
    .textContent = "How often the favourite won";
  if (brier) {
    el("text", {x: W - m.r, y: H - m.b - 8, "text-anchor": "end",
      style: "fill:var(--muted);font-size:11px"}, svg).textContent = "Brier score " + brier.toFixed(3);
  }
}

/* ------------------------------------------------------- tipping point */
function renderTipping(hostId, items, noteId, note) {
  /* items: [{label, sub, prob, tip}], already sorted. Built in HTML for the
     same reason the driver bars are: this card runs full width on a desktop
     and 400px on a phone, and SVG text scales with the box. */
  var host = $(hostId); if (!host) return;
  host.innerHTML = "";
  if (!items || !items.length) {
    host.innerHTML = '<div class="tipfoot">Control is not in doubt in enough races to name a deciding one.</div>';
    return;
  }
  var max = items[0].prob || 1;
  items.forEach(function (it) {
    var row = document.createElement("div");
    row.className = "tiprow";
    row.innerHTML = '<div class="lbl">' + esc(it.label) +
      (it.sub ? ' <i>' + esc(it.sub) + "</i>" : "") + "</div>" +
      '<div class="tiptrack"><span class="tipfill"></span></div>' +
      '<div class="val">' + pct1(it.prob) + "</div>";
    var fill = row.querySelector(".tipfill");
    fill.style.width = Math.max(it.prob / max * 100, 1.5) + "%";
    if (it.tip) bindTip(fill, function () { return it.tip; });
    host.appendChild(row);
  });
  if (noteId && $(noteId)) $(noteId).innerHTML = note || "";
}

function senateTipping(senate, limit) {
  return senate.seats.filter(function (r) { return r.tipping_prob > 0; })
    .sort(function (a, b) { return b.tipping_prob - a.tipping_prob; })
    .slice(0, limit)
    .map(function (r) {
      var fav = r.dem_win_prob >= 0.5 ? "D" : "R";
      return {
        label: STATE_NAMES[r.state] + (r.seat_id.indexOf("special") > -1 ? " (sp.)" : ""),
        sub: RATING_NAME[r.rating] + (r.rating === "tossup" ? "" : " " + r.lean),
        prob: r.tipping_prob,
        tip: "<b>" + esc(STATE_NAMES[r.state]) + "</b><br>Decides the Senate in <b>" +
          pct1(r.tipping_prob) + "</b> of simulations<br>Democrats win it " + pct(r.dem_win_prob) +
          " of the time · currently favoured: " + fav
      };
    });
}

function houseTipping(house, meta, limit) {
  var t = house.district_tipping || {};
  return Object.keys(t).sort(function (a, b) { return t[b] - t[a]; }).slice(0, limit)
    .map(function (id) {
      var m = (meta || {})[id] || {}, p = house.district_probs[id];
      return {
        label: id, sub: STATE_NAMES[m.state] || "",
        prob: t[id],
        tip: "<b>" + esc(id) + "</b>" + (m.incumbent ? " · " + esc(m.incumbent) : "") +
          "<br>Decides the House in <b>" + pct1(t[id]) + "</b> of simulations" +
          (p === undefined ? "" : "<br>Democrats win it " + pct(p) + " of the time")
      };
    });
}

function init() {
  tip = document.createElement("div");
  tip.id = "tip"; tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);
  window.addEventListener("scroll", hideTip, {passive: true});
  var rerun = function () { renderers.forEach(function (fn) { fn(); }); };
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", rerun);
  }
  new MutationObserver(rerun).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
}

global.FC = {
  init: init, onRender: onRender, $: $, esc: esc, pct: pct, pct1: pct1, signed: signed, margin: margin,
  fmtDate: fmtDate, css: css, binColor: binColor, legendBins: legendBins, headlineFor: headlineFor,
  renderHero: renderHero, renderJoint: renderJoint, renderHemicycle: renderHemicycle, renderHistogram: renderHistogram,
  renderSenateMap: renderSenateMap, renderWatch: renderWatch, renderSenateTable: renderSenateTable,
  renderHouseMap: renderHouseMap, renderHouseTable: renderHouseTable, renderEnvironment: renderEnvironment,
  renderIndicators: renderIndicators, renderTrend: renderTrend,
  renderMarketCompare: renderMarketCompare, renderPairedBars: renderPairedBars,
  renderMarketRaces: renderMarketRaces, renderAttention: renderAttention, renderStory: renderStory, renderMarketStrip: renderMarketStrip, renderMarginBins: renderMarginBins,
  renderCalibration: renderCalibration, renderTipping: renderTipping, senateTipping: senateTipping, houseTipping: houseTipping,
  senateSeatDots: senateSeatDots, houseSeatDots: houseSeatDots, STATE_NAMES: STATE_NAMES, ELECTION: ELECTION
};
})(window);
