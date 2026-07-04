/* SkyFrame loads editor (v0.3/v0.4) — load patterns (incl. wind generator),
   static cases (with P-Δ), response-spectrum cases (with live spectrum
   preview), time-history cases (with sparkline preview), combos (add /
   envelope) and the mass source.
   All edits are pure mutations on the client model dict via modeledit.js;
   the host app marks the model dirty through onChange(). No frameworks. */

import * as ME from "./modeledit.js";
import { spectrumChart, thSparkline } from "./charts.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2) => (v == null || !isFinite(v)) ? "—" :
  v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: d });

const KIND_LABEL = { dead: "dead", live: "live", quake: "quake", other: "other" };

export class LoadsEditor {
  /**
   * root: container element (the scrollable loads pane body)
   * opts: { getModel, onChange, toast, onWind }
   *   onChange() — called after EVERY model mutation (host marks dirty).
   *   onWind(params) — async; generates a wind pattern (backend or mock).
   */
  constructor(root, opts) {
    this.root = root;
    this.getModel = opts.getModel;
    this.onChange = opts.onChange;
    this.toast = opts.toast;
    this.onWind = opts.onWind || null;
    this._wind = { name: "WX", direction: "X", V: 40, exposure: "C", Cp: 0.8 };
  }

  /** mutation helpers — structural edits re-render, value edits don't */
  _mutated(rerender = true) {
    this.onChange();
    if (rerender) this.render();
  }

  render() {
    const m = this.getModel();
    if (!m) { this.root.innerHTML = ""; return; }
    const scroll = this.root.scrollTop;
    this.root.textContent = "";
    this.root.appendChild(this._patternsSection(m));
    this.root.appendChild(this._casesSection(m));
    this.root.appendChild(this._rsSection(m));
    this.root.appendChild(this._thSection(m));
    this.root.appendChild(this._combosSection(m));
    this.root.appendChild(this._massSection(m));
    this.root.scrollTop = scroll;
  }

  _section(id, title, subtitle, addLabel, onAdd) {
    const sec = document.createElement("section");
    sec.className = "loads-section";
    sec.id = id;
    const head = document.createElement("header");
    head.className = "loads-section-head";
    head.innerHTML = `<div><h3>${esc(title)}</h3>` +
      (subtitle ? `<p class="muted">${subtitle}</p>` : "") + `</div>`;
    if (addLabel) {
      const btn = document.createElement("button");
      btn.className = "btn btn-small";
      btn.textContent = addLabel;
      btn.addEventListener("click", onAdd);
      head.appendChild(btn);
    }
    sec.appendChild(head);
    return sec;
  }

  _nameInput(value, className, onRename) {
    const i = document.createElement("input");
    i.type = "text";
    i.className = className;
    i.value = value;
    i.spellcheck = false;
    i.addEventListener("change", () => {
      const nu = i.value.trim();
      if (nu === value) return;
      if (!onRename(nu)) {
        i.value = value;
        this.toast("Rename failed", "Name empty or already in use", "error", 4000);
      } else this._mutated();
    });
    return i;
  }

  _delBtn(blockedBy, what, onDel) {
    const b = document.createElement("button");
    b.className = "del";
    b.textContent = "✕";
    if (blockedBy && blockedBy.length) {
      b.disabled = true;
      b.title = `Can't delete — referenced by ${blockedBy.join(", ")}`;
    } else {
      b.title = `Delete ${what}`;
      b.addEventListener("click", onDel);
    }
    return b;
  }

  /* ============================================================ patterns */
  _patternsSection(m) {
    const sec = this._section("ls-patterns", "Load patterns",
      "What loads are applied — assign member / area loads in Model mode.",
      "+ Add pattern", () => { ME.addPattern(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const names = Object.keys(m.patterns);
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No load patterns yet.</p>`;

    for (const name of names) {
      const p = m.patterns[name];
      const row = document.createElement("div");
      row.className = "lp-row";

      row.appendChild(this._nameInput(name, "lp-name",
        nu => ME.renamePattern(m, name, nu)));

      const chips = document.createElement("div");
      chips.className = "kind-chips";
      for (const k of ME.PATTERN_KINDS) {
        const c = document.createElement("button");
        c.className = `kind-chip k-${k}` + (p.kind === k ? " is-on" : "");
        c.textContent = KIND_LABEL[k];
        c.title = `Pattern kind: ${k}`;
        c.addEventListener("click", () => { p.kind = k; this._mutated(); });
        chips.appendChild(c);
      }
      row.appendChild(chips);

      const counts = document.createElement("span");
      counts.className = "lp-counts muted";
      const nm = (p.member_loads || []).length;
      const na = (p.area_loads || []).length;
      const ns = (p.story_forces || []).length;
      counts.innerHTML =
        `<b>${nm}</b> member · <b>${na}</b> area · <b>${ns}</b> story`;
      counts.title = `${nm} member load${nm === 1 ? "" : "s"}, ${na} area load${na === 1 ? "" : "s"}, ${ns} story force${ns === 1 ? "" : "s"}`;
      row.appendChild(counts);

      const refs = ME.patternRefs(m, name);
      row.appendChild(this._delBtn(refs, `pattern ${name}`, () => {
        if (ME.deletePattern(m, name)) this._mutated();
      }));
      list.appendChild(row);
    }
    sec.appendChild(list);
    sec.appendChild(this._windCard(m));
    return sec;
  }

  /* ---- v0.4: wind pattern generator (POST /api/pattern/wind, mock local) */
  _windCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card";
    card.id = "windCard";
    const w = this._wind;
    card.innerHTML = `
      <div class="wind-head">
        <b>Wind pattern generator</b>
        <span class="muted">ASCE-style velocity-pressure profile → story forces</span>
      </div>
      <div class="wind-fields">
        <label class="rs-field"><span>name</span>
          <input id="windName" type="text" value="${esc(w.name)}" spellcheck="false"></label>
        <label class="rs-field"><span>direction</span>
          <select id="windDir">
            <option value="X"${w.direction === "X" ? " selected" : ""}>X</option>
            <option value="Y"${w.direction === "Y" ? " selected" : ""}>Y</option>
          </select></label>
        <label class="rs-field"><span>V (m/s)</span>
          <input id="windV" type="number" min="10" step="1" value="${w.V}"></label>
        <label class="rs-field"><span>exposure</span>
          <select id="windExp">
            ${["B", "C", "D"].map(e =>
              `<option${e === w.exposure ? " selected" : ""}>${e}</option>`).join("")}
          </select></label>
        <label class="rs-field"><span>Cp</span>
          <input id="windCp" type="number" min="0" step="0.05" value="${w.Cp}"></label>
        <button class="btn btn-small" id="windGen">Generate</button>
      </div>`;
    const $id = id => card.querySelector(`#${id}`);
    $id("windName").addEventListener("change", e => { w.name = e.target.value.trim() || "WX"; });
    $id("windDir").addEventListener("change", e => { w.direction = e.target.value; });
    $id("windV").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v > 0) w.V = v; else e.target.value = String(w.V);
    });
    $id("windExp").addEventListener("change", e => { w.exposure = e.target.value; });
    $id("windCp").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v)) w.Cp = v; else e.target.value = String(w.Cp);
    });
    $id("windGen").addEventListener("click", async () => {
      if (!this.onWind) return;
      const btn = $id("windGen");
      btn.disabled = true;
      try {
        await this.onWind({ ...w });
        this.render();
      } catch (err) {
        this.toast("Wind generation failed", err.message, "error", 7000);
      } finally {
        btn.disabled = false;
      }
    });
    return card;
  }

  /* ============================================================ cases */
  _factorChip(keys, current, factor, { onKey, onFactor, onRemove }) {
    const chip = document.createElement("span");
    chip.className = "factor-chip";
    const sel = document.createElement("select");
    for (const k of keys) {
      const o = document.createElement("option");
      o.value = k; o.textContent = k; o.selected = k === current;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => onKey(sel.value));
    const times = document.createElement("i");
    times.textContent = "×";
    const num = document.createElement("input");
    num.type = "number"; num.step = "0.05"; num.value = String(factor);
    num.addEventListener("change", () => {
      const v = parseFloat(num.value);
      if (isFinite(v)) onFactor(v);
      else num.value = String(factor);
    });
    const x = document.createElement("button");
    x.className = "chip-x"; x.textContent = "✕"; x.title = "Remove";
    x.addEventListener("click", onRemove);
    chip.append(sel, times, num, x);
    return chip;
  }

  /** Chip row editing a {key: factor} dict against a pool of candidate keys. */
  _factorChips(dict, pool, addTitle) {
    const wrap = document.createElement("div");
    wrap.className = "chips-wrap";
    for (const [key, f] of Object.entries(dict)) {
      const free = pool.filter(k => k === key || dict[k] === undefined);
      wrap.appendChild(this._factorChip(free, key, f, {
        onKey: nu => {
          if (nu === key) return;
          dict[nu] = dict[key]; delete dict[key];
          this._mutated();
        },
        onFactor: v => { dict[key] = v; this._mutated(false); },
        onRemove: () => { delete dict[key]; this._mutated(); },
      }));
    }
    const add = document.createElement("button");
    add.className = "chip-add";
    add.textContent = "+";
    add.title = addTitle;
    const unused = pool.filter(k => dict[k] === undefined);
    if (!unused.length) { add.disabled = true; add.title = "All entries already referenced"; }
    else add.addEventListener("click", () => { dict[unused[0]] = 1.0; this._mutated(); });
    wrap.appendChild(add);
    return wrap;
  }

  _casesSection(m) {
    const sec = this._section("ls-cases", "Static load cases",
      "Each case sums pattern × factor. P-Δ runs the case with geometric stiffness.",
      "+ Add case", () => { ME.addCase(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const patPool = Object.keys(m.patterns);
    const names = Object.keys(m.cases);
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No static cases yet.</p>`;

    for (const name of names) {
      const c = m.cases[name];
      const row = document.createElement("div");
      row.className = "lc-row";

      row.appendChild(this._nameInput(name, "lc-name",
        nu => ME.renameCase(m, name, nu)));
      row.appendChild(this._factorChips(c.patterns, patPool, "Add a pattern to this case"));

      const pd = document.createElement("label");
      pd.className = "pd-toggle" + (c.pdelta ? " is-on" : "");
      pd.title = "Include P-Δ (second-order) effects for this case";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = !!c.pdelta;
      cb.addEventListener("change", () => { c.pdelta = cb.checked; this._mutated(); });
      pd.append(cb, document.createTextNode("P-Δ"));
      row.appendChild(pd);

      const refs = ME.caseRefs(m, name);
      row.appendChild(this._delBtn(refs, `case ${name}`, () => {
        if (ME.deleteCase(m, name)) this._mutated();
      }));
      list.appendChild(row);
    }
    sec.appendChild(list);
    return sec;
  }

  /* ============================================================ RS cases */
  _rsSection(m) {
    const sec = this._section("ls-rs", "Response-spectrum cases",
      "Modal RSA — results are positive envelopes (±). Spectrum in T (s) vs Sa (g).",
      "+ Add RS case", () => { ME.addRsCase(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const names = Object.keys(m.rs_cases);
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No response-spectrum cases yet.</p>`;

    for (const name of names) list.appendChild(this._rsCard(m, name));
    sec.appendChild(list);
    return sec;
  }

  _rsCard(m, name) {
    const rc = m.rs_cases[name];
    const card = document.createElement("div");
    card.className = "rs-card";

    /* header: name · direction · damping · method · scale · delete */
    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renameRsCase(m, name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };
    const dir = document.createElement("select");
    dir.innerHTML = `<option value="X">X</option><option value="Y">Y</option>`;
    dir.value = rc.direction;
    dir.addEventListener("change", () => { rc.direction = dir.value; this._mutated(false); });
    head.appendChild(mkField("direction", dir));

    const damp = document.createElement("input");
    damp.type = "number"; damp.step = "0.01"; damp.min = "0"; damp.max = "0.5";
    damp.value = String(rc.damping);
    damp.addEventListener("change", () => {
      const v = parseFloat(damp.value);
      if (isFinite(v) && v >= 0 && v < 1) { rc.damping = v; this._mutated(false); }
      else damp.value = String(rc.damping);
    });
    head.appendChild(mkField("damping", damp));

    const meth = document.createElement("select");
    meth.innerHTML = `<option>CQC</option><option>SRSS</option>`;
    meth.value = rc.combo_method;
    meth.addEventListener("change", () => { rc.combo_method = meth.value; this._mutated(false); });
    head.appendChild(mkField("modal combo", meth));

    const scale = document.createElement("input");
    scale.type = "number"; scale.step = "0.05"; scale.min = "0";
    scale.value = String(rc.scale);
    scale.addEventListener("change", () => {
      const v = parseFloat(scale.value);
      if (isFinite(v) && v > 0) { rc.scale = v; this._mutated(false); }
      else scale.value = String(rc.scale);
    });
    head.appendChild(mkField("scale", scale));

    head.appendChild(this._delBtn(null, `RS case ${name}`, () => {
      if (ME.deleteRsCase(m, name)) this._mutated();
    }));
    card.appendChild(head);

    /* body: point table + live preview */
    const body = document.createElement("div");
    body.className = "rs-body";

    const tableWrap = document.createElement("div");
    tableWrap.className = "rs-table";
    const chartWrap = document.createElement("div");
    chartWrap.className = "rs-chart";
    const drawChart = () => {
      chartWrap.textContent = "";
      const title = document.createElement("div");
      title.className = "chart-title";
      title.innerHTML = `Spectrum preview <span class="unit">Sa g vs T s · ${esc(rc.direction)} · ${esc(rc.combo_method)}</span>`;
      chartWrap.appendChild(title);
      chartWrap.appendChild(spectrumChart(rc.spectrum));
    };
    const rebuildTable = () => {
      tableWrap.textContent = "";
      const head2 = document.createElement("div");
      head2.className = "rs-pt head";
      head2.innerHTML = `<span>T s</span><span>Sa g</span><span></span>`;
      tableWrap.appendChild(head2);

      rc.spectrum.forEach((pt, idx) => {
        const r = document.createElement("div");
        r.className = "rs-pt";
        const mkNum = (col, min) => {
          const i = document.createElement("input");
          i.type = "number"; i.step = col === 0 ? "0.1" : "0.05"; i.min = String(min);
          i.value = String(pt[col]);
          i.addEventListener("change", () => {
            const v = parseFloat(i.value);
            if (isFinite(v) && v >= min) {
              pt[col] = v;
              this._mutated(false);
              drawChart();
            } else i.value = String(pt[col]);
          });
          return i;
        };
        r.appendChild(mkNum(0, 0));
        r.appendChild(mkNum(1, 0));
        const x = document.createElement("button");
        x.className = "chip-x"; x.textContent = "✕"; x.title = "Remove point";
        x.addEventListener("click", () => {
          rc.spectrum.splice(idx, 1);
          this._mutated(false);
          rebuildTable(); drawChart();
        });
        r.appendChild(x);
        tableWrap.appendChild(r);
      });

      const foot = document.createElement("div");
      foot.className = "rs-table-foot";
      const addPt = document.createElement("button");
      addPt.className = "btn btn-small";
      addPt.textContent = "+ Point";
      addPt.addEventListener("click", () => {
        const last = rc.spectrum[rc.spectrum.length - 1];
        const T = last ? +(last[0] + 0.5).toFixed(2) : 0;
        const Sa = last ? +(Math.max(0.05, last[1] * 0.8)).toFixed(3) : 0.4;
        rc.spectrum.push([T, Sa]);
        this._mutated(false);
        rebuildTable(); drawChart();
      });
      const ubc = document.createElement("button");
      ubc.className = "btn btn-small";
      ubc.textContent = "UBC-style default";
      ubc.title = "Replace the spectrum with a flat-plateau / (1/T)-decay UBC-style shape";
      ubc.addEventListener("click", () => {
        rc.spectrum = ME.ubcSpectrum();
        this._mutated(false);
        rebuildTable(); drawChart();
      });
      foot.append(addPt, ubc);
      tableWrap.appendChild(foot);
    };
    rebuildTable();
    drawChart();
    // direction / method changes update the preview subtitle live
    dir.addEventListener("change", drawChart);
    meth.addEventListener("change", drawChart);

    body.append(tableWrap, chartWrap);
    card.appendChild(body);
    return card;
  }

  /* ============================================================ TH cases (v0.4) */
  _thSection(m) {
    const sec = this._section("ls-th", "Time-history cases",
      "Linear modal time-history — ground acceleration record in m/s². " +
      "Results land in the <b>Time History</b> tab after a solve.",
      "+ Add TH case", () => { ME.addThCase(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const names = Object.keys(m.th_cases);
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No time-history cases yet.</p>`;

    for (const name of names) list.appendChild(this._thCard(m, name));
    sec.appendChild(list);
    return sec;
  }

  _thCard(m, name) {
    const tc = m.th_cases[name];
    const card = document.createElement("div");
    card.className = "rs-card th-card";

    /* header: name · direction · damping · scale · dt · delete */
    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renameThCase(m, name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };
    const mkNum = (value, step, min, set) => {
      const i = document.createElement("input");
      i.type = "number"; i.step = step; i.min = String(min);
      i.value = String(value);
      i.addEventListener("change", () => {
        const v = parseFloat(i.value);
        if (isFinite(v) && v >= min && set(v) !== false) this._mutated(false);
        else i.value = String(value);
      });
      return i;
    };

    const dir = document.createElement("select");
    dir.innerHTML = `<option value="X">X</option><option value="Y">Y</option>`;
    dir.value = tc.direction;
    dir.addEventListener("change", () => { tc.direction = dir.value; this._mutated(false); });
    head.appendChild(mkField("direction", dir));
    head.appendChild(mkField("damping", mkNum(tc.damping, "0.01", 0, v => {
      if (v >= 1) return false;
      tc.damping = v;
    })));
    head.appendChild(mkField("scale", mkNum(tc.scale, "0.05", 0, v => { tc.scale = v; })));
    const dtIn = mkNum(tc.dt, "0.005", 0.001, v => { tc.dt = v; });
    head.appendChild(mkField("dt s", dtIn));

    head.appendChild(this._delBtn(null, `TH case ${name}`, () => {
      if (ME.deleteThCase(m, name)) this._mutated();
    }));
    card.appendChild(head);

    /* body: accel textarea + sparkline preview */
    const body = document.createElement("div");
    body.className = "th-body";

    const left = document.createElement("div");
    left.className = "th-record";
    const lbl = document.createElement("div");
    lbl.className = "th-label";
    lbl.innerHTML = `Acceleration record <span class="unit">m/s² · comma / whitespace separated</span>`;
    const ta = document.createElement("textarea");
    ta.className = "th-accel";
    ta.spellcheck = false;
    ta.rows = 5;
    ta.placeholder = "0, 0.12, 0.31, …";
    const fill = () => { ta.value = tc.accel.map(v => +(+v).toFixed(4)).join(", "); };
    fill();
    ta.addEventListener("change", () => {
      const vals = ME.parseAccel(ta.value);
      if (vals === null) {
        this.toast("Record not parsed", "Only numbers, commas and whitespace are allowed", "error", 5000);
        fill();
        return;
      }
      tc.accel = vals;
      this._mutated(false);
      drawSpark();
    });
    const foot = document.createElement("div");
    foot.className = "rs-table-foot";
    const seed = document.createElement("button");
    seed.className = "btn btn-small";
    seed.textContent = "Sine demo";
    seed.title = "Seed a ramped decaying 1.2 Hz sine record (8 s @ dt)";
    seed.addEventListener("click", () => {
      tc.accel = ME.sineRecord(tc.dt, 8);
      fill();
      this._mutated(false);
      drawSpark();
    });
    const clear = document.createElement("button");
    clear.className = "btn btn-small";
    clear.textContent = "Clear";
    clear.addEventListener("click", () => {
      tc.accel = [];
      fill();
      this._mutated(false);
      drawSpark();
    });
    foot.append(seed, clear);
    left.append(lbl, ta, foot);

    const right = document.createElement("div");
    right.className = "rs-chart th-chart";
    const drawSpark = () => {
      right.textContent = "";
      const title = document.createElement("div");
      title.className = "chart-title";
      title.innerHTML = `Record preview <span class="unit">${esc(tc.direction)} · ζ ${fmt(tc.damping, 3)} · ×${fmt(tc.scale, 2)}</span>`;
      right.appendChild(title);
      right.appendChild(thSparkline(tc.accel, tc.dt, { width: 320, height: 84 }));
    };
    drawSpark();
    dir.addEventListener("change", drawSpark);
    dtIn.addEventListener("change", drawSpark);

    body.append(left, right);
    card.appendChild(body);
    return card;
  }

  /* ============================================================ combos */
  _combosSection(m) {
    const sec = this._section("ls-combos", "Load combinations",
      "<b>add</b> = factored linear sum of case results · <b>envelope</b> = " +
      "component-wise max/min across the factored cases (tables gain a " +
      "max/min toggle). Static cases only.",
      "+ Add combo", () => { ME.addCombo(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const casePool = Object.keys(m.cases);
    const names = Object.keys(m.combos);
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No combinations yet.</p>`;

    for (const name of names) {
      const cb = m.combos[name];
      const row = document.createElement("div");
      row.className = "lc-row";
      row.appendChild(this._nameInput(name, "lc-name",
        nu => ME.renameCombo(m, name, nu)));
      row.appendChild(this._factorChips(cb.cases, casePool, "Add a static case to this combo"));

      const typ = document.createElement("select");
      typ.className = "combo-type";
      typ.title = "add: factored sum · envelope: max/min across the factored cases";
      typ.innerHTML = `<option value="add">add</option><option value="envelope">envelope</option>`;
      typ.value = cb.combo_type === "envelope" ? "envelope" : "add";
      typ.addEventListener("change", () => { cb.combo_type = typ.value; this._mutated(false); });
      row.appendChild(typ);

      row.appendChild(this._delBtn(null, `combo ${name}`, () => {
        if (ME.deleteCombo(m, name)) this._mutated();
      }));
      list.appendChild(row);
    }
    sec.appendChild(list);
    return sec;
  }

  /* ============================================================ mass source (v0.4) */
  _massSection(m) {
    const sec = this._section("ls-mass", "Mass source",
      "Seismic/dynamic mass = Σ pattern × factor (gravity loads → mass). " +
      "Default is <b>1.0 × DEAD</b>.", null, null);
    const row = document.createElement("div");
    row.className = "lc-row mass-row";
    const tag = document.createElement("span");
    tag.className = "mass-tag";
    tag.textContent = "mass =";
    row.appendChild(tag);
    row.appendChild(this._factorChips(m.mass_source, ME.patternNames(m),
      "Add a pattern to the mass source"));
    sec.appendChild(row);
    return sec;
  }
}
