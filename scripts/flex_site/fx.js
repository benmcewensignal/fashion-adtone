(() => {
  // One invented look, changed a step at a time; the reader's answers for each picture, and how often each
  // answer comes up in two houses' runway looks (built from data/flex by the site's build).
  const FX = /*FX*/null/*FX*/;
  const root = document.getElementById("fx");
  if (!root || !FX) return;
  const range = root.querySelector(".fx-range");
  const pic = root.querySelector(".fx-pic");
  const now = root.querySelector(".fx-now");
  const step = root.querySelector(".fx-step");
  const list = root.querySelector(".fx-list");
  const byK = new Map(FX.frames.map(f => [f.k, f]));
  const start = byK.get(0);
  const word = v => (v in FX.labels ? FX.labels[v] : String(v).replace(/_/g, " "));
  const vals = (f, q) => [].concat(f.a[q] ?? []);
  const isList = q => FX.lists.includes(q);
  const key = (q, v) => (isList(q) ? q + "+" + v : q + "=" + v);
  const share = k => (FX.shares[k] || [null, null]).map(x => (x ? Math.round((100 * x[0]) / x[1]) + "%" : ""));
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // what changed from picture b to picture a: options added and taken away, single answers replaced
  function diff(a, b) {
    const out = [];
    for (const [q] of FX.q) {
      const va = vals(a, q), vb = vals(b, q);
      if (isList(q)) {
        va.filter(v => !vb.includes(v)).forEach(v => out.push({ q, v, on: true }));
        vb.filter(v => !va.includes(v)).forEach(v => out.push({ q, v, on: false }));
      } else if (va[0] !== vb[0]) out.push({ q, v: va[0], was: vb[0], on: true });
    }
    return out;
  }
  const qname = q => FX.q.find(x => x[0] === q)[1];

  function row(c, withQ) {
    const r = el("div", "fx-row" + (c.on ? "" : " gone"));
    const [d, b] = share(key(c.q, c.v));
    const name = withQ ? `<span class="k">${esc(qname(c.q))}:</span> ` : "";
    const val = c.on ? `<b>${esc(word(c.v))}</b>` : `<s>${esc(word(c.v))}</s>`;
    const was = c.was != null ? ` <span class="was">(was ${esc(word(c.was))})</span>` : "";
    const gone = c.on ? "" : '<span class="vh">no longer </span>';
    r.innerHTML = `<span class="w"><i class="d${c.on ? "" : " off"}" aria-hidden="true"></i><span class="t">${name}${gone}${val}${was}</span></span>` +
      `<span><span class="vh">Dior </span>${d}</span><span><span class="vh">Balenciaga </span>${b}</span>`;
    return r;
  }

  function show(k) {
    const f = byK.get(k);
    if (!f) return;
    pic.querySelectorAll(".fx-patch").forEach(img => {
      const p = +img.dataset.k;
      img.hidden = !(Math.sign(p) === Math.sign(k) && Math.abs(p) <= Math.abs(k));
    });
    pic.setAttribute("aria-label", f.alt);
    range.setAttribute("aria-valuetext", f.label);
    now.textContent = f.label;
    // this step's changes, against the picture one step nearer the start
    step.replaceChildren();
    if (k === 0) step.append(el("p", "empty", "Move the slider either way."));
    else {
      const ch = diff(f, byK.get(k - Math.sign(k)));
      if (!ch.length) step.append(el("p", "empty", "The reader's answers did not change at this step."));
      ch.forEach(c => step.append(row(c, true)));
    }
    // every answer, those that differ from the start marked
    list.replaceChildren();
    const away = diff(f, start);
    for (const [q, name] of FX.q) {
      const g = el("div", "fx-q");
      g.append(el("span", "qn", esc(name)));
      vals(f, q).forEach(v => {
        const c = away.find(x => x.q === q && x.v === v && x.on);
        const r = row({ q, v, on: true, was: c && c.was }, false);
        if (!c) r.querySelector("i.d").classList.add("off");
        g.append(r);
      });
      away.filter(x => x.q === q && !x.on).forEach(c => g.append(row(c, false)));
      list.append(g);
    }
  }
  range.addEventListener("input", () => show(+range.value));
  show(+range.value);
})();
