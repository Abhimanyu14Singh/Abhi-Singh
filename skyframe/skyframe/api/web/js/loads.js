/* SkyFrame loads editor (v0.3/v0.4) — load patterns (incl. wind generator),
   static cases (with P-Δ), response-spectrum cases (with live spectrum
   preview), time-history cases (with sparkline preview), combos (add /
   envelope) and the mass source.
   All edits are pure mutations on the client model dict via modeledit.js;
   the host app marks the model dirty through onChange(). No frameworks. */

import * as ME from "./modeledit.js";
import { spectrumChart, thSparkline } from "./charts.js";
import { asce7SpectrumPreview, spectrumParameters, elfCs } from "./mock.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2) => (v == null || !isFinite(v)) ? "—" :
  v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: d });

const KIND_LABEL = { dead: "dead", live: "live", quake: "quake", other: "other",
  notional: "notional", wind: "wind" };

export class LoadsEditor {
  /**
   * root: container element (the scrollable loads pane body)
   * opts: { getModel, onChange, toast, onWind,
   *         onSelfWeight, onAutoCombos, onCodeRs, onElf }
   *   onChange() — called after EVERY model mutation (host marks dirty).
   *   onWind(params) — async; generates a wind pattern (backend or mock).
   *   onSelfWeight/onAutoCombos/onCodeRs/onElf(params) — async v0.7 ASCE 7
   *     code tools; each mutates the model server-side (or mock) and the host
   *     re-adopts the returned model. This editor re-renders after each.
   */
  constructor(root, opts) {
    this.root = root;
    this.getModel = opts.getModel;
    this.onChange = opts.onChange;
    this.toast = opts.toast;
    this.onWind = opts.onWind || null;
    this.onSelfWeight = opts.onSelfWeight || null;
    this.onAutoCombos = opts.onAutoCombos || null;
    this.onCodeRs = opts.onCodeRs || null;
    this.onElf = opts.onElf || null;
    // v0.10 — RS directional combination + notional loads
    this.onRsDirectional = opts.onRsDirectional || null;
    this.onNotional = opts.onNotional || null;
    this._wind = { name: "WX", direction: "X", V: 40, exposure: "C", Cp: 0.8 };
    // v0.7 code-tool card state (persisted across re-renders)
    this._sw = { name: "SW", factor: 1.0 };
    // v0.17 — ev/SDS: optional vertical seismic Ev = 0.2·SDS·D folded into D
    this._combos = { standard: "LRFD", ev: false, SDS: 1.0 };
    this._codeRs = { name: "RS-Code", Ss: 1.0, S1: 0.6, site_class: "D", R: 8, Ie: 1.0, direction: "X" };
    this._elf = { name: "EQ-ELF", SDS: 1.0, SD1: 0.6, R: 8, Ie: 1.0, direction: "X" };
    // v0.10 card state (persisted across re-renders)
    this._rsDir = { name: "RS-Dir", name_x: "", name_y: "", method: "100_30" };
    this._notional = { name: "NOTIONAL", direction: "X", coeff: 0.002, gravity_pattern: "DEAD" };
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
    this.root.appendChild(this._codeToolsSection(m));
    this.root.appendChild(this._casesSection(m));
    this.root.appendChild(this._functionsSection(m));   // v0.13
    this.root.appendChild(this._rsSection(m));
    this.root.appendChild(this._thSection(m));
    this.root.appendChild(this._poSection(m));
    this.root.appendChild(this._bucklingSection(m));
    this.root.appendChild(this._stagedSection(m));
    this.root.appendChild(this._combosSection(m));
    this.root.appendChild(this._sectionCutsSection(m));  // v0.13
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
      const wrap = document.createElement("div");
      wrap.className = "lp-wrap";
      const row = document.createElement("div");
      row.className = "lp-row";

      row.appendChild(this._nameInput(name, "lp-name",
        nu => ME.renamePattern(m, name, nu)));

      const chips = document.createElement("div");
      chips.className = "kind-chips";
      // v0.10 — a special kind (e.g. "notional") shows a read-only badge chip
      if (p.kind && !ME.PATTERN_KINDS.includes(p.kind)) {
        const c = document.createElement("span");
        c.className = `kind-chip k-${p.kind} is-on is-locked`;
        c.textContent = KIND_LABEL[p.kind] || p.kind;
        c.title = `Pattern kind: ${p.kind} (auto-generated)`;
        chips.appendChild(c);
      }
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
      const nt = (p.thermal_loads || []).length;
      counts.innerHTML =
        `<b>${nm}</b> member · <b>${na}</b> area · <b>${ns}</b> story` +
        (nt ? ` · <b>${nt}</b> ΔT` : "");
      counts.title = `${nm} member load${nm === 1 ? "" : "s"}, ${na} area load${na === 1 ? "" : "s"}, ${ns} story force${ns === 1 ? "" : "s"}, ${nt} thermal load${nt === 1 ? "" : "s"}`;
      row.appendChild(counts);

      const refs = ME.patternRefs(m, name);
      row.appendChild(this._delBtn(refs, `pattern ${name}`, () => {
        if (ME.deletePattern(m, name)) this._mutated();
      }));
      wrap.appendChild(row);

      // v0.8 — accidental torsion for lateral (quake / wind) patterns
      const isLateral = p.kind === "quake" || p.kind === "wind" || !!p.wind || !!p.elf;
      if (isLateral) wrap.appendChild(this._accidentalTorsion(m, p));

      list.appendChild(wrap);
    }
    sec.appendChild(list);
    sec.appendChild(this._windCard(m));
    sec.appendChild(this._notionalCard(m));
    sec.appendChild(this._thermalCard(m));
    return sec;
  }

  /* ---- v0.10: notional loads (AISC stability) generator.
     POST /api/pattern/notional, mock local. Creates a lateral pattern that
     shows up like wind/EQ patterns (story_forces). */
  _notionalCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card notional-card";
    card.id = "notionalCard";
    const s = this._notional;
    const pats = ME.patternNames(m);
    card.innerHTML = `
      <div class="wind-head">
        <b>Notional loads (AISC stability)</b>
        <span class="muted">coeff × gravity applied laterally — direct analysis method</span>
      </div>
      <div class="wind-fields">
        <label class="rs-field"><span>name</span>
          <input id="ntName" type="text" value="${esc(s.name)}" spellcheck="false"></label>
        <label class="rs-field"><span>direction</span>
          <select id="ntDir">
            <option value="X"${s.direction === "X" ? " selected" : ""}>X</option>
            <option value="Y"${s.direction === "Y" ? " selected" : ""}>Y</option>
          </select></label>
        <label class="rs-field"><span>coefficient</span>
          <input id="ntCoeff" type="number" min="0" step="0.001" value="${s.coeff}"></label>
        <label class="rs-field"><span>gravity pattern</span>
          <select id="ntGrav">${pats.map(p =>
            `<option value="${esc(p)}"${p === s.gravity_pattern ? " selected" : ""}>${esc(p)}</option>`).join("")}</select></label>
        <button class="btn btn-small" id="ntCreate">Create</button>
      </div>
      <p class="code-note muted">Applies a notional lateral force <b>Ni = coeff × Yi</b>
        (default <b>0.002</b>, AISC 360 §C2.2b) at each level, where Yi is the gravity load from
        the chosen pattern — accounts for initial out-of-plumbness in the
        <b>direct analysis method</b>. Creates a lateral pattern like wind / EQ.</p>`;
    const $ = id => card.querySelector("#" + id);
    $("ntName").addEventListener("change", e => { s.name = e.target.value.trim() || "NOTIONAL"; e.target.value = s.name; });
    $("ntDir").addEventListener("change", e => { s.direction = e.target.value; });
    $("ntCoeff").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v > 0) s.coeff = v; else e.target.value = String(s.coeff);
    });
    $("ntGrav").addEventListener("change", e => { s.gravity_pattern = e.target.value; });
    $("ntCreate").addEventListener("click", () =>
      this._runTool($("ntCreate"), this.onNotional, { ...s }, "Notional pattern creation failed"));
    if (!this.onNotional) $("ntCreate").disabled = true;
    return card;
  }

  /* ---- v0.8: accidental-torsion row (ASCE 7 §12.8.4) for a lateral pattern */
  _accidentalTorsion(m, p) {
    const wrap = document.createElement("div");
    wrap.className = "acc-tors";
    const toggle = document.createElement("label");
    toggle.className = "pd-toggle acc-tors-toggle" + (p.accidental_torsion ? " is-on" : "");
    toggle.title = "Adds a story torque Mt = ±ecc·B·Fx per ASCE 7-16 §12.8.4";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.className = "acc-tors-cb"; cb.checked = !!p.accidental_torsion;
    toggle.append(cb, document.createTextNode("Accidental torsion (±ecc)"));

    const eccWrap = document.createElement("label");
    eccWrap.className = "rs-field acc-ecc" + (p.accidental_torsion ? "" : " hidden");
    const es = document.createElement("span");
    es.innerHTML = `ecc <span class="unit">fraction of B</span>`;
    const eccIn = document.createElement("input");
    eccIn.type = "number"; eccIn.step = "0.01"; eccIn.min = "0"; eccIn.max = "0.5";
    eccIn.className = "acc-ecc-in";
    eccIn.value = String(p.ecc ?? 0.05);
    eccIn.addEventListener("change", () => {
      const v = parseFloat(eccIn.value);
      if (isFinite(v) && v >= 0 && v <= 0.5) { p.ecc = v; this._mutated(false); }
      else eccIn.value = String(p.ecc ?? 0.05);
    });
    eccWrap.append(es, eccIn);

    cb.addEventListener("change", () => {
      p.accidental_torsion = cb.checked;
      toggle.classList.toggle("is-on", cb.checked);
      eccWrap.classList.toggle("hidden", !cb.checked);
      this._mutated(false);
    });

    const note = document.createElement("span");
    note.className = "acc-tors-note muted";
    note.textContent = "±5% mass eccentricity adds a per-story torque (ASCE 7 §12.8.4).";

    wrap.append(toggle, eccWrap, note);
    return wrap;
  }

  /* ---- v0.8: global thermal expansion coefficient card */
  _thermalCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card thermal-card";
    card.id = "thermalCard";
    card.innerHTML = `
      <div class="wind-head">
        <b>Thermal expansion</b>
        <span class="muted">global α · assign per-member ΔT in Model mode</span>
      </div>
      <div class="wind-fields">
        <label class="rs-field"><span>α <span class="unit">/°C</span></span>
          <input id="thermAlpha" type="number" step="1e-6" min="0" value="${m.thermal_alpha ?? 1.2e-5}"></label>
        <span class="code-note muted" style="flex:1 1 200px">Coefficient of thermal expansion applied to every member's
          ΔT thermal load. Select a beam/column in <b>Model</b> mode to assign ΔT.</span>
      </div>`;
    const inp = card.querySelector("#thermAlpha");
    inp.addEventListener("change", () => {
      const v = parseFloat(inp.value);
      if (isFinite(v) && v >= 0) { m.thermal_alpha = v; this._mutated(false); }
      else inp.value = String(m.thermal_alpha ?? 1.2e-5);
    });
    return card;
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

  /* ==================================================== code tools (v0.7)
     Four ASCE 7-16 helpers grouped under one subheading — self-weight,
     auto load combos, code response-spectrum (with live preview) and ELF.
     Each mirrors the wind card: fill inputs → callback → host re-adopts. */
  _codeToolsSection(m) {
    const sec = this._section("ls-codetools", "Code tools (ASCE 7)",
      "One-click ASCE 7-16 helpers — self-weight, load combinations, code " +
      "response spectrum and equivalent lateral force. Each mutates the model " +
      "(live backend or local mock) and refreshes the editors below.",
      null, null);
    const grid = document.createElement("div");
    grid.className = "code-tools";
    grid.append(
      this._selfWeightCard(m),
      this._autoCombosCard(m),
      this._codeRsCard(m),
      this._elfCard(m),
    );
    sec.appendChild(grid);
    return sec;
  }

  /** Run an async code-tool callback with button disabling + error toast. */
  async _runTool(btn, cb, params, failTitle) {
    if (!cb) return;
    btn.disabled = true;
    try {
      await cb(params);
      this.render();
    } catch (err) {
      this.toast(failTitle, err.message, "error", 7000);
      btn.disabled = false;
    }
  }

  /* ---- self-weight pattern (POST /api/pattern/selfweight) */
  _selfWeightCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card code-card";
    card.id = "selfWeightCard";
    const s = this._sw;
    card.innerHTML = `
      <div class="wind-head">
        <b>Self-weight pattern</b>
        <span class="muted">real member &amp; shell self-weight</span>
      </div>
      <div class="wind-fields">
        <label class="rs-field"><span>name</span>
          <input id="swName" type="text" value="${esc(s.name)}" spellcheck="false"></label>
        <label class="rs-field"><span>factor</span>
          <input id="swFactor" type="number" step="0.1" value="${s.factor}"></label>
        <button class="btn btn-small" id="swAdd">Add self-weight pattern</button>
      </div>
      <p class="code-note muted">Adds real member &amp; shell self-weight from material density
        as a dead pattern's <code>self_weight_factor</code>. A pattern with the same name is
        <b>replaced</b>.</p>`;
    const $ = id => card.querySelector("#" + id);
    $("swName").addEventListener("change", e => { s.name = e.target.value.trim() || "SW"; e.target.value = s.name; });
    $("swFactor").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v)) s.factor = v; else e.target.value = String(s.factor);
    });
    $("swAdd").addEventListener("click", () =>
      this._runTool($("swAdd"), this.onSelfWeight, { ...s }, "Self-weight failed"));
    if (!this.onSelfWeight) $("swAdd").disabled = true;
    return card;
  }

  /* ---- auto ASCE 7 load combinations (POST /api/combos/asce7) */
  _autoCombosCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card code-card";
    card.id = "autoCombosCard";
    const c = this._combos;
    card.innerHTML = `
      <div class="wind-head">
        <b>Auto load combinations</b>
        <span class="muted">ASCE 7-16 §2.3 / §2.4</span>
      </div>
      <div class="wind-fields">
        <label class="rs-field"><span>standard</span>
          <select id="acStd">
            <option value="LRFD"${c.standard === "LRFD" ? " selected" : ""}>LRFD (§2.3)</option>
            <option value="ASD"${c.standard === "ASD" ? " selected" : ""}>ASD (§2.4)</option>
          </select></label>
        <button class="btn btn-small" id="acGen">Generate ASCE 7 combinations</button>
      </div>
      <div class="check-row ac-ev-row">
        <label title="Fold the vertical seismic component Ev = 0.2·SDS·D (ASCE 7-16 §12.4.2.2) into the seismic combos' D factors">
          <input type="checkbox" id="acEv"${c.ev ? " checked" : ""}>
          Include vertical seismic E<sub>v</sub> (0.2·S<sub>DS</sub>·D)
        </label>
        <label class="rs-field ac-sds-field"><span>SDS (g)</span>
          <input id="acSDS" type="number" step="0.05" min="0" value="${c.SDS}"${c.ev ? "" : " disabled"}></label>
      </div>
      <p class="code-note muted" id="acEvNote"></p>
      <p class="code-note muted">Builds factored combinations from your
        <b>Dead / Live / Quake / Wind</b> cases (±E, ±W sign variants). Terms with no
        matching case are dropped. <span class="warn-inline">Appends to existing combinations.</span></p>`;
    const $ = id => card.querySelector("#" + id);
    const fnum = v => String(+v.toFixed(2));
    const evNote = () => {
      const n = $("acEvNote");
      if (!c.ev) {
        n.innerHTML = `Seismic combos keep the plain D factors ` +
          `(${c.standard === "ASD" ? "1.0D + 0.7E · 0.6D + 0.7E" : "1.2D + E · 0.9D + E"}).`;
        return;
      }
      const s = c.SDS;
      n.innerHTML = c.standard === "ASD"
        ? `E<sub>v</sub> on — ASD seismic combos become <b>${fnum(1.0 + 0.14 * s)}D + 0.7E</b> / ` +
          `<b>${fnum(0.6 - 0.14 * s)}D + 0.7E</b> at SDS=${fnum(s)} (±0.14·S<sub>DS</sub>·D).`
        : `E<sub>v</sub> on — strength seismic combos become <b>${fnum(1.2 + 0.2 * s)}D + E</b> / ` +
          `<b>${fnum(0.9 - 0.2 * s)}D + E</b> at SDS=${fnum(s)} (±0.2·S<sub>DS</sub>·D).`;
    };
    $("acStd").addEventListener("change", e => { c.standard = e.target.value; evNote(); });
    $("acEv").addEventListener("change", e => {
      c.ev = e.target.checked;
      $("acSDS").disabled = !c.ev;
      evNote();
    });
    $("acSDS").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v >= 0) c.SDS = v; else e.target.value = String(c.SDS);
      evNote();
    });
    $("acGen").addEventListener("click", () =>
      this._runTool($("acGen"), this.onAutoCombos,
        { standard: c.standard, ...(c.ev ? { SDS: c.SDS } : {}) },
        "Combo generation failed"));
    if (!this.onAutoCombos) $("acGen").disabled = true;
    evNote();
    return card;
  }

  /* ---- code response-spectrum case + live preview (POST /api/case/rs-code) */
  _codeRsCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card code-card code-rs-card";
    card.id = "codeRsCard";
    const s = this._codeRs;

    const head = document.createElement("div");
    head.className = "wind-head";
    head.innerHTML = `<b>Code response spectrum</b>
      <span class="muted">ASCE 7-16 design spectrum → RS case</span>`;
    card.appendChild(head);

    const body = document.createElement("div");
    body.className = "code-rs-body";

    const form = document.createElement("div");
    form.className = "wind-fields code-rs-fields";
    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const sp = document.createElement("span");
      sp.textContent = label;
      w.append(sp, node);
      return w;
    };
    const mkNum = (val, step, min, set) => {
      const i = document.createElement("input");
      i.type = "number"; i.step = step; i.min = String(min); i.value = String(val);
      i.addEventListener("change", () => {
        const v = parseFloat(i.value);
        if (isFinite(v) && v >= min && set(v) !== false) redraw();
        else i.value = String(val);
      });
      return i;
    };
    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.value = s.name; nameIn.spellcheck = false;
    nameIn.addEventListener("change", () => { s.name = nameIn.value.trim() || "RS-Code"; nameIn.value = s.name; });
    form.appendChild(mkField("case name", nameIn));
    form.appendChild(mkField("Ss (g)", mkNum(s.Ss, "0.05", 0, v => { s.Ss = v; })));
    form.appendChild(mkField("S1 (g)", mkNum(s.S1, "0.05", 0, v => { s.S1 = v; })));

    const siteSel = document.createElement("select");
    siteSel.innerHTML = ["A", "B", "C", "D", "E", "F"].map(c =>
      `<option${c === s.site_class ? " selected" : ""}>${c}</option>`).join("");
    siteSel.addEventListener("change", () => { s.site_class = siteSel.value; redraw(); });
    form.appendChild(mkField("site class", siteSel));
    form.appendChild(mkField("R", mkNum(s.R, "0.5", 0.1, v => { if (v <= 0) return false; s.R = v; })));
    form.appendChild(mkField("Ie", mkNum(s.Ie, "0.05", 0.1, v => { if (v <= 0) return false; s.Ie = v; })));
    const dirSel = document.createElement("select");
    dirSel.innerHTML = `<option value="X">X</option><option value="Y">Y</option>`;
    dirSel.value = s.direction;
    dirSel.addEventListener("change", () => { s.direction = dirSel.value; });
    form.appendChild(mkField("direction", dirSel));

    const chartWrap = document.createElement("div");
    chartWrap.className = "rs-chart code-rs-chart";

    const warn = document.createElement("p");
    warn.className = "code-note warn-inline";
    warn.id = "codeRsWarn";

    const redraw = () => {
      chartWrap.textContent = "";
      const title = document.createElement("div");
      title.className = "chart-title";
      const isF = s.site_class === "F";
      const site = isF ? "D" : s.site_class;
      const prm = spectrumParameters(s.Ss, s.S1, site);
      const pts = asce7SpectrumPreview(s.Ss, s.S1, site);
      title.innerHTML = `Design spectrum preview ` +
        `<span class="unit">SDS ${fmt(prm.SDS, 3)} · SD1 ${fmt(prm.SD1, 3)} · Ts ${fmt(prm.Ts, 3)} s</span>`;
      chartWrap.appendChild(title);
      chartWrap.appendChild(spectrumChart(pts));
      const ann = document.createElement("div");
      ann.className = "code-rs-annot";
      ann.innerHTML =
        `<span><b>SDS</b> ${fmt(prm.SDS, 3)} g</span>` +
        `<span><b>SD1</b> ${fmt(prm.SD1, 3)} g</span>` +
        `<span><b>T0</b> ${fmt(prm.T0, 3)} s</span>` +
        `<span><b>Ts</b> ${fmt(prm.Ts, 3)} s</span>` +
        `<span><b>Ie/R</b> ${fmt(s.Ie / s.R, 4)}</span>` +
        `<span><b>pts</b> ${pts.length}</span>`;
      chartWrap.appendChild(ann);
      warn.textContent = isF
        ? "Site class F needs a site-specific study (ASCE 7-16 §11.4.8) — preview uses class D."
        : "";
      warn.style.display = isF ? "" : "none";
    };

    const btnRow = document.createElement("div");
    btnRow.className = "code-rs-btnrow";
    const btn = document.createElement("button");
    btn.className = "btn btn-small"; btn.id = "codeRsCreate"; btn.textContent = "Create RS case";
    btn.addEventListener("click", () =>
      this._runTool(btn, this.onCodeRs, {
        name: s.name, direction: s.direction, Ss: s.Ss, S1: s.S1,
        site_class: s.site_class, R: s.R, Ie: s.Ie,
      }, "RS case creation failed"));
    if (!this.onCodeRs) btn.disabled = true;
    btnRow.appendChild(btn);

    const left = document.createElement("div");
    left.className = "code-rs-left";
    left.append(form, warn, btnRow);
    body.append(left, chartWrap);
    card.appendChild(body);
    redraw();
    return card;
  }

  /* ---- ELF seismic pattern (POST /api/pattern/elf) */
  _elfCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card code-card";
    card.id = "elfCard";
    const s = this._elf;
    const stories = m.stories || [];
    const hn = stories.length ? stories[stories.length - 1].elevation : 1;

    const head = document.createElement("div");
    head.className = "wind-head";
    head.innerHTML = `<b>ELF seismic pattern</b>
      <span class="muted">ASCE 7-16 §12.8 equivalent lateral force</span>`;
    card.appendChild(head);

    const form = document.createElement("div");
    form.className = "wind-fields";
    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const sp = document.createElement("span");
      sp.textContent = label;
      w.append(sp, node);
      return w;
    };
    const mkNum = (val, step, min, set) => {
      const i = document.createElement("input");
      i.type = "number"; i.step = step; i.min = String(min); i.value = String(val);
      i.addEventListener("change", () => {
        const v = parseFloat(i.value);
        if (isFinite(v) && v >= min && set(v) !== false) syncReadout();
        else i.value = String(val);
      });
      return i;
    };
    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.value = s.name; nameIn.spellcheck = false;
    nameIn.addEventListener("change", () => { s.name = nameIn.value.trim() || "EQ-ELF"; nameIn.value = s.name; });
    form.appendChild(mkField("name", nameIn));
    form.appendChild(mkField("SDS (g)", mkNum(s.SDS, "0.05", 0, v => { s.SDS = v; })));
    form.appendChild(mkField("SD1 (g)", mkNum(s.SD1, "0.05", 0, v => { s.SD1 = v; })));
    form.appendChild(mkField("R", mkNum(s.R, "0.5", 0.1, v => { if (v <= 0) return false; s.R = v; })));
    form.appendChild(mkField("Ie", mkNum(s.Ie, "0.05", 0.1, v => { if (v <= 0) return false; s.Ie = v; })));
    const dirSel = document.createElement("select");
    dirSel.innerHTML = `<option value="X">X</option><option value="Y">Y</option>`;
    dirSel.value = s.direction;
    dirSel.addEventListener("change", () => { s.direction = dirSel.value; });
    form.appendChild(mkField("direction", dirSel));
    const btn = document.createElement("button");
    btn.className = "btn btn-small"; btn.id = "elfCreate"; btn.textContent = "Create ELF pattern";
    btn.addEventListener("click", () =>
      this._runTool(btn, this.onElf, {
        name: s.name, SDS: s.SDS, SD1: s.SD1, R: s.R, Ie: s.Ie, direction: s.direction,
      }, "ELF pattern creation failed"));
    if (!this.onElf) btn.disabled = true;
    form.appendChild(btn);
    card.appendChild(form);

    const readout = document.createElement("p");
    readout.className = "code-note muted";
    readout.id = "elfReadout";
    const syncReadout = () => {
      const r = elfCs(s.SDS, s.SD1, s.R, s.Ie, hn);
      const cs = r ? fmt(r.Cs, 4) : "—";
      const ta = r ? fmt(r.Ta, 3) : "—";
      readout.innerHTML = `Seismic response coefficient <b>Cs = ${cs}</b> ` +
        `(Ta ≈ ${ta} s, hn ${fmt(hn, 1)} m). Base shear <b>V = Cs·W</b> and the story-force ` +
        `distribution are computed <b>server-side</b> from the model mass.`;
    };
    syncReadout();
    card.appendChild(readout);
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
    sec.appendChild(this._rsDirectionalCard(m));
    return sec;
  }

  /* ---- v0.10: RS directional combination (ASCE 7 §12.5).
     Combine an RS-X case and an RS-Y case by 100/30 or SRSS; POST
     /api/case/rs-directional. The combined case appears as "RS: <name>" in
     all results selectors (it lands in rs_cases at solve time). */
  _rsDirectionalCard(m) {
    const card = document.createElement("div");
    card.className = "wind-card rs-dir-card";
    card.id = "rsDirCard";
    const s = this._rsDir;
    const rsNames = Object.keys(m.rs_cases || {});
    // default the X / Y selects to the first sensible RS cases
    if (!s.name_x || !rsNames.includes(s.name_x))
      s.name_x = rsNames.find(n => (m.rs_cases[n].direction || "X") === "X") || rsNames[0] || "";
    if (!s.name_y || !rsNames.includes(s.name_y))
      s.name_y = rsNames.find(n => (m.rs_cases[n].direction || "X") === "Y") || rsNames[0] || "";

    const opt = (sel) => rsNames.length
      ? rsNames.map(n => `<option value="${esc(n)}"${n === sel ? " selected" : ""}>${esc(n)}</option>`).join("")
      : `<option value="">— no RS cases —</option>`;

    const head = document.createElement("div");
    head.className = "wind-head";
    head.innerHTML = `<b>Directional combination (ASCE 7 §12.5)</b>
      <span class="muted">combine RS-X &amp; RS-Y → a single directional case</span>`;
    card.appendChild(head);

    const fields = document.createElement("div");
    fields.className = "wind-fields";
    fields.innerHTML = `
      <label class="rs-field"><span>name</span>
        <input id="rsDirName" type="text" value="${esc(s.name)}" spellcheck="false"></label>
      <label class="rs-field"><span>RS-X case</span>
        <select id="rsDirX">${opt(s.name_x)}</select></label>
      <label class="rs-field"><span>RS-Y case</span>
        <select id="rsDirY">${opt(s.name_y)}</select></label>
      <label class="rs-field"><span>method</span>
        <select id="rsDirMethod">
          <option value="100_30"${s.method === "100_30" ? " selected" : ""}>100/30</option>
          <option value="SRSS"${s.method === "SRSS" ? " selected" : ""}>SRSS</option>
        </select></label>
      <button class="btn btn-small" id="rsDirCreate">Create</button>`;
    card.appendChild(fields);

    const note = document.createElement("p");
    note.className = "code-note muted";
    note.innerHTML = `<b>100/30</b> takes 100 % of one direction with 30 % of the orthogonal
      (max of the two orderings); <b>SRSS</b> takes the square root of the sum of the squares.
      The result is a positive envelope that appears as <b>RS: ${esc(s.name)}</b> in every
      results selector.`;
    card.appendChild(note);

    const $ = id => card.querySelector("#" + id);
    $("rsDirName").addEventListener("change", e => {
      s.name = e.target.value.trim() || "RS-Dir"; e.target.value = s.name;
      note.innerHTML = note.innerHTML.replace(/RS: [^<]*/, `RS: ${esc(s.name)}`);
    });
    $("rsDirX").addEventListener("change", e => { s.name_x = e.target.value; });
    $("rsDirY").addEventListener("change", e => { s.name_y = e.target.value; });
    $("rsDirMethod").addEventListener("change", e => { s.method = e.target.value; });
    const btn = $("rsDirCreate");
    btn.addEventListener("click", () => {
      if (!s.name_x || !s.name_y) {
        this.toast("Pick RS cases", "Select both an RS-X and an RS-Y case first", "error", 4000);
        return;
      }
      this._runTool(btn, this.onRsDirectional, { ...s }, "Directional combination failed");
    });
    if (!this.onRsDirectional || rsNames.length < 1) btn.disabled = true;

    // existing directional combos (list + delete)
    const combos = Object.keys(m.rs_combos || {});
    if (combos.length) {
      const list = document.createElement("div");
      list.className = "rs-dir-list";
      for (const name of combos) {
        const rc = m.rs_combos[name];
        const row = document.createElement("div");
        row.className = "rs-dir-row";
        const label = document.createElement("span");
        label.className = "rs-dir-label";
        label.innerHTML = `<b>${esc(name)}</b> <span class="muted">` +
          `${esc(rc.name_x || "—")} + ${esc(rc.name_y || "—")} · ${esc(rc.method === "SRSS" ? "SRSS" : "100/30")}</span>`;
        row.appendChild(label);
        row.appendChild(this._delBtn(null, `RS combo ${name}`, () => {
          if (ME.deleteRsCombo(m, name)) this._mutated();
        }));
        list.appendChild(row);
      }
      card.appendChild(list);
    }
    return card;
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

    // v0.13 — reference a library spectrum function ("(inline)" keeps the
    // inline point editor; a named function hides/disables it).
    const funcNames = Object.keys(m.spectrum_functions || {});
    const funcSel = document.createElement("select");
    funcSel.className = "func-select";
    funcSel.innerHTML = `<option value="">(inline)</option>` +
      funcNames.map(n => `<option value="${esc(n)}"${rc.function === n ? " selected" : ""}>${esc(n)}</option>`).join("");
    if (rc.function && !funcNames.includes(rc.function))    // dangling ref
      funcSel.insertAdjacentHTML("beforeend",
        `<option value="${esc(rc.function)}" selected>${esc(rc.function)} (missing)</option>`);
    funcSel.value = rc.function || "";
    head.appendChild(mkField("function", funcSel));

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
    // v0.13 — points come from the referenced function when one is set
    const activePoints = () => {
      const fn = rc.function && (m.spectrum_functions || {})[rc.function];
      return fn ? (fn.points || []) : rc.spectrum;
    };
    const drawChart = () => {
      chartWrap.textContent = "";
      const title = document.createElement("div");
      title.className = "chart-title";
      const src = rc.function ? `function ${esc(rc.function)}` : "inline";
      title.innerHTML = `Spectrum preview <span class="unit">Sa g vs T s · ${esc(rc.direction)} · ${esc(rc.combo_method)} · ${src}</span>`;
      chartWrap.appendChild(title);
      chartWrap.appendChild(spectrumChart(activePoints()));
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

    // v0.13 — "using function <name>" note replaces the inline editor
    const funcNote = document.createElement("p");
    funcNote.className = "func-ref muted";
    const applyFuncState = () => {
      const on = !!rc.function;
      tableWrap.classList.toggle("hidden", on);
      funcNote.classList.toggle("hidden", !on);
      if (on) {
        const missing = !(m.spectrum_functions || {})[rc.function];
        funcNote.innerHTML = missing
          ? `⚠ using function <b>${esc(rc.function)}</b> — not found in the library`
          : `Using function <b>${esc(rc.function)}</b> — the inline point table is disabled. ` +
            `Pick <b>(inline)</b> to edit points here.`;
      }
      drawChart();
    };
    funcSel.addEventListener("change", () => {
      rc.function = funcSel.value || "";
      this._mutated(false);
      applyFuncState();
    });
    applyFuncState();

    body.append(tableWrap, funcNote, chartWrap);
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

    // v0.13 — reference a library time-history function ("(inline)" keeps the
    // inline accel record; a named function hides/disables it).
    const funcNames = Object.keys(m.th_functions || {});
    const funcSel = document.createElement("select");
    funcSel.className = "func-select";
    funcSel.innerHTML = `<option value="">(inline)</option>` +
      funcNames.map(n => `<option value="${esc(n)}"${tc.function === n ? " selected" : ""}>${esc(n)}</option>`).join("");
    if (tc.function && !funcNames.includes(tc.function))
      funcSel.insertAdjacentHTML("beforeend",
        `<option value="${esc(tc.function)}" selected>${esc(tc.function)} (missing)</option>`);
    funcSel.value = tc.function || "";
    head.appendChild(mkField("function", funcSel));

    head.appendChild(this._delBtn(null, `TH case ${name}`, () => {
      if (ME.deleteThCase(m, name)) this._mutated();
    }));
    card.appendChild(head);

    /* v0.6: nonlinear (plastic-hinge) controls */
    card.appendChild(this._thNonlinear(m, tc));

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
    // v0.13 — record comes from the referenced function when one is set
    const activeRecord = () => {
      const fn = tc.function && (m.th_functions || {})[tc.function];
      return fn ? { values: fn.values || [], dt: fn.dt || tc.dt } : { values: tc.accel, dt: tc.dt };
    };
    const drawSpark = () => {
      right.textContent = "";
      const title = document.createElement("div");
      title.className = "chart-title";
      const src = tc.function ? `function ${esc(tc.function)}` : "inline";
      title.innerHTML = `Record preview <span class="unit">${esc(tc.direction)} · ζ ${fmt(tc.damping, 3)} · ×${fmt(tc.scale, 2)} · ${src}</span>`;
      right.appendChild(title);
      const rec = activeRecord();
      right.appendChild(thSparkline(rec.values, rec.dt, { width: 320, height: 84 }));
    };
    drawSpark();
    dir.addEventListener("change", drawSpark);
    dtIn.addEventListener("change", drawSpark);

    // v0.13 — "using function <name>" note replaces the inline record editor
    const funcNote = document.createElement("p");
    funcNote.className = "func-ref muted";
    const applyFuncState = () => {
      const on = !!tc.function;
      left.classList.toggle("hidden", on);
      funcNote.classList.toggle("hidden", !on);
      if (on) {
        const fn = (m.th_functions || {})[tc.function];
        funcNote.innerHTML = fn
          ? `Using function <b>${esc(tc.function)}</b> <span class="unit">${(fn.values || []).length} pts · dt ${fmt(fn.dt, 3)} s</span> — ` +
            `the inline record is disabled. Pick <b>(inline)</b> to paste a record here.`
          : `⚠ using function <b>${esc(tc.function)}</b> — not found in the library`;
      }
      drawSpark();
    };
    funcSel.addEventListener("change", () => {
      tc.function = funcSel.value || "";
      this._mutated(false);
      applyFuncState();
    });
    applyFuncState();

    const leftCol = document.createElement("div");
    leftCol.className = "th-left-col";
    leftCol.append(left, funcNote);
    body.append(leftCol, right);
    card.appendChild(body);
    return card;
  }

  /* ---- v0.6: nonlinear plastic-hinge block for a TH case (reuses the
     pushover hinge-picker pattern). */
  _thNonlinear(m, tc) {
    const wrap = document.createElement("div");
    wrap.className = "th-nl-controls";

    const toggle = document.createElement("label");
    toggle.className = "pd-toggle th-nl-toggle" + (tc.nonlinear ? " is-on" : "");
    toggle.title = "Run this case with Steel01 plastic hinges (gravity applied first)";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!tc.nonlinear;
    cb.className = "th-nl-cb";
    toggle.append(cb, document.createTextNode("Nonlinear (plastic hinges)"));
    wrap.appendChild(toggle);

    const panel = document.createElement("div");
    panel.className = "th-nl-panel" + (tc.nonlinear ? "" : " hidden");
    wrap.appendChild(panel);

    cb.addEventListener("change", () => {
      tc.nonlinear = cb.checked;
      toggle.classList.toggle("is-on", cb.checked);
      panel.classList.toggle("hidden", !cb.checked);
      this._mutated(false);
    });

    const mkNum = (value, step, min, set) => {
      const i = document.createElement("input");
      i.type = "number"; i.step = step; i.min = String(min);
      i.value = value != null ? String(value) : "";
      i.addEventListener("change", () => {
        const v = parseFloat(i.value);
        if (i.value.trim() === "") { set(null); this._mutated(false); return; }
        if (isFinite(v) && v >= min && set(v) !== false) this._mutated(false);
        else i.value = value != null ? String(value) : "";
      });
      return i;
    };
    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.innerHTML = label;
      w.append(s, node);
      return w;
    };

    /* row: gravity factors + default My + hardening + hinges */
    const row = document.createElement("div");
    row.className = "th-nl-row";

    const grav = document.createElement("div");
    grav.className = "lc-row po-grav th-nl-grav";
    const gtag = document.createElement("span");
    gtag.className = "mass-tag"; gtag.textContent = "gravity =";
    gtag.title = "Static gravity state applied and held before the record runs";
    grav.append(gtag, this._factorChips(tc.gravity, ME.patternNames(m),
      "Add a gravity pattern held during the record"));
    row.appendChild(grav);

    row.appendChild(mkField("default M<sub>y</sub> <span class='unit'>kN·m</span>",
      mkNum(tc.default_My, "25", 0, v => {
        if (v == null) { delete tc.default_My; return; }
        if (v <= 0) return false; tc.default_My = v;
      })));
    row.appendChild(mkField("hardening",
      mkNum(tc.hardening, "0.01", 0, v => { if (v >= 1) return false; tc.hardening = v; })));

    const hingesSel = document.createElement("select");
    hingesSel.innerHTML =
      `<option value="column_base">column base</option><option value="all_ends">all ends</option>`;
    hingesSel.value = tc.hinges || "column_base";
    hingesSel.addEventListener("change", () => { tc.hinges = hingesSel.value; this._mutated(false); });
    row.appendChild(mkField("hinges", hingesSel));
    panel.appendChild(row);

    /* per-member My override picker + table (reuse pushover pattern) */
    const pick = document.createElement("div");
    pick.className = "po-pick";
    const storySel = document.createElement("select");
    storySel.innerHTML = `<option value="">all stories</option>` +
      m.stories.map(s => `<option>${esc(s.name)}</option>`).join("");
    const kindSel = document.createElement("select");
    kindSel.innerHTML = `<option value="column">columns</option>
      <option value="beam">beams</option><option value="">all kinds</option>`;
    const memSel = document.createElement("select");
    memSel.className = "po-mem";
    const myIn = document.createElement("input");
    myIn.type = "number"; myIn.step = "25"; myIn.min = "1"; myIn.value = "250";
    myIn.title = "Hinge yield moment My (kN·m)";
    const rebuildMemSel = () => {
      const st = storySel.value, kd = kindSel.value;
      const cands = m.members.filter(mm =>
        (!st || mm.story === st) &&
        (!kd || mm.kind === kd) &&
        tc.My[mm.uid] === undefined);
      memSel.innerHTML = cands.length
        ? cands.map(mm => `<option value="${esc(mm.uid)}">${esc(mm.uid)} · ${esc(mm.kind)}</option>`).join("")
        : `<option value="">— none left —</option>`;
    };
    rebuildMemSel();
    storySel.addEventListener("change", rebuildMemSel);
    kindSel.addEventListener("change", rebuildMemSel);
    const addBtn = document.createElement("button");
    addBtn.className = "btn btn-small"; addBtn.textContent = "+ My override";
    addBtn.addEventListener("click", () => {
      const uid = memSel.value, v = parseFloat(myIn.value);
      if (!uid || !isFinite(v) || v <= 0) return;
      tc.My[uid] = v; this._mutated();
    });
    const applyCols = document.createElement("button");
    applyCols.className = "btn btn-small"; applyCols.textContent = "Apply default to columns";
    applyCols.title = "Assign the default My as a hinge override on every column";
    applyCols.addEventListener("click", () => {
      const v = tc.default_My;
      if (!isFinite(v) || v <= 0) {
        this.toast("No default My", "Enter a positive default My first", "error", 4000); return;
      }
      let n = 0;
      for (const mm of m.members) if (mm.kind === "column") { tc.My[mm.uid] = v; n++; }
      this._mutated();
      this.toast("My overrides set", `My = ${v} kN·m on ${n} columns`, "info", 3500);
    });
    pick.append(storySel, kindSel, memSel, myIn, addBtn, applyCols);
    panel.appendChild(pick);

    const rows = document.createElement("div");
    rows.className = "po-rows";
    const memBy = {};
    for (const mm of m.members) memBy[mm.uid] = mm;
    const entries = Object.entries(tc.My);
    if (entries.length) {
      const headRow = document.createElement("div");
      headRow.className = "po-row head";
      headRow.innerHTML = `<span>Member</span><span>Kind · story</span><span>M<sub>y</sub> kN·m</span><span></span>`;
      rows.appendChild(headRow);
    } else {
      const empty = document.createElement("p");
      empty.className = "muted po-count";
      empty.textContent = "No per-member overrides — the default My covers eligible members.";
      rows.appendChild(empty);
    }
    for (const [uid, my] of entries) {
      const r = document.createElement("div");
      r.className = "po-row";
      const u = document.createElement("b"); u.textContent = uid;
      const meta = document.createElement("span"); meta.className = "muted";
      const mm = memBy[uid];
      meta.textContent = mm ? `${mm.kind} · ${mm.story}` : "missing member";
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "25"; inp.min = "1"; inp.value = String(my);
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value);
        if (isFinite(v) && v > 0) { tc.My[uid] = v; this._mutated(false); }
        else inp.value = String(tc.My[uid]);
      });
      const x = document.createElement("button");
      x.className = "chip-x"; x.textContent = "✕"; x.title = "Remove override";
      x.addEventListener("click", () => { delete tc.My[uid]; this._mutated(); });
      r.append(u, meta, inp, x);
      rows.appendChild(r);
    }
    panel.appendChild(rows);
    return wrap;
  }

  /* ============================================================ pushover (v0.5) */
  _poSection(m) {
    const sec = this._section("ls-pushover", "Pushover cases",
      "Displacement-controlled nonlinear static analysis — assign hinge yield " +
      "moments M<sub>y</sub>; the capacity curve lands in the <b>Pushover</b> tab after a solve.",
      "+ Add pushover", () => { ME.addPushoverCase(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const names = Object.keys(m.pushover_cases || {});
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No pushover cases yet.</p>`;
    for (const name of names) list.appendChild(this._poCard(m, name));
    sec.appendChild(list);
    return sec;
  }

  _poCard(m, name) {
    const pc = m.pushover_cases[name];
    const card = document.createElement("div");
    card.className = "rs-card po-card";

    /* header: name · direction · target drift % · steps · hardening · delete */
    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renamePushoverCase(m, name, nu)));

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
    dir.value = pc.direction;
    dir.addEventListener("change", () => { pc.direction = dir.value; this._mutated(false); });
    head.appendChild(mkField("direction", dir));
    head.appendChild(mkField("target drift %",
      mkNum(+(pc.target_drift * 100).toFixed(3), "0.25", 0.05,
        v => { pc.target_drift = v / 100; })));
    head.appendChild(mkField("steps", mkNum(pc.steps, "10", 2,
      v => { pc.steps = Math.round(v); })));
    head.appendChild(mkField("hardening", mkNum(pc.hardening, "0.01", 0,
      v => { if (v >= 1) return false; pc.hardening = v; })));
    head.appendChild(this._delBtn(null, `pushover case ${name}`, () => {
      if (ME.deletePushoverCase(m, name)) this._mutated();
    }));
    card.appendChild(head);

    /* gravity pattern factors */
    const grav = document.createElement("div");
    grav.className = "lc-row po-grav";
    const tag = document.createElement("span");
    tag.className = "mass-tag";
    tag.textContent = "gravity =";
    tag.title = "Gravity state held constant during the lateral push (pattern × factor)";
    grav.appendChild(tag);
    grav.appendChild(this._factorChips(pc.gravity, ME.patternNames(m),
      "Add a gravity pattern to this pushover"));
    card.appendChild(grav);

    /* hinges: default My + apply-to-columns + per-member table */
    const body = document.createElement("div");
    body.className = "po-hinges";

    const defRow = document.createElement("div");
    defRow.className = "po-defrow";
    const defIn = document.createElement("input");
    defIn.type = "number"; defIn.step = "25"; defIn.min = "0";
    defIn.id = "poDefaultMy";
    defIn.value = pc.default_My != null ? String(pc.default_My) : "";
    defIn.placeholder = "—";
    defIn.addEventListener("change", () => {
      const v = parseFloat(defIn.value);
      if (isFinite(v) && v > 0) { pc.default_My = v; this._mutated(false); }
      else if (defIn.value.trim() === "") { delete pc.default_My; this._mutated(false); }
      else defIn.value = pc.default_My != null ? String(pc.default_My) : "";
    });
    const defLbl = document.createElement("label");
    defLbl.className = "rs-field";
    defLbl.innerHTML = `<span>default M<sub>y</sub> kN·m</span>`;
    defLbl.appendChild(defIn);
    const applyBtn = document.createElement("button");
    applyBtn.className = "btn btn-small";
    applyBtn.textContent = "Apply to all columns";
    applyBtn.title = "Assign the default My as a hinge on every column";
    applyBtn.addEventListener("click", () => {
      const v = parseFloat(defIn.value);
      if (!isFinite(v) || v <= 0) {
        this.toast("No default My", "Enter a positive default My first", "error", 4000);
        return;
      }
      pc.default_My = v;
      let n = 0;
      for (const mm of m.members) if (mm.kind === "column") { pc.My[mm.uid] = v; n++; }
      this._mutated();
      this.toast("Hinges assigned", `My = ${v} kN·m on ${n} columns`, "info", 3500);
    });
    const count = document.createElement("span");
    count.className = "muted po-count";
    count.textContent = `${Object.keys(pc.My).length} hinge${Object.keys(pc.My).length === 1 ? "" : "s"}`;
    defRow.append(defLbl, applyBtn, count);
    body.appendChild(defRow);

    /* member picker: story + kind filters → member select + My + add */
    const pick = document.createElement("div");
    pick.className = "po-pick";
    const storySel = document.createElement("select");
    storySel.innerHTML = `<option value="">all stories</option>` +
      m.stories.map(s => `<option>${esc(s.name)}</option>`).join("");
    const kindSel = document.createElement("select");
    kindSel.innerHTML = `<option value="column">columns</option>
      <option value="beam">beams</option><option value="">all kinds</option>`;
    const memSel = document.createElement("select");
    memSel.className = "po-mem";
    const myIn = document.createElement("input");
    myIn.type = "number"; myIn.step = "25"; myIn.min = "1"; myIn.value = "250";
    myIn.title = "Hinge yield moment My (kN·m)";
    const rebuildMemSel = () => {
      const st = storySel.value, kd = kindSel.value;
      const cands = m.members.filter(mm =>
        (!st || mm.story === st) &&
        (!kd || mm.kind === kd || (kd === "beam" && mm.kind === "brace")) &&
        pc.My[mm.uid] === undefined);
      memSel.innerHTML = cands.length
        ? cands.map(mm => `<option value="${esc(mm.uid)}">${esc(mm.uid)} · ${esc(mm.kind)}</option>`).join("")
        : `<option value="">— none left —</option>`;
    };
    rebuildMemSel();
    storySel.addEventListener("change", rebuildMemSel);
    kindSel.addEventListener("change", rebuildMemSel);
    const addBtn = document.createElement("button");
    addBtn.className = "btn btn-small";
    addBtn.textContent = "+ Hinge";
    addBtn.addEventListener("click", () => {
      const uid = memSel.value;
      const v = parseFloat(myIn.value);
      if (!uid || !isFinite(v) || v <= 0) return;
      pc.My[uid] = v;
      this._mutated();
    });
    pick.append(storySel, kindSel, memSel, myIn, addBtn);
    body.appendChild(pick);

    /* existing hinge rows */
    const rows = document.createElement("div");
    rows.className = "po-rows";
    const memBy = {};
    for (const mm of m.members) memBy[mm.uid] = mm;
    const entries = Object.entries(pc.My);
    if (entries.length) {
      const headRow = document.createElement("div");
      headRow.className = "po-row head";
      headRow.innerHTML = `<span>Member</span><span>Kind · story</span><span>M<sub>y</sub> kN·m</span><span></span>`;
      rows.appendChild(headRow);
    }
    for (const [uid, my] of entries) {
      const row = document.createElement("div");
      row.className = "po-row";
      const u = document.createElement("b");
      u.textContent = uid;
      const meta = document.createElement("span");
      meta.className = "muted";
      const mm = memBy[uid];
      meta.textContent = mm ? `${mm.kind} · ${mm.story}` : "missing member";
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "25"; inp.min = "1";
      inp.value = String(my);
      inp.addEventListener("change", () => {
        const v = parseFloat(inp.value);
        if (isFinite(v) && v > 0) { pc.My[uid] = v; this._mutated(false); }
        else inp.value = String(pc.My[uid]);
      });
      const x = document.createElement("button");
      x.className = "chip-x"; x.textContent = "✕"; x.title = "Remove hinge";
      x.addEventListener("click", () => { delete pc.My[uid]; this._mutated(); });
      row.append(u, meta, inp, x);
      rows.appendChild(row);
    }
    body.appendChild(rows);
    card.appendChild(body);
    return card;
  }

  /* ============================================================ buckling (v0.10) */
  _bucklingSection(m) {
    const sec = this._section("ls-buckling", "Buckling cases",
      "Linearized (eigenvalue) buckling — the gravity state below is scaled " +
      "until the structure buckles. Critical load factors λ land in the " +
      "<b>Buckling</b> tab after a solve; λ &lt; 1 means buckling below the applied gravity.",
      "+ Add buckling", () => { ME.addBucklingCase(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const names = Object.keys(m.buckling_cases || {});
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No buckling cases yet.</p>`;
    for (const name of names) list.appendChild(this._bucklingCard(m, name));
    sec.appendChild(list);
    return sec;
  }

  _bucklingCard(m, name) {
    const bc = m.buckling_cases[name];
    const card = document.createElement("div");
    card.className = "rs-card buckling-card";

    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renameBucklingCase(m, name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };
    const modesIn = document.createElement("input");
    modesIn.type = "number"; modesIn.step = "1"; modesIn.min = "1"; modesIn.max = "20";
    modesIn.value = String(bc.num_modes);
    modesIn.addEventListener("change", () => {
      const v = parseInt(modesIn.value, 10);
      if (isFinite(v) && v >= 1) { bc.num_modes = v; this._mutated(false); syncNote(); }
      else modesIn.value = String(bc.num_modes);
    });
    head.appendChild(mkField("modes", modesIn));

    head.appendChild(this._delBtn(null, `buckling case ${name}`, () => {
      if (ME.deleteBucklingCase(m, name)) this._mutated();
    }));
    card.appendChild(head);

    // gravity pattern factors (the buckled gravity state)
    const grav = document.createElement("div");
    grav.className = "lc-row po-grav";
    const tag = document.createElement("span");
    tag.className = "mass-tag";
    tag.textContent = "gravity =";
    tag.title = "Gravity state (pattern × factor) that is scaled to buckling";
    grav.appendChild(tag);
    grav.appendChild(this._factorChips(bc.gravity, ME.patternNames(m),
      "Add a gravity pattern to this buckling case"));
    card.appendChild(grav);

    const note = document.createElement("p");
    note.className = "muted staged-note";
    const syncNote = () => {
      note.innerHTML = `Reports the first <b>${bc.num_modes}</b> critical load factor${bc.num_modes === 1 ? "" : "s"} λ. ` +
        `The applied gravity buckles the structure when scaled by λ — <b>λ &lt; 1 is unsafe</b>.`;
    };
    syncNote();
    card.appendChild(note);
    return card;
  }

  /* ============================================================ staged (v0.6) */
  _stagedSection(m) {
    const sec = this._section("ls-staged", "Staged construction",
      "Sequential story-by-story build — each story's gravity is applied on the " +
      "partial structure built so far, then results accumulate. Live load is " +
      "applied at the end on the full structure. Results appear as " +
      "<b>Staged: &lt;name&gt;</b> in the case selector, with a <b>vs one-shot</b> badge.",
      "+ Add staged", () => { ME.addStagedCase(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    const names = Object.keys(m.staged_cases || {});
    if (!names.length) list.innerHTML = `<p class="muted loads-empty">No staged cases yet.</p>`;
    for (const name of names) list.appendChild(this._stagedCard(m, name));
    sec.appendChild(list);
    return sec;
  }

  _stagedCard(m, name) {
    const sc = m.staged_cases[name];
    const card = document.createElement("div");
    card.className = "rs-card staged-card";

    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renameStagedCase(m, name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };

    // gravity pattern to stage (dead-like patterns)
    const patSel = document.createElement("select");
    const pats = ME.patternNames(m);
    patSel.innerHTML = pats.map(p =>
      `<option value="${esc(p)}"${p === sc.pattern ? " selected" : ""}>${esc(p)}</option>`).join("");
    patSel.addEventListener("change", () => { sc.pattern = patSel.value; this._mutated(false); });
    head.appendChild(mkField("gravity pattern", patSel));

    head.appendChild(this._delBtn(null, `staged case ${name}`, () => {
      if (ME.deleteStagedCase(m, name)) this._mutated();
    }));
    card.appendChild(head);

    // include-live factors (applied unstaged at the end)
    const live = document.createElement("div");
    live.className = "lc-row po-grav";
    const tag = document.createElement("span");
    tag.className = "mass-tag";
    tag.textContent = "include live =";
    tag.title = "Live/other patterns applied on the FULL structure at the end, unstaged";
    live.appendChild(tag);
    live.appendChild(this._factorChips(sc.include_live, ME.patternNames(m),
      "Add a live pattern applied at the end"));
    card.appendChild(live);

    const note = document.createElement("p");
    note.className = "muted staged-note";
    note.innerHTML = `Stages <b>per story</b> (bottom → top). Upper stories settle less than a ` +
      `one-shot run — the “slab built level” effect. Every partial structure must be stable on its own.`;
    card.appendChild(note);
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

  /* ============================================================ function library (v0.13)
     Two lists — response-spectrum functions (name + editable (T,Sa) points +
     live spectrum preview, with a Eurocode 8 preset) and time-history
     functions (name + values + dt + sparkline). RS/TH cases reference these
     by name via their "function" dropdown. */
  _functionsSection(m) {
    m.spectrum_functions = m.spectrum_functions || {};
    m.th_functions = m.th_functions || {};
    const sec = this._section("ls-functions", "Function library",
      "Reusable spectrum &amp; ground-motion functions. Reference one from an RS " +
      "or TH case's <b>function</b> dropdown to share the same curve across cases.",
      null, null);

    /* ---- response-spectrum functions ---- */
    const specGroup = document.createElement("div");
    specGroup.className = "fn-group";
    const specHead = document.createElement("div");
    specHead.className = "fn-group-head";
    specHead.innerHTML = `<h4>Response-spectrum functions <span class="muted">Sa (g) vs T (s)</span></h4>`;
    const addSpec = document.createElement("button");
    addSpec.className = "btn btn-small"; addSpec.id = "addSpecFn"; addSpec.textContent = "+ Spectrum function";
    addSpec.addEventListener("click", () => { ME.addSpectrumFunction(m); this._mutated(); });
    specHead.appendChild(addSpec);
    specGroup.appendChild(specHead);
    const specList = document.createElement("div");
    specList.className = "loads-rows";
    const specNames = Object.keys(m.spectrum_functions);
    if (!specNames.length)
      specList.innerHTML = `<p class="muted loads-empty">No spectrum functions yet.</p>`;
    for (const name of specNames) specList.appendChild(this._specFnCard(m, name));
    specGroup.appendChild(specList);
    sec.appendChild(specGroup);

    /* ---- time-history functions ---- */
    const thGroup = document.createElement("div");
    thGroup.className = "fn-group";
    const thHead = document.createElement("div");
    thHead.className = "fn-group-head";
    thHead.innerHTML = `<h4>Time-history functions <span class="muted">ground accel · m/s²</span></h4>`;
    const addTh = document.createElement("button");
    addTh.className = "btn btn-small"; addTh.id = "addThFn"; addTh.textContent = "+ TH function";
    addTh.addEventListener("click", () => { ME.addThFunction(m); this._mutated(); });
    thHead.appendChild(addTh);
    thGroup.appendChild(thHead);
    const thList = document.createElement("div");
    thList.className = "loads-rows";
    const thNames = Object.keys(m.th_functions);
    if (!thNames.length)
      thList.innerHTML = `<p class="muted loads-empty">No time-history functions yet.</p>`;
    for (const name of thNames) thList.appendChild(this._thFnCard(m, name));
    thGroup.appendChild(thList);
    sec.appendChild(thGroup);

    return sec;
  }

  _specFnCard(m, name) {
    const sf = m.spectrum_functions[name];
    const card = document.createElement("div");
    card.className = "rs-card spec-fn-card";

    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renameSpectrumFunction(m, name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };
    const damp = document.createElement("input");
    damp.type = "number"; damp.step = "0.01"; damp.min = "0"; damp.max = "0.5";
    damp.value = String(sf.damping);
    damp.addEventListener("change", () => {
      const v = parseFloat(damp.value);
      if (isFinite(v) && v >= 0 && v < 1) { sf.damping = v; this._mutated(false); }
      else damp.value = String(sf.damping);
    });
    head.appendChild(mkField("damping", damp));

    const refs = ME.spectrumFunctionRefs(m, name);
    head.appendChild(this._delBtn(refs, `spectrum function ${name}`, () => {
      if (ME.deleteSpectrumFunction(m, name)) this._mutated();
    }));
    card.appendChild(head);

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
      title.innerHTML = `Spectrum preview <span class="unit">Sa g vs T s · ζ ${fmt(sf.damping, 3)} · ${(sf.points || []).length} pts</span>`;
      chartWrap.appendChild(title);
      chartWrap.appendChild(spectrumChart(sf.points));
    };
    const rebuildTable = () => {
      tableWrap.textContent = "";
      const head2 = document.createElement("div");
      head2.className = "rs-pt head";
      head2.innerHTML = `<span>T s</span><span>Sa g</span><span></span>`;
      tableWrap.appendChild(head2);
      sf.points.forEach((pt, idx) => {
        const r = document.createElement("div");
        r.className = "rs-pt";
        const mkNum = (col, min) => {
          const i = document.createElement("input");
          i.type = "number"; i.step = col === 0 ? "0.1" : "0.05"; i.min = String(min);
          i.value = String(pt[col]);
          i.addEventListener("change", () => {
            const v = parseFloat(i.value);
            if (isFinite(v) && v >= min) { pt[col] = v; this._mutated(false); drawChart(); }
            else i.value = String(pt[col]);
          });
          return i;
        };
        r.appendChild(mkNum(0, 0));
        r.appendChild(mkNum(1, 0));
        const x = document.createElement("button");
        x.className = "chip-x"; x.textContent = "✕"; x.title = "Remove point";
        x.addEventListener("click", () => {
          sf.points.splice(idx, 1); this._mutated(false); rebuildTable(); drawChart();
        });
        r.appendChild(x);
        tableWrap.appendChild(r);
      });
      const foot = document.createElement("div");
      foot.className = "rs-table-foot";
      const addPt = document.createElement("button");
      addPt.className = "btn btn-small"; addPt.textContent = "+ Point";
      addPt.addEventListener("click", () => {
        const last = sf.points[sf.points.length - 1];
        const T = last ? +(last[0] + 0.5).toFixed(2) : 0;
        const Sa = last ? +(Math.max(0.05, last[1] * 0.8)).toFixed(3) : 0.4;
        sf.points.push([T, Sa]); this._mutated(false); rebuildTable(); drawChart();
      });
      const ec8 = document.createElement("button");
      ec8.className = "btn btn-small ec8-preset"; ec8.textContent = "EC8 preset";
      ec8.title = "Seed a Eurocode 8 (Type 1) elastic response spectrum";
      ec8.addEventListener("click", () => {
        sf.points = ME.ec8Spectrum({ damping: sf.damping });
        this._mutated(false); rebuildTable(); drawChart();
        this.toast("EC8 spectrum seeded", `Eurocode 8 Type 1 curve · ${sf.points.length} points`, "info", 3500);
      });
      const ubc = document.createElement("button");
      ubc.className = "btn btn-small"; ubc.textContent = "UBC default";
      ubc.addEventListener("click", () => {
        sf.points = ME.ubcSpectrum(); this._mutated(false); rebuildTable(); drawChart();
      });
      foot.append(addPt, ec8, ubc);
      tableWrap.appendChild(foot);
    };
    rebuildTable();
    drawChart();
    body.append(tableWrap, chartWrap);
    card.appendChild(body);
    return card;
  }

  _thFnCard(m, name) {
    const tf = m.th_functions[name];
    const card = document.createElement("div");
    card.className = "rs-card th-card th-fn-card";

    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(name, "rs-name",
      nu => ME.renameThFunction(m, name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };
    const dtIn = document.createElement("input");
    dtIn.type = "number"; dtIn.step = "0.005"; dtIn.min = "0.001"; dtIn.value = String(tf.dt);
    dtIn.addEventListener("change", () => {
      const v = parseFloat(dtIn.value);
      if (isFinite(v) && v > 0) { tf.dt = v; this._mutated(false); drawSpark(); }
      else dtIn.value = String(tf.dt);
    });
    head.appendChild(mkField("dt s", dtIn));

    const refs = ME.thFunctionRefs(m, name);
    head.appendChild(this._delBtn(refs, `TH function ${name}`, () => {
      if (ME.deleteThFunction(m, name)) this._mutated();
    }));
    card.appendChild(head);

    const body = document.createElement("div");
    body.className = "th-body";
    const left = document.createElement("div");
    left.className = "th-record";
    const lbl = document.createElement("div");
    lbl.className = "th-label";
    lbl.innerHTML = `Values <span class="unit">m/s² · comma / whitespace separated</span>`;
    const ta = document.createElement("textarea");
    ta.className = "th-accel"; ta.spellcheck = false; ta.rows = 5;
    ta.placeholder = "0, 0.12, 0.31, …";
    const fill = () => { ta.value = tf.values.map(v => +(+v).toFixed(4)).join(", "); };
    fill();
    ta.addEventListener("change", () => {
      const vals = ME.parseAccel(ta.value);
      if (vals === null) {
        this.toast("Record not parsed", "Only numbers, commas and whitespace are allowed", "error", 5000);
        fill(); return;
      }
      tf.values = vals; this._mutated(false); drawSpark();
    });
    const foot = document.createElement("div");
    foot.className = "rs-table-foot";
    const seed = document.createElement("button");
    seed.className = "btn btn-small"; seed.textContent = "Sine demo";
    seed.addEventListener("click", () => {
      tf.values = ME.sineRecord(tf.dt, 8); fill(); this._mutated(false); drawSpark();
    });
    const clear = document.createElement("button");
    clear.className = "btn btn-small"; clear.textContent = "Clear";
    clear.addEventListener("click", () => { tf.values = []; fill(); this._mutated(false); drawSpark(); });
    foot.append(seed, clear);
    left.append(lbl, ta, foot);

    const right = document.createElement("div");
    right.className = "rs-chart th-chart";
    const drawSpark = () => {
      right.textContent = "";
      const title = document.createElement("div");
      title.className = "chart-title";
      title.innerHTML = `Record preview <span class="unit">${tf.values.length} pts · dt ${fmt(tf.dt, 3)} s</span>`;
      right.appendChild(title);
      right.appendChild(thSparkline(tf.values, tf.dt, { width: 320, height: 84 }));
    };
    drawSpark();
    body.append(left, right);
    card.appendChild(body);
    return card;
  }

  /* ============================================================ section cuts (v0.13)
     Manager for cutting planes written to model.section_cuts — name, axis
     (X/Y/Z), coordinate along that axis, and optional in-plane bounding
     ranges. Drawn as translucent planes in the 3D view; resultants land in the
     Section Cut Forces results tab after a solve. */
  _sectionCutsSection(m) {
    m.section_cuts = m.section_cuts || [];
    const sec = this._section("ls-cuts", "Section cuts",
      "Cutting planes that report the internal force resultant (FX/FY/FZ, " +
      "MX/MY/MZ) transmitted across them. Results land in the <b>Section Cuts</b> " +
      "tab after a solve; each plane is drawn in the 3D view.",
      "+ Section cut", () => { ME.addSectionCut(m); this._mutated(); });

    const list = document.createElement("div");
    list.className = "loads-rows";
    if (!m.section_cuts.length)
      list.innerHTML = `<p class="muted loads-empty">No section cuts yet.</p>`;
    for (const cut of m.section_cuts) list.appendChild(this._cutCard(m, cut));
    sec.appendChild(list);
    return sec;
  }

  _cutCard(m, cut) {
    const card = document.createElement("div");
    card.className = "rs-card cut-card";

    const head = document.createElement("div");
    head.className = "rs-head";
    head.appendChild(this._nameInput(cut.name, "rs-name",
      nu => ME.renameSectionCut(m, cut.name, nu)));

    const mkField = (label, node) => {
      const w = document.createElement("label");
      w.className = "rs-field";
      const s = document.createElement("span");
      s.textContent = label;
      w.append(s, node);
      return w;
    };
    const axisSel = document.createElement("select");
    axisSel.innerHTML = `<option value="x">X (Y-Z plane)</option>
      <option value="y">Y (X-Z plane)</option><option value="z">Z (X-Y plane)</option>`;
    axisSel.value = cut.axis;
    head.appendChild(mkField("axis", axisSel));

    const coord = document.createElement("input");
    coord.type = "number"; coord.step = "0.5"; coord.value = String(cut.coord);
    coord.addEventListener("change", () => {
      const v = parseFloat(coord.value);
      if (isFinite(v)) { cut.coord = v; this._mutated(); }
      else coord.value = String(cut.coord);
    });
    head.appendChild(mkField("coord m", coord));

    head.appendChild(this._delBtn(null, `section cut ${cut.name}`, () => {
      if (ME.deleteSectionCut(m, cut.name)) this._mutated();
    }));
    card.appendChild(head);

    // optional in-plane bounding ranges (the two axes NOT equal to `axis`)
    const AX = [["x", 0, "x_range"], ["y", 1, "y_range"], ["z", 2, "z_range"]];
    const rangesRow = document.createElement("div");
    rangesRow.className = "cut-ranges";
    const rebuildRanges = () => {
      rangesRow.textContent = "";
      const tag = document.createElement("span");
      tag.className = "mass-tag"; tag.textContent = "bound to:";
      tag.title = "Optional bounding box that clips the cut plane";
      rangesRow.appendChild(tag);
      for (const [ax, , key] of AX) {
        if (ax === cut.axis) continue;
        const wrap = document.createElement("label");
        wrap.className = "cut-range-field";
        const on = Array.isArray(cut[key]);
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = on; cb.className = "cut-range-cb";
        const lo = document.createElement("input");
        lo.type = "number"; lo.step = "0.5"; lo.className = "cut-range-num";
        lo.placeholder = "lo"; lo.value = on ? String(cut[key][0]) : "";
        const hi = document.createElement("input");
        hi.type = "number"; hi.step = "0.5"; hi.className = "cut-range-num";
        hi.placeholder = "hi"; hi.value = on ? String(cut[key][1]) : "";
        lo.disabled = hi.disabled = !on;
        const label = document.createElement("span");
        label.className = "cut-range-lbl"; label.textContent = ax.toUpperCase();
        const commit = () => {
          const l = parseFloat(lo.value), h = parseFloat(hi.value);
          if (cb.checked && isFinite(l) && isFinite(h)) ME.setCutRange(cut, key, l, h);
          else if (cb.checked) { /* incomplete — leave as-is */ }
          else ME.setCutRange(cut, key, null, null);
          this._mutated(false);
        };
        cb.addEventListener("change", () => {
          if (cb.checked) {
            const [loB, hiB] = ME.modelBBox(m);
            const idx = { x: 0, y: 1, z: 2 }[ax];
            ME.setCutRange(cut, key, +loB[idx].toFixed(2), +hiB[idx].toFixed(2));
          } else ME.setCutRange(cut, key, null, null);
          this._mutated();
        });
        lo.addEventListener("change", commit);
        hi.addEventListener("change", commit);
        wrap.append(cb, label, lo, hi);
        rangesRow.appendChild(wrap);
      }
    };
    axisSel.addEventListener("change", () => {
      cut.axis = axisSel.value;
      // drop the range on the (now out-of-plane) new axis to keep it in-plane
      delete cut[{ x: "x_range", y: "y_range", z: "z_range" }[cut.axis]];
      this._mutated();
    });
    rebuildRanges();
    card.appendChild(rangesRow);

    const note = document.createElement("p");
    note.className = "muted staged-note";
    note.innerHTML = `Plane <b>${cut.axis.toUpperCase()} = ${fmt(cut.coord, 2)} m</b>. ` +
      `The resultant sums the internal forces of members crossing the plane` +
      (Array.isArray(cut.x_range) || Array.isArray(cut.y_range) || Array.isArray(cut.z_range)
        ? ` within the bounding box.` : `.`);
    card.appendChild(note);
    return card;
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
