/* Private: the content arrives sealed (AES-256-GCM, its key drawn from the password by PBKDF2) and is opened
   here, in the browser. Only the sealed file is published. */
(() => {
  const STORE = "focal-private";
  const keep = {
    get() { try { return sessionStorage.getItem(STORE); } catch (e) { return null; } },
    set(v) { try { sessionStorage.setItem(STORE, v); } catch (e) {} },
    clear() { try { sessionStorage.removeItem(STORE); } catch (e) {} },
  };
  const local = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const NS = "http://www.w3.org/2000/svg";
  const mk = (t, attrs, text) => { const e = document.createElementNS(NS, t); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; };
  const MINUS = "−";
  const ordinal = n => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th");
  const pct = x => Math.round(100 * x) + "%";
  const by = (x, up, down) => { const v = Math.round(100 * (Math.exp(x) - 1)); return v === 0 ? "no different from" : `${Math.abs(v)}% ${v > 0 ? up : down}`; };
  const byPct = (v, up, down) => v === 0 ? "no different from" : `${Math.abs(v)}% ${v > 0 ? up : down}`;
  const signed = (x, d) => (x > 0 ? "+" : x < 0 ? MINUS : "") + Math.abs(x).toFixed(d);
  const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const andList = xs => xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1];
  // what sets a set of pictures apart, from the reader's answers: each named answer's share of the pictures against the
  // median other brand's (or runway's), the comparison named once
  const sayApart = (a, who) => andList(a.map((x, i) => `${pct(x.h)} ${x.l} against ${pct(x.m)}${i === 0 && who ? ` at ${who}` : ""}`));
  const KIND_NAME = { image: "campaign pictures", product: "product" };
  const kindsText = m => ["image", "product"].filter(k => m[k]).map(k => `${KIND_NAME[k]} ${ordinal(m[k].p)} of 100 (${m[k].n} pictures)`).join(", ");
  // after a window's distinctness, kind by kind: what sets it apart, or why nothing is named
  function whatSetsApart(m, who, answers = "the reader's answers") {
    const ks = ["image", "product"].filter(k => m[k] && "a" in m[k]);
    if (!ks.length) return "";
    const named = ks.filter(k => m[k].a && m[k].a.length);
    if (!named.length) {
      return ks.some(k => m[k].a) ? ` No answer stands far from ${who}'s.` : " Too few pictures of one kind to say what sets them apart.";
    }
    if (ks.length === 1) return ` What sets them apart in ${answers}: ${sayApart(m[ks[0]].a, who)}.`;
    return ` What sets them apart in ${answers}: ` + ks.map((k, i) => {
      const x = m[k];
      return `the ${KIND_NAME[k]}, ${x.a == null ? "too few to say" : x.a.length ? sayApart(x.a, k === named[0] ? who : null) : `nothing far from ${who}'s`}`;
    }).join("; ") + ".";
  }
  // the clothes: one set of outfits or looks
  const clothesApart = (a, who) => a == null ? "" : a.length ? ` What sets them apart in the clothes answers: ${sayApart(a, who)}.` : ` No clothes answer stands far from ${who}'s.`;
  const THIN = 8;

  let sealed = null, P = null, current = null;
  const redraw = {};

  // the sealed content lives in the public repo, so it can be refreshed without redeploying the site; a copy
  // deployed beside the page is the fallback
  const SOURCES = ["https://raw.githubusercontent.com/benmcewensignal/fashion-adtone/main/www/private.json", "private.json"];
  async function fetchSealed() {
    for (const u of SOURCES) {
      try { const r = await fetch(u, { cache: "no-store" }); if (r.ok) return await r.json(); } catch (e) {}
    }
    throw new Error("missing");
  }
  async function unseal(pw) {
    if (!sealed) sealed = await fetchSealed();
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt: b64(sealed.salt), iterations: sealed.iter, hash: sealed.hash },
      base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(sealed.iv) }, key, b64(sealed.ct));
    if (sealed.zip === "gzip") {
      if (typeof DecompressionStream === "undefined") throw new Error("old");
      return JSON.parse(await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"))).text());
    }
    return JSON.parse(new TextDecoder().decode(plain));
  }

  /* ---- shared pieces ---- */
  function head(host, part, big) {
    const h = el("header", "pv-head");
    if (part.kicker) h.append(el("span", "k", part.kicker));
    h.append(el("h3", null, part.title));
    if (part.lede) h.append(el("p", big ? "big" : null, part.lede));
    host.append(h);
  }
  function findings(host, list) {
    if (!list || !list.length) return;
    const f = el("div", "pv-find");
    for (const x of list) { const d = el("div"); if (x.h) d.append(el("h4", null, x.h)); d.append(el("p", null, x.p)); f.append(d); }
    host.append(f);
  }
  // every report opens with its finding: the report's name small, the finding as the headline, then its line
  function lead(host, part, f, line) {
    const h = el("header", "pv-head pv-lead");
    if (part.title) h.append(el("span", "k", part.title));
    h.append(el("h3", null, f.h));
    if (line !== false && f.p) h.append(el("p", null, line || f.p));
    host.append(h);
  }
  // the rest of a report's reading sits under its table or chart, as the note
  function note(host, list, intro) {
    if ((!list || !list.length) && !intro) return;
    const n = el("div", "pv-note");
    if (intro) n.append(el("p", null, intro));
    for (const x of list || []) { const d = el("div"); if (x.h) d.append(el("h4", null, x.h)); d.append(el("p", null, x.p)); n.append(d); }
    host.append(n);
  }
  // how each reading is read is set out once, on its own page, and linked from every report
  function methodLink(host) {
    const p = el("p", "pv-how"); const a = el("a", null, "How it is read"); a.href = "#/private/method"; p.append(a); host.append(p);
  }
  const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"];
  const word = n => WORDS[n] || String(n);
  function cols(cls, names) {
    const c = el("div", "pv-cols pv-grid " + cls); c.setAttribute("aria-hidden", "true");
    for (const n of names) c.append(el("span", null, n));
    return c;
  }
  const at = p => `calc(5px + (100% - 10px) * ${p / 100})`;
  function place(lab, p, thin, title, none) {
    const c = el("span", "pv-cell"); c.append(el("span", "lab", lab));
    if (p == null) { c.append(el("span", "none", none)); return c; }
    const t = el("span", "pv-track"); const dot = el("i", thin ? "thin" : null); dot.style.left = at(p);
    t.title = title; t.append(dot); c.append(t, el("span", "val num", String(p)));
    return c;
  }
  function share(lab, mix, pics) {
    const c = el("span", "pv-cell pv-share"); c.append(el("span", "lab", lab));
    if (mix) { const bar = el("span", "bar"); const i = el("i"); i.style.width = pct(mix.image); bar.title = `${pct(mix.image)} of ${pics} pictures`; bar.append(i); c.append(bar, el("span", "val num", pct(mix.image))); }
    else c.append(el("span", "none", pics ? "too few pictures" : "no pictures on file"));
    return c;
  }
  function dl(pairs) {
    const box = el("div", "pv-detail"); const d = el("dl");
    for (const [t, text, wait] of pairs) { const row = el("div"); row.append(el("dt", null, t), el("dd", wait ? "wait" : null, text)); d.append(row); }
    box.append(d); return box;
  }
  function widthOf(node, lo, hi) { return Math.max(lo, Math.min(hi, node.clientWidth || hi)); }

  /* ---- the reshuffle ---- */
  const STILL = {
    itself: () => "Itself, both ways",
    overtaken: s => `Overtaken by ${s.past_other}`,
    moved: s => `Moved toward ${s.recent_other}`,
    neither: () => "Itself neither way",
    close: () => "A close call",
  };
  function stillText(s) {
    if (!s) return "Too few homepage pictures since November 2025 to say.";
    const r = Math.round(100 * s.recent_share), q = Math.round(100 * s.past_share);
    return {
      itself: `Itself, both ways: its recent pictures matched its own past, and its past its own recent pictures, in ${r} and ${q} of 100 draws.`,
      overtaken: `Its recent pictures still match its own past best (${r} of 100 draws), but ${s.past_other}'s recent pictures now sit nearer its past than its own do (it held in ${q} of 100).`,
      moved: `Its recent pictures now sit closest to ${s.recent_other}'s past (it matched its own in ${r} of 100 draws).`,
      neither: `Neither way: its recent pictures sit closest to ${s.recent_other}'s past, and its past nearer ${s.past_other}'s recent pictures (${r} and ${q} of 100 draws).`,
      close: `A close call: it matched itself in ${r} and ${q} of 100 draws.`,
    }[s.cat];
  }
  function drawScatter(svg, pts, wrap) {
    for (const c of [...svg.children]) if (c.tagName.toLowerCase() !== "title") c.remove();
    const W = widthOf(wrap, 300, 600), L = 34, R = 14, T = 26, B = 44;
    const side = Math.min(W - L - R, 460), H = T + side + B;
    const x = v => L + v / 100 * (W - L - R), y = v => T + (1 - v / 100) * side;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    // no box: only the two lines at 50 cross the plot, with the places marked along the edges
    svg.append(mk("line", { x1: x(50), x2: x(50), y1: T, y2: T + side, class: "sx-mid" }));
    svg.append(mk("line", { x1: L, x2: W - R, y1: y(50), y2: y(50), class: "sx-mid" }));
    for (const v of [0, 25, 50, 75, 100]) {
      if (v % 50 === 0 || W >= 460) {
        svg.append(mk("text", { x: x(v), y: T + side + 16, "text-anchor": "middle", class: "sx-tick" }, String(v)));
        svg.append(mk("text", { x: L - 7, y: y(v) + 4, "text-anchor": "end", class: "sx-tick" }, String(v)));
      }
    }
    svg.append(mk("text", { x: W - R, y: T + side + 36, "text-anchor": "end", class: "sx-axis" }, "Jump at the show →"));
    svg.append(mk("text", { x: L, y: T - 10, "text-anchor": "start", class: "sx-axis" }, "↑ Attention lasted"));
    const boxes = [];
    const overlaps = b => boxes.some(o => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y);
    const olds = pts.filter(p => !p.new), news = pts.filter(p => p.new);
    for (const p of olds) {
      const g = mk("g", { tabindex: "0" });
      g.append(mk("title", {}, `${p.name}, ${p.day}: jump ${p.heat}% on its usual level (${p.x} of 100); lasted ${p.last > 0 ? "+" : p.last < 0 ? MINUS : ""}${Math.abs(p.last)}% against the median brand (${p.y} of 100)`));
      g.append(mk("circle", { cx: x(p.x), cy: y(p.y), r: 9, class: "sx-hit" }));
      g.append(mk("circle", { cx: x(p.x), cy: y(p.y), r: 3.5, class: "sx-old" }));
      svg.append(g);
    }
    for (const p of news) boxes.push({ x: x(p.x) - 6, y: y(p.y) - 6, w: 12, h: 12 });
    const labels = [];
    for (const p of news.slice().sort((a, b) => b.y - a.y || b.x - a.x)) {
      const cx = x(p.x), cy = y(p.y), w = p.name.length * 6.5 + 2, h = 13;
      const cands = [
        { x: cx + 9, y: cy - h / 2, a: "start" }, { x: cx - 9 - w, y: cy - h / 2, a: "end" },
        { x: cx - w / 2, y: cy - 8 - h, a: "middle" }, { x: cx - w / 2, y: cy + 8, a: "middle" },
        { x: cx + 9, y: cy - h / 2 - 13, a: "start" }, { x: cx + 9, y: cy - h / 2 + 13, a: "start" },
        { x: cx - 9 - w, y: cy - h / 2 - 13, a: "end" }, { x: cx - 9 - w, y: cy - h / 2 + 13, a: "end" },
      ];
      let pick = cands.find(c => c.x >= 0 && c.x + w <= W && c.y >= 0 && c.y + h <= H && !overlaps({ x: c.x, y: c.y, w, h }));
      if (!pick) pick = cands[0];
      boxes.push({ x: pick.x, y: pick.y, w, h });
      const tx = pick.a === "start" ? pick.x : pick.a === "end" ? pick.x + w : pick.x + w / 2;
      labels.push({ p, tx, ty: pick.y + h - 3, a: pick.a, far: Math.abs(pick.y + h / 2 - cy) > 10 });
    }
    for (const lb of labels) {
      if (lb.far) svg.append(mk("line", { x1: x(lb.p.x), y1: y(lb.p.y), x2: lb.a === "end" ? lb.tx + 2 : lb.tx - 2, y2: lb.ty - 4, stroke: "currentColor", "stroke-width": 0.75, opacity: 0.45 }));
    }
    for (const p of news) {
      const g = mk("g", { tabindex: "0" });
      g.append(mk("title", {}, `${p.name}, ${p.day}: jump ${p.heat}% on its usual level (${p.x} of 100); lasted ${p.last > 0 ? "+" : p.last < 0 ? MINUS : ""}${Math.abs(p.last)}% against the median brand (${p.y} of 100)`));
      g.append(mk("circle", { cx: x(p.x), cy: y(p.y), r: 11, class: "sx-hit" }));
      g.append(mk("circle", { cx: x(p.x), cy: y(p.y), r: 5.5, class: "sx-new" }));
      svg.append(g);
    }
    for (const lb of labels) svg.append(mk("text", { x: lb.tx, y: lb.ty, "text-anchor": lb.a, class: "sx-lab" }, lb.p.name));
  }
  function windowCell(r) {
    const c = el("span", "pv-cell"); c.append(el("span", "lab", "Shop window, distinct"));
    const w = r.window, before = w.before && w.before.dist_p;
    const after = [w.next, w.debut].find(x => x && x.dist_p != null) || null;
    if (!after) { c.append(el("span", "none", (w.debut && w.debut.pics) || (w.next && w.next.pics) ? "too few pictures since" : "no pictures on file")); return c; }
    const t = el("span", "pv-track");
    if (before != null) {
      const lo = Math.min(before, after.dist_p), hi = Math.max(before, after.dist_p);
      const line = el("b"); line.style.left = at(lo); line.style.width = `calc((100% - 10px) * ${(hi - lo) / 100})`; t.append(line);
      const was = el("i", "was"); was.style.left = at(before); t.append(was);
    }
    const now = el("i", after.pics < THIN ? "thin" : null); now.style.left = at(after.dist_p); t.append(now);
    t.title = (before != null ? `${w.before.label}: ${before} of 100; ` : "") + `${after.label}: ${after.dist_p} of 100, on ${after.pics} pictures`;
    c.append(t, el("span", "val num", before != null ? `${before}→${after.dist_p}` : String(after.dist_p)));
    return c;
  }
  function reshuffleClothes(r, norms) {
    const c = r.clothes || {}, db = c.debut || {}, nx = c.next || {}, bits = [];
    if (db.runway) bits.push(db.runway.v > 0.005
      ? `The first show's ${db.runway.looks} looks moved ${db.runway.v.toFixed(2)} from the show of ${db.runway.prev}, against ${norms.runway_median != null ? norms.runway_median.toFixed(2) : "?"} for the median runway from one season to the next.`
      : `The first show's ${db.runway.looks} looks moved no more than chance from the show of ${db.runway.prev}.`);
    for (const [w, when] of [[db.window, "after the first show"], [nx.window, "after the next"]]) {
      if (w) bits.push(`The shop window's ${w.worn} outfits ${when} ${w.apart ? "can" : "cannot"} be told apart from those after the show of ${w.prev}.`);
    }
    if (!bits.length) {
      const looks = db.looks, worn = db.worn;
      bits.push((looks ? `Only ${looks} look${looks === 1 ? "" : "s"} of the first show read, too few;` : "The first show's looks are not on file yet;")
        + ` ${worn ? `${worn} outfit${worn === 1 ? "" : "s"}` : "no outfits"} in the shop window since, ${worn && worn >= 5 ? "and too few before to compare" : "too few to read"}.`);
      return ["The clothes", bits.join(" "), true];
    }
    return ["The clothes", bits.join(" ")];
  }
  // the headline finding's two figures: the houses showing a new designer (filled) against the rest (hollow)
  function leadStats(L) {
    const box = el("div", "pv-stats");
    for (const [lab, a, b, p, d] of [["Jump at the show", L.new_jump, L.other_jump, L.p_jump, 3], ["Attention lasted", L.new_last, L.other_last, L.p_last, 2]]) {
      const line = el("div", "pv-stat");
      const t = el("span", "pv-track"); t.title = `${lab}: ${a} of 100 on average for the houses showing a new designer, ${b} for the rest`;
      const lo = Math.min(a, b), hi = Math.max(a, b);
      const span = el("b"); span.style.left = at(lo); span.style.width = `calc((100% - 10px) * ${(hi - lo) / 100})`;
      const was = el("i", "was"); was.style.left = at(b);
      const now = el("i"); now.style.left = at(a);
      t.append(span, was, now);
      line.append(el("span", "lab", lab), t, el("span", "val num", `${a} against ${b}`),
        el("span", "p", `The gap is ${p < 0.05 ? "beyond" : "within"} chance (p ${p.toFixed(d)}).`));
      box.append(line);
    }
    const key = el("p", "pv-key");
    key.append(el("span", "dot"), ` The ${word(L.new)} houses showing a new designer in ${L.season_label.toLowerCase()}; `, el("span", "dot thin"),
      ` the other ${L.shows - L.new}. Each placed from 0 to 100 among the season's shows, on average.`);
    box.append(key);
    return box;
  }
  function renderReshuffle(host, part) {
    const F = part.findings, first = F[0];
    const still = F.find(x => x.h === "Who still looks like themselves");
    const rest = F.filter(x => x !== first && x !== still);
    // the finding, then the board, then the picture; the rest of the reading behind its own disclosure
    lead(host, part, first, part.lead ? false : null);
    if (part.lead) host.append(leadStats(part.lead));
    const wide = el("div", "pv-wide");
    wide.append(el("p", "pv-sec", "House by house"));
    wide.append(el("p", "pv-key", part.lede.split(". ")[0] + ". Each dot is the house's place among the brands of its season, from 0 to 100; in the shop window, hollow is the last season before the change and filled the latest since, a hollow filled dot fewer than eight pictures. Open a house for the detail."));
    wide.append(cols("c-score", ["House", "Jump at the first show", "Attention lasted", "Shop window, distinct", "Still itself"]));
    const rows = el("div", "pv-rows");
    for (const r of part.rows) {
      const d = el("details"); const s = el("summary", "pv-grid c-score");
      const who = el("span", "pv-who"); who.append(el("span", "pv-name", r.name), el("span", "pv-sub", `${r.designer}, ${r.first_day}`)); s.append(who);
      const db = r.debut || {};
      s.append(place("Jump at the first show", db.heat_p, false, db.heat != null ? `${db.heat}% on its usual level, ${db.heat_p} of 100` : "", "no page views"));
      s.append(place("Attention lasted", db.last_p, false, db.last_p != null ? `${db.last_p} of 100` : "", "read after 120 days"));
      s.append(windowCell(r));
      const st = el("span", "pv-cell txt"); st.append(el("span", "lab", "Still itself"), el("span", "say", r.still ? STILL[r.still.cat](r.still) : "Too few pictures yet"));
      s.append(st);
      const pairs = [];
      pairs.push(["The designer", `${r.designer}, first shown ${r.first_day}.` + (r.note ? " " + r.note : "")]);
      if (db.heat != null) pairs.push(["The first show", `Attention ran ${byPct(db.heat, "above", "below")} the brand's usual level, ${ordinal(db.heat_p)} of 100 in ${r.season_label.toLowerCase()}.` + (db.last != null ? ` From 30 to 120 days after, it held ${byPct(db.last, "better", "worse")} than the median brand's, ${ordinal(db.last_p)} of 100.` : " Whether it lasted is read 120 days after.")]);
      const sc = r.second;
      pairs.push(["The next show", sc ? `${sc.day}: ${ordinal(sc.heat_p)} of 100 for the jump` + (sc.last_p != null ? `, ${ordinal(sc.last_p)} for whether it lasted.` : "; whether it lasted is read 120 days after.") : "None on file since.", !sc]);
      const ws = [["before", "Before"], ["debut", "First season"], ["next", "Next season"]]
        .filter(([k]) => r.window[k] && r.window[k].dist_p != null)
        .map(([k, n]) => `${r.window[k].label}: ${r.window[k].dist_p} of 100 for distinctness, on ${r.window[k].pics} pictures.` + whatSetsApart(r.window[k].apart || {}, "the median brand"));
      const mv = r.window.debut && r.window.debut.move_p;
      pairs.push(["The shop window", ws.length ? ws.join(" ") + (mv != null ? ` Moved from the season before: ${mv} of 100.` : "") : "No homepage pictures on file since the change.", !ws.length]);
      pairs.push(reshuffleClothes(r, part.clothes_norms || {}));
      let crew = r.crew ? `${r.crew.before} advertising photographer${r.crew.before === 1 ? "" : "s"} before the change and ${r.crew.after} since; kept: ${r.crew.kept.length ? r.crew.kept.join(", ") : "none"}.` : "No campaign credits on file before the change.";
      if (r.came) {
        const ppl = r.came.people.filter(p => p.name !== r.designer).map(p => p.name);
        crew += ppl.length ? ` From ${r.came.from}, ${r.designer} brought ${ppl.join(", ").replace(/, ([^,]*)$/, " and $1")}.` : r.came.people.length ? ` ${r.designer} photographs the campaigns, as at ${r.came.from}.` : "";
      }
      pairs.push(["The people", crew, !r.crew]);
      pairs.push(["Still itself", stillText(r.still), !r.still]);
      d.append(s, dl(pairs));
      rows.append(d);
    }
    wide.append(rows); host.append(wide);
    if (still) { const s = el("p", "pv-lede"); s.append(el("b", null, "Who still looks like themselves. "), still.p); host.append(s); }
    const fig = el("figure", "pv-fig");
    fig.append(el("p", "pv-sec", part.scatter.title));
    const svg = mk("svg", { role: "img", "aria-label": part.scatter.title }); svg.append(mk("title", {}, part.scatter.title));
    fig.append(svg, el("figcaption", null, part.scatter.caption));
    host.append(fig);
    redraw.reshuffle = () => drawScatter(svg, part.points, fig);
    if (rest.length) {
      const more = el("details", "pv-more"); more.append(el("summary", null, `The rest of the reading, in ${word(rest.length)} findings`));
      const f = el("div", "pv-find");
      for (const x of rest) { const d = el("div"); d.append(el("h4", null, x.h), el("p", null, x.p)); f.append(d); }
      more.append(f); host.append(more);
    }
    methodLink(host);
  }

  /* ---- who still looks like themselves ---- */
  const CAT = r => ({ itself: "Itself, both ways", close: "A close call", moved: `Moved toward ${r.recent.other_name}`, overtaken: `Overtaken by ${r.past.other_name}`, neither: "Itself neither way" })[r.cat];
  const ORDER = ["itself", "close", "moved", "overtaken", "neither"];
  function bar(lab, side) {
    const c = el("span", "pv-cell pv-bar"); c.append(el("span", "lab", lab));
    const s = Math.round(100 * side.share);
    const t = el("span", "pv-track");
    for (const v of [25, 80]) { const k = el("u"); k.style.left = at(v); t.append(k); }
    const dot = el("i", s >= 80 ? null : s <= 25 ? "other" : "thin"); dot.style.left = at(s); t.append(dot);
    t.title = `Matched itself in ${s} of 100 draws; nearest other brand: ${side.other_name}`;
    c.append(t, el("span", "val num", String(s)));
    c.append(el("span", "who-else", side.share >= 0.5 ? `then ${side.other_name}` : `nearer ${side.other_name}`));
    return c;
  }
  // under each brand: where it stands, and the nearest other brand
  const NEAREST = r => ({
    itself: `Itself, both ways; next nearest ${r.recent.other_name}`,
    close: `A close call; nearest other ${r.recent.other_name}`,
    moved: `Moved toward ${r.recent.other_name}'s past`,
    overtaken: `Overtaken by ${r.past.other_name}'s recent pictures`,
    neither: `Itself neither way; nearest ${r.recent.other_name}'s past`,
  })[r.cat];
  function renderThemselves(host, part) {
    const [first, ...rest] = part.findings;
    lead(host, part, first);
    const wide = el("div", "pv-wide");
    wide.append(el("p", "pv-sec", "Brand by brand"));
    wide.append(el("p", "pv-key", part.caption));
    wide.append(cols("c-sig", ["Brand", "Its recent pictures, against every past", "Its past, against every brand's recent pictures"]));
    const rows = el("div", "pv-rows");
    const sorted = part.rows.slice().sort((a, b) => ORDER.indexOf(a.cat) - ORDER.indexOf(b.cat) || (b.recent.margin + b.past.margin) - (a.recent.margin + a.past.margin));
    for (const r of sorted) {
      const line = el("div", "pv-line pv-grid c-sig"); line.title = `${r.n_past} pictures before November 2025, ${r.n_recent} since`;
      const who = el("span", "pv-who"); who.append(el("span", "pv-name", r.name), el("span", "pv-sub", NEAREST(r)));
      line.append(who, bar("Its recent pictures", r.recent), bar("Its past", r.past));
      rows.append(line);
    }
    wide.append(rows);
    const miss = el("p", "pv-key", part.missing); miss.style.marginTop = "0.9rem"; wide.append(miss);
    host.append(wide);
    note(host, rest, part.lede);
    methodLink(host);
  }

  /* ---- one brand ---- */
  function renderBrand(host, part) {
    head(host, part, true);
    const f = el("dl", "pv-facts");
    for (const x of part.facts) { const d = el("div"); d.append(el("dt", null, x.t), el("dd", null, x.d)); f.append(d); }
    host.append(f);
    const wide = el("div", "pv-wide");
    wide.append(el("p", "pv-sec", "Season by season"));
    wide.append(el("p", "pv-key", part.strip_caption));
    wide.append(cols("c-board", ["Season", "Distinct from peers", "Moved from last season", "Attention lasted", "Jump at the show", "Campaign pictures"]));
    const rows = el("div", "pv-rows");
    const so = s => { const [y, h] = s.split(" "); return 2 * Number(y) + (h === "AW" ? 1 : 0); };
    const list = part.rows.filter(r => so(r.season) >= so("2022 AW")).slice().reverse();
    let divided = false;
    for (const r of list) {
      if (!divided && so(r.season) < so(part.change_season)) { rows.append(el("div", "pv-divider", `Above: ${part.change_label}`)); divided = true; }
      const d = el("details"); const line = el("summary", "pv-grid c-board");
      const who = el("span", "pv-who"); who.append(el("span", "pv-name", r.label), el("span", "pv-sub", r.day)); line.append(who);
      const thin = r.pics < THIN, none = r.pics ? "too few of one kind" : "no pictures on file";
      line.append(place("Distinct from peers", r.dist_p, thin, `${r.dist_p} of 100, from ${r.pics} pictures`, none));
      line.append(place("Moved from last season", r.move_p, thin, `${r.move_p} of 100, from ${r.pics} pictures`, r.pics ? "no window to compare" : none));
      line.append(place("Attention lasted", r.last_p, false, r.last != null ? `${r.last_p} of 100` : "", r.heat_p == null ? "no page views" : "read after 120 days"));
      line.append(place("Jump at the show", r.heat_p, false, r.heat != null ? `${r.heat}% on its usual level, ${r.heat_p} of 100` : "", "too few page views"));
      line.append(share("Campaign pictures", r.image != null ? { image: r.image } : null, r.pics));
      const sw = !r.pics ? "No homepage pictures on file for the season." : `${r.pics} homepage picture${r.pics > 1 ? "s" : ""}.`
        + (r.kinds ? ` Distinct from peers: ${kindsText(r.kinds)}.` + whatSetsApart(r.kinds, "the median brand") : " Too few pictures of one kind to place it.");
      d.append(line, dl([["The shop window", sw, !r.kinds]]));
      rows.append(d);
    }
    wide.append(rows); host.append(wide);
    findings(host, [{ p: part.close }]);
    methodLink(host);
  }

  /* ---- the season board ---- */
  const SORTS = [["dist", "Distinct"], ["move", "Moved"], ["clothes", "Clothes"], ["last", "Lasted"], ["heat", "Jump"], ["name", "Name"]];
  function renderBoard(host, part) {
    const D = part;
    head(host, part);
    const byKey = Object.fromEntries(D.seasons.map(s => [s.key, s]));
    let season = byKey[local.get("focal-pv-season")] ? local.get("focal-pv-season") : D.default;
    let sort = SORTS.some(s => s[0] === local.get("focal-pv-sort")) ? local.get("focal-pv-sort") : "dist";
    const wide = el("div", "pv-wide");
    const seasons = el("div", "pv-chips scroll"); seasons.setAttribute("aria-label", "Season");
    const bh = el("div", "pv-board-head"); const title = el("h4"); const meta = el("p"); const stages = el("ul", "pv-stages"); bh.append(title, meta, stages);
    const controls = el("div", "pv-controls"); const sorts = el("div", "pv-chips"); controls.append(el("span", "lab", "Sort by"), sorts);
    const key = el("p", "pv-key");
    key.append("Each dot is the brand's place among the season's brands, from 0 at the left to 100 at the right. ", el("span", "dot"), "read from eight homepage pictures or more (for the clothes, eight outfits); ", el("span", "dot thin"), "fewer. The clothes are provisional until a trained eye has checked the questions. Open a brand to follow its collection stage by stage.");
    const rowsHost = el("div", "pv-rows");
    wide.append(seasons, bh, controls, key, cols("c-season", ["Brand", "Distinct from peers", "Moved from last season", "Clothes distinct", "Attention lasted", "Jump at the show", "Campaign pictures"]), rowsHost);
    host.append(wide);
    function chips(h, items, cur, pick) {
      h.replaceChildren();
      for (const [k, t] of items) { const b = el("button", "pv-chip", t); b.type = "button"; b.setAttribute("aria-pressed", String(k === cur)); b.addEventListener("click", () => pick(k)); h.append(b); }
    }
    const kinds = kindsText;
    function detail(r) {
      const pairs = [];
      pairs.push(["The scene", r.amb == null ? "Ambassador appointments are on file from 2019." : r.amb === 0 ? "No ambassador appointed in the six months before." : `${r.amb} ambassador${r.amb > 1 ? "s" : ""} appointed in the six months before.`, r.amb == null]);
      let show = `${r.day}.`;
      if (r.heat != null) show += ` Attention at the show ran ${by(r.heat, "above", "below")} the brand's usual level, ${ordinal(r.heat_p)} of 100 in the season.`;
      else show += " Too few page views on file to read the jump.";
      if (r.last != null) show += ` From 30 to 120 days after, it held ${by(r.last, "better", "worse")} than the median brand's, ${ordinal(r.last_p)} of 100.`;
      else show += " Whether it lasted is read 120 days after the show.";
      if (r.mom != null) show += ` Coming in, its attention two to three months before the show was ${by(r.mom, "higher", "lower")} than two months earlier.`;
      if (r.press != null) show += ` News coverage ran ${by(r.press, "above", "below")} its usual level${r.tone != null ? `, with its tone ${r.tone >= 0 ? "up" : "down"} ${Math.abs(r.tone).toFixed(1)} points` : ""}.`;
      pairs.push(["The show", show]);
      let sw;
      if (!r.pics) sw = r.months ? `No homepage pictures on file from ${r.months[0]} to ${r.months[1]}.` : "No shop window yet.";
      else {
        sw = `${r.pics} homepage picture${r.pics > 1 ? "s" : ""} first shown from ${r.months[0]} to ${r.months[1]}`;
        sw += r.mix ? `: ${pct(r.mix.image)} campaign pictures, ${pct(r.mix.product)} product, ${pct(r.mix.other)} other.` : ".";
        if (r.shift != null) sw += ` The mix moved ${pct(r.shift)} from the previous window.`;
        if (r.dist) sw += ` Distinct from peers: ${kinds(r.dist)}.` + whatSetsApart(r.dist, "the median brand");
        if (r.move) sw += ` Moved from its pictures after the show of ${r.move.prev}: ${kinds(r.move)}.`;
        if (!r.dist && !r.move) sw += " Too few pictures of one kind to place it.";
        if (!r.complete) sw += " The window is still open.";
      }
      pairs.push(["The shop window", sw, !r.pics]);
      const lines = Object.entries(r.lines || {}).map(([k, v]) => `${v} ${k}`).join(", ");
      pairs.push(["The campaigns", r.camps ? `${r.camps} listed for the season (${lines}). Their pictures are not read yet.` : "None listed for the season.", !r.camps]);
      pairs.push(["The advertising", "Waits on Meta's archive.", true]);
      pairs.push(...clothesPairs(r.cl || {}));
      return dl(pairs);
    }
    const apartText = (m, what) => m.apart ? `told apart from ${what}` : `not told apart from ${what} at these numbers`;
    function clothesPairs(cl) {
      const out = [], rw = cl.rw, sw = cl.sw;
      if (!rw) out.push(["The runway, translated", "No runway looks on file for this show yet.", true]);
      else if (!rw.desc) out.push(["The runway, translated", `Only ${rw.looks} look${rw.looks === 1 ? "" : "s"} read from the brand's runway pages, too few to describe.`, true]);
      else {
        let t = `${rw.looks} looks read from the brand's own runway pages for the show of ${rw.show}. ${rw.desc}`;
        if (rw.dist) t += (rw.dist.p != null ? ` Distinct from the season's other runways: ${ordinal(rw.dist.p)} of 100.` : ` Told apart from ${rw.dist.apart} of the ${rw.dist.n} other runways read for the season.`)
          + clothesApart(rw.dist.a, "the median runway");
        if (rw.move) t += ` Against its runway of ${rw.move.prev}: ${apartText(rw.move, "it")}${rw.move.p != null ? `, ${ordinal(rw.move.p)} of 100 for how far it moved` : ""}.`;
        out.push(["The runway, translated", t]);
      }
      if (!sw || !sw.read) out.push(["The clothes in the shop window", "No shop-window pictures read for their clothes.", true]);
      else if (!sw.desc) out.push(["The clothes in the shop window", `${sw.worn} of the ${sw.read} pictures read show an outfit worn, too few to read the clothes.`, true]);
      else {
        let t = `${sw.worn} of the ${sw.read} pictures read show an outfit worn. ${sw.desc}`;
        if (sw.dist) t += (sw.dist.p != null ? ` Distinct from the other brands' outfits in the same months: ${ordinal(sw.dist.p)} of 100.` : ` Told apart from ${sw.dist.apart} of ${sw.dist.n} other brands.`)
          + clothesApart(sw.dist.a, "the median brand");
        if (sw.move) t += ` Against its window after the show of ${sw.move.prev}: ${apartText(sw.move, "it")}${sw.move.p != null ? `, ${ordinal(sw.move.p)} of 100 for how far it moved` : ""}.`;
        if (sw.trans) {
          t += ` Against its own runway: ${apartText(sw.trans, "it")}, likeness ${Math.min(1, sw.trans.v).toFixed(2)}`;
          t += sw.recog ? `; closer to its own runway than to ${sw.recog.less} of the ${sw.recog.of} other runways read.` : ".";
        }
        out.push(["The clothes in the shop window", t]);
      }
      return out;
    }
    const clothesP = r => r.cl && r.cl.sw && r.cl.sw.dist ? r.cl.sw.dist.p : null;
    const sortValue = r => sort === "name" ? r.name.toLowerCase() : sort === "dist" ? r.dist && r.dist.p : sort === "move" ? r.move && r.move.p : sort === "clothes" ? clothesP(r) : sort === "last" ? r.last_p : r.heat_p;
    function render() {
      const s = byKey[season];
      chips(seasons, D.seasons.slice().reverse().map(x => [x.key, x.label]), season, k => { season = k; local.set("focal-pv-season", k); render(); });
      chips(sorts, SORTS, sort, k => { sort = k; local.set("focal-pv-sort", k); render(); });
      const on = seasons.querySelector('[aria-pressed="true"]'); if (on) seasons.scrollLeft = Math.max(0, on.offsetLeft - (seasons.clientWidth - on.offsetWidth) / 2);
      title.textContent = s.label;
      meta.textContent = `Shown ${s.from} to ${s.to}${s.window_to ? `, with shop windows to ${s.window_to}` : ""}. ${s.rows.length} brands, each set against the others of the season.`;
      const c = s.counts, n = s.rows.length; stages.replaceChildren();
      for (const [nm, text, wait] of [["The show", `${c.show} of ${n} with a jump in attention`, false], ["Attention lasted", c.lasting ? `${c.lasting} of ${n}` : "read 120 days after the show", !c.lasting],
        ["The shop window", `${c.distinctness} of ${n} placed`, false], ["The clothes", c.clothes_window ? `${c.clothes_window} of ${n} shop windows` : "no shop window read yet", !c.clothes_window],
        ["The runway, translated", c.runway ? `${c.runway} of ${n} runways` : "no runway looks read yet", !c.runway],
        ["The campaigns", `${c.campaigns} of ${n} listed`, false], ["The advertising", "waits on Meta", true]]) {
        const li = el("li"); li.append(el("span", "s", nm), el("span", "v" + (wait ? " wait" : ""), text)); stages.append(li);
      }
      const rows = s.rows.slice().sort((a, b) => {
        const x = sortValue(a), y = sortValue(b);
        if (sort === "name") return x < y ? -1 : 1;
        if (x == null && y == null) return a.name < b.name ? -1 : 1;
        if (x == null) return 1; if (y == null) return -1;
        return y - x || (a.name < b.name ? -1 : 1);
      });
      rowsHost.replaceChildren();
      for (const r of rows) {
        const d = el("details"); const sm = el("summary", "pv-grid c-season");
        const who = el("span", "pv-who"); who.append(el("span", "pv-name", r.name), el("span", "pv-sub num", r.day)); sm.append(who);
        const thin = r.pics < D.thin, noPic = r.pics ? "too few of one kind" : "no pictures on file";
        sm.append(place("Distinct from peers", r.dist && r.dist.p, thin, `Distinct: ${r.dist && r.dist.p} of 100, from ${r.pics} pictures`, noPic));
        sm.append(place("Moved from last season", r.move && r.move.p, thin, `Moved: ${r.move && r.move.p} of 100, from ${r.pics} pictures`, r.pics ? "no window to compare" : noPic));
        const csw = r.cl && r.cl.sw, cp = clothesP(r);
        sm.append(place("Clothes distinct", cp, !!csw && csw.worn < D.thin, `Clothes distinct: ${cp} of 100, from ${csw && csw.worn} outfits`,
          !csw || !csw.read ? "no pictures read" : !csw.worn ? "no outfit shown" : csw.worn < 5 ? `${csw.worn} outfit${csw.worn === 1 ? "" : "s"}, too few` : "too few brands to place"));
        sm.append(place("Attention lasted", r.last_p, false, `Lasted: ${r.last_p} of 100`, r.heat == null ? "no page views" : "read after 120 days"));
        sm.append(place("Jump at the show", r.heat_p, false, `Jump: ${r.heat_p} of 100`, "too few page views"));
        sm.append(share("Campaign pictures", r.mix, r.pics));
        d.append(sm, detail(r)); rowsHost.append(d);
      }
    }
    render();
    const a = D.association, names = { distinctness: "Distinct from peers", movement: "Moved from last season", image_share: "Share of campaign pictures", momentum: "Momentum coming in" };
    const sec = el("section", "pv-fig"); sec.style.maxWidth = "40rem";
    sec.append(el("p", "pv-sec", "What goes with attention that lasts"));
    const fp = el("p", null, a.text || (a.status === "ok" ? `Across ${a.n} collections from ${a.brands} brands, each brand compared with itself.` : "Too few collections yet to say."));
    fp.style.fontSize = "0.9375rem"; sec.append(fp);
    if (a.status === "ok") {
      const svg = mk("svg", { role: "img", "aria-label": "Each measure's association with lasting attention, with its 90 per cent interval" });
      svg.append(mk("title", {}, "Each measure's association with lasting attention, with its 90 per cent interval"));
      const fig = el("figure"); fig.style.margin = "1rem 0 0"; fig.append(svg, el("figcaption", null, "Change in lasting attention, in log page views, for one standard deviation of each measure, within brands. Lines are 90 per cent intervals; a line that crosses zero shows no clear association."));
      sec.append(fig);
      const terms = a.terms.filter(t => a.coefficients[t]);
      const table = el("table", "pv-coef"); const th = el("thead"); const tr0 = el("tr");
      tr0.append(el("th", null, "Measure"), el("th", "r", "Estimate"), el("th", "r", "90% interval")); th.append(tr0); table.append(th);
      const tb = el("tbody");
      for (const t of terms) { const c = a.coefficients[t], tr = el("tr"); tr.append(el("td", null, names[t] || t), el("td", "r", signed(c.estimate, 3)), el("td", "r", `${signed(c.interval_90[0], 3)} to ${signed(c.interval_90[1], 3)}`)); tb.append(tr); }
      table.append(tb); sec.append(table);
      redraw.board = () => {
        for (const c of [...svg.children]) if (c.tagName.toLowerCase() !== "title") c.remove();
        const vals = terms.flatMap(t => a.coefficients[t].interval_90);
        const lo = Math.min(-0.04, Math.floor(Math.min(...vals) / 0.02) * 0.02), hi = Math.max(0.04, Math.ceil(Math.max(...vals) / 0.02) * 0.02);
        const W = widthOf(fig, 300, 640), L = Math.min(190, Math.round(W * 0.44)), R = 22, row = 38, top = 10, H = top + row * terms.length + 34;
        const x = v => L + (v - lo) / (hi - lo) * (W - L - R);
        svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
        for (let v = lo; v <= hi + 1e-9; v += 0.02) {
          const xv = x(v), z = Math.abs(v) < 1e-9;
          svg.append(mk("line", { x1: xv, x2: xv, y1: top - 4, y2: top + row * terms.length, class: z ? "sx-zero" : "sx-grid" }));
          if (W >= 420 || Math.round(v * 100) % 4 === 0) svg.append(mk("text", { x: xv, y: top + row * terms.length + 18, "text-anchor": "middle", class: "sx-tick" }, signed(v, 2)));
        }
        terms.forEach((t, k) => {
          const c = a.coefficients[t], y = top + row * k + row / 2;
          svg.append(mk("text", { x: 0, y: y + 4, class: "sx-lab" }, names[t] || t));
          const g = mk("g", { tabindex: "0" });
          g.append(mk("title", {}, `${names[t] || t}: ${signed(c.estimate, 3)}, 90% interval ${signed(c.interval_90[0], 3)} to ${signed(c.interval_90[1], 3)}`));
          g.append(mk("rect", { x: x(c.interval_90[0]) - 6, y: y - 12, width: x(c.interval_90[1]) - x(c.interval_90[0]) + 12, height: 24, class: "sx-hit" }));
          g.append(mk("line", { x1: x(c.interval_90[0]), x2: x(c.interval_90[1]), y1: y, y2: y, class: "sx-ci" }));
          g.append(mk("circle", { cx: x(c.estimate), cy: y, r: 5, class: "sx-dot" }));
          svg.append(g);
        });
      };
      const st = D.sticky;
      const sp = el("p", "note", `A show's attention lasts no better, within chance, than an equal jump on an ordinary day. Across the ${st.n} shows whose surprise can be measured, comparing each brand with itself on attention in English, the attention that lasted rose ${st.slope.toFixed(2)} for each unit of surprise at the show, against ${st.placebo.toFixed(2)} on ordinary days away from the brand's main shows (p ${st.p.toFixed(2)}).`);
      sec.append(sp);
      for (const t of D.sticky_more || []) sec.append(el("p", "note", t));
    }
    host.append(sec);
    methodLink(host);
  }

  /* ---- a reading: findings, then figures of two plain kinds, so a new reading needs only new content ---- */
  function renderReading(host, part) {
    const [first, ...rest] = part.findings || [];
    if (first) lead(host, part, first); else head(host, part);
    for (const f of part.figures || []) {
      const wide = el("div", "pv-wide");
      if (f.title) wide.append(el("p", "pv-sec", f.title));
      if (f.caption) wide.append(el("p", "pv-key", f.caption));
      const rows = el("div", "pv-rows");
      for (const r of f.rows) {
        const line = el("div", "pv-line pv-grid"); line.style.gridTemplateColumns = "minmax(0, 1fr)";
        const who = el("span", "pv-who stack"); who.append(el("span", "pv-name", r.name));
        if (r.sub) who.append(el("span", "pv-sub", r.sub));
        line.append(who);
        if (f.kind === "pair") {
          // then (hollow) and now (filled) on one track
          const c = el("span", "pv-cell pv-bar"); c.style.gridTemplateColumns = "minmax(0, 1fr) 7.5rem";
          const t = el("span", "pv-track"); t.title = r.title || "";
          const lo = Math.min(r.a, r.b) / f.max * 100, hi = Math.max(r.a, r.b) / f.max * 100;
          const span = el("b"); span.style.left = at(lo); span.style.width = `calc((100% - 10px) * ${(hi - lo) / 100})`;
          const was = el("i", "was"); was.style.left = at(r.a / f.max * 100);
          const now = el("i"); now.style.left = at(r.b / f.max * 100);
          t.append(span, was, now);
          c.append(t, el("span", "val num", r.label)); line.append(c);
        } else {
          const c = el("span", "pv-cell pv-share"); c.style.gridTemplateColumns = "minmax(0, 1fr) 4.5rem";
          const bar = el("span", "bar"); bar.title = r.title || "";
          const i = el("i"); i.style.width = `${Math.max(0, Math.min(100, r.v / f.max * 100))}%`;
          if (r.other) i.style.background = "var(--graphite)";
          bar.append(i); c.append(bar, el("span", "val num", r.label)); line.append(c);
        }
        rows.append(line);
      }
      wide.append(rows);
      host.append(wide);
    }
    note(host, rest, part.lede);
    methodLink(host);
  }

  /* ---- a report in its own frame: the Tom Ford report, as its own page, sized to its length ---- */
  function renderDoc(host, part) {
    const f = el("iframe", "pv-doc");
    f.title = part.title || part.tab;
    // the frame takes the height of the report's body, which does not grow with the frame, so the two cannot feed each other
    const fit = () => {
      try { const d = f.contentDocument, h = d && d.body ? Math.ceil(d.body.getBoundingClientRect().height) : 0; if (h > 0) f.style.height = h + "px"; } catch (e) {}
    };
    f.addEventListener("load", () => {
      fit();
      try {
        const w = f.contentWindow, d = f.contentDocument;
        if (w.ResizeObserver) new w.ResizeObserver(fit).observe(d.body);
        if (d.fonts) d.fonts.ready.then(fit);
      } catch (e) {}
    });
    f.srcdoc = part.html;
    host.append(f);
    redraw[part.key] = fit;
  }

  /* ---- how it is read: once, for every reading behind the password ---- */
  function renderMethod(host, part) {
    head(host, part);
    const box = el("div", "pv-method");
    for (const g of part.groups) {
      const s = el("section"); s.append(el("h4", null, g.h));
      const ul = el("ul"); for (const t of g.items) ul.append(el("li", null, t)); s.append(ul); box.append(s);
    }
    if (part.foot) box.append(el("p", "pv-key", part.foot));
    host.append(box);
  }

  const RENDER = { reshuffle: renderReshuffle, themselves: renderThemselves, loewe: renderBrand, board: renderBoard };
  const KIND = { reading: renderReading, doc: renderDoc, method: renderMethod };

  /* ---- lock, open, show ---- */
  const form = $("pv-lock"), input = $("pv-pass"), go = $("pv-go"), msg = $("pv-msg");
  function lockView() {
    form.hidden = false; $("pv-content").hidden = true;
    $("pv-intro").textContent = "Selected analysis for clients.";
  }
  function build() {
    const tabs = $("pv-tabs"), host = $("pv-parts");
    tabs.replaceChildren(); host.replaceChildren();
    for (const part of P.parts) {
      const a = el("a", null, part.tab); a.href = "#/private/" + part.key; a.dataset.key = part.key; tabs.append(a);
      const sec = el("div", "pv-part"); sec.dataset.key = part.key; sec.hidden = true;
      try { (part.kind === "doc" ? KIND.doc : (RENDER[part.key] || KIND[part.kind] || (() => {})))(sec, part); } catch (e) { console.error(e); sec.append(el("p", "note", "This part could not be drawn.")); }
      host.append(sec);
    }
  }
  function show(sub) {
    if (!P) return;
    const keys = P.parts.map(p => p.key);
    const key = keys.includes(sub) ? sub : keys[0];
    form.hidden = true; $("pv-content").hidden = false;
    // the date once, on the first report
    $("pv-intro").textContent = key === keys[0] ? `Selected analysis, as of ${P.as_of}.` : "Selected analysis.";
    for (const a of $("pv-tabs").children) a.setAttribute("aria-current", a.dataset.key === key ? "page" : "false");
    for (const s of $("pv-parts").children) s.hidden = s.dataset.key !== key;
    if (redraw[key]) redraw[key]();
  }
  async function attempt(pw, quiet) {
    if (!pw) { msg.textContent = "Enter the password."; return; }
    if (!(window.crypto && crypto.subtle)) { msg.textContent = "This browser cannot open it."; return; }
    go.disabled = true; msg.textContent = "Opening…";
    try {
      P = await unseal(pw);
      keep.set(pw); input.value = ""; msg.textContent = "";
      build(); show(current);
    } catch (e) {
      P = null; keep.clear();
      msg.textContent = e && e.name === "OperationError" ? (quiet ? "" : "That password does not open it.")
        : e && e.message === "old" ? "This browser is too old to open it; try an up-to-date one." : "It could not be loaded just now. Try again.";
    } finally { go.disabled = false; }
  }
  form.addEventListener("submit", e => { e.preventDefault(); attempt(input.value.trim()); });
  // the abridged report comes first; a client with a password goes straight to it
  const jump = $("pv-jump");
  if (jump) jump.addEventListener("click", () => { input.scrollIntoView({ block: "center", behavior: "smooth" }); input.focus({ preventScroll: true }); });
  $("pv-close").addEventListener("click", () => {
    keep.clear(); P = null;
    $("pv-parts").replaceChildren(); $("pv-tabs").replaceChildren();
    for (const k in redraw) delete redraw[k];
    lockView(); msg.textContent = "Locked."; location.hash = "#/private";
  });
  let wait; addEventListener("resize", () => { clearTimeout(wait); wait = setTimeout(() => { if (P && current !== undefined) { const k = P.parts.map(p => p.key).includes(current) ? current : P.parts[0].key; if (redraw[k]) redraw[k](); } }, 150); });
  window.focalPrivate = {
    route(sub) {
      current = sub || null;
      if (P) { show(current); return; }
      lockView();
      const pw = keep.get();
      if (pw) attempt(pw, true);
    },
  };
})();
