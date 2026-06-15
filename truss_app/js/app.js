/* ============================================================================
 * app.js — Controller. Wires model + view + solver + explain into the UI,
 * handles the drawing tools, the what-if loop, and all panel rendering.
 * ==========================================================================*/

(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const App = {
    model: new TrussModel(),
    units: new Units('SI'),
    view: null,
    result: null,
    tool: 'select',
    sel: { type: null, index: -1 },
    // transient interaction state
    drag: null, // { index, moved }
    memberStart: -1,

    init() {
      this.view = new TrussView($('canvas'));
      this.populateExamples();
      this.populateSections();
      this.bindToolbar();
      this.bindCanvas();
      this.bindPanels();
      this.bindGlobal();
      window.addEventListener('resize', () => this.render());
      // Start with an instructive preset.
      this.loadExample('pratt');
    },

    /* ------------------------------------------------------------------ *
     * Population of dropdowns
     * ------------------------------------------------------------------ */
    populateExamples() {
      const sel = $('exampleSelect');
      for (const key in TRUSS_EXAMPLES) {
        const o = document.createElement('option');
        o.value = key; o.textContent = TRUSS_EXAMPLES[key].name;
        sel.appendChild(o);
      }
      sel.addEventListener('change', () => {
        if (sel.value) this.loadExample(sel.value);
      });
    },

    populateSections() {
      const sel = $('mpSection');
      // wipe all but the first "custom" option
      sel.querySelectorAll('optgroup').forEach((g) => g.remove());
      for (const grp of AISC_SECTIONS) {
        const og = document.createElement('optgroup');
        og.label = grp.group;
        for (const it of grp.items) {
          const o = document.createElement('option');
          o.value = it.name;
          o.textContent = `${it.name}  (A=${it.areaIn2} in², ${it.weight} lb/ft)`;
          og.appendChild(o);
        }
        sel.appendChild(og);
      }
    },

    /* ------------------------------------------------------------------ *
     * Tools
     * ------------------------------------------------------------------ */
    bindToolbar() {
      $('toolrail').querySelectorAll('.tool').forEach((b) => {
        b.addEventListener('click', () => this.setTool(b.dataset.tool));
      });
    },

    setTool(tool) {
      this.tool = tool;
      this.memberStart = -1;
      $('toolrail').querySelectorAll('.tool').forEach((b) =>
        b.classList.toggle('active', b.dataset.tool === tool));
      const hints = {
        select: 'Drag joints to move them. Click a member or joint to edit it.',
        node: 'Click anywhere on the grid to drop a joint (snaps to grid).',
        member: 'Click one joint, then another, to connect them.',
        support: 'Click a joint to cycle: none → pin → roller(Y) → roller(X).',
        load: 'Click a joint, then set the load components in Properties.',
        delete: 'Click a joint or member to delete it.',
      };
      $('toolHint').innerHTML = hints[tool] || '';
      $('canvas').style.cursor = tool === 'select' ? 'default' : 'crosshair';
    },

    /* ------------------------------------------------------------------ *
     * Canvas interaction
     * ------------------------------------------------------------------ */
    bindCanvas() {
      const svg = $('canvas');
      svg.addEventListener('pointerdown', (e) => this.onPointerDown(e));
      svg.addEventListener('pointermove', (e) => this.onPointerMove(e));
      svg.addEventListener('pointerup', (e) => this.onPointerUp(e));
      svg.addEventListener('pointerleave', () => { this.drag = null; });
    },

    localXY(e) {
      const r = $('canvas').getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    },

    snapWorld(px, py) {
      const [wx, wy] = this.view.toWorld(px, py);
      const g = this.units.gridBase;
      return [Math.round(wx / g) * g, Math.round(wy / g) * g];
    },

    onPointerDown(e) {
      const [px, py] = this.localXY(e);
      const nodeHit = this.view.nodeIndexNear(px, py);
      const memberHit = nodeHit < 0 ? this.view.memberIndexNear(px, py) : -1;

      switch (this.tool) {
        case 'node': {
          const [wx, wy] = this.snapWorld(px, py);
          this.model.commit();
          const idx = this.model.addNode(wx, wy);
          this.select('node', idx);
          this.recompute();
          break;
        }
        case 'member': {
          if (nodeHit >= 0) {
            if (this.memberStart < 0) {
              this.memberStart = nodeHit;
              this.setStatus(`Member start at Joint ${nodeHit + 1} — click the far joint.`);
            } else if (nodeHit !== this.memberStart) {
              this.model.commit();
              const mi = this.model.addMember(this.memberStart, nodeHit,
                this.units.defaultE, this.units.defaultA);
              this.memberStart = -1;
              this.select('member', mi);
              this.recompute();
            }
          }
          break;
        }
        case 'support': {
          if (nodeHit >= 0) {
            this.model.commit();
            const order = ['none', 'pin', 'roller-x', 'roller-y'];
            const cur = this.model.supportKind(nodeHit);
            const next = order[(order.indexOf(cur) + 1) % order.length];
            this.model.setSupport(nodeHit, next);
            this.select('node', nodeHit);
            this.recompute();
          }
          break;
        }
        case 'load': {
          if (nodeHit >= 0) {
            this.select('node', nodeHit);
            this.switchTab('properties');
            $('npFx').focus();
          }
          break;
        }
        case 'delete': {
          if (nodeHit >= 0) {
            this.model.commit(); this.model.deleteNode(nodeHit);
            this.clearSelection(); this.recompute();
          } else if (memberHit >= 0) {
            this.model.commit(); this.model.deleteMember(memberHit);
            this.clearSelection(); this.recompute();
          }
          break;
        }
        case 'select':
        default: {
          if (nodeHit >= 0) {
            this.select('node', nodeHit);
            this.drag = { index: nodeHit, moved: false };
            $('canvas').setPointerCapture(e.pointerId);
          } else if (memberHit >= 0) {
            this.select('member', memberHit);
          } else {
            this.clearSelection();
          }
          break;
        }
      }
    },

    onPointerMove(e) {
      const [px, py] = this.localXY(e);
      if (this.drag) {
        const [wx, wy] = this.snapWorld(px, py);
        const n = this.model.nodes[this.drag.index];
        if (n.x !== wx || n.y !== wy) {
          if (!this.drag.moved) { this.model.commit(); this.drag.moved = true; }
          this.model.moveNode(this.drag.index, wx, wy);
          this.render(); // cheap redraw while dragging
        }
        return;
      }
      // Hover status with world coords.
      const [wx, wy] = this.view.toWorld(px, py);
      this.setStatus(`x=${this.units.len(wx)}  y=${this.units.len(wy)}`);
    },

    onPointerUp(e) {
      if (this.drag) {
        const moved = this.drag.moved;
        this.drag = null;
        try { $('canvas').releasePointerCapture(e.pointerId); } catch (_) {}
        if (moved) { this.refreshNodeProps(); this.recompute(); }
      }
    },

    /* ------------------------------------------------------------------ *
     * Selection
     * ------------------------------------------------------------------ */
    select(type, index) {
      this.sel = { type, index };
      this.switchTab('properties');
      this.renderProps();
      this.highlightSelection();
    },
    clearSelection() {
      this.sel = { type: null, index: -1 };
      this.renderProps();
      this.highlightSelection();
    },
    highlightSelection() {
      // member spotlight handled in view via opts; node highlight via class.
      this.view.opts.highlightMember = this.sel.type === 'member' ? this.sel.index : null;
      this.render();
      if (this.sel.type === 'node') {
        const c = $('canvas').querySelector(`circle[data-node="${this.sel.index}"]`);
        if (c) c.classList.add('selected');
      }
    },

    /* ------------------------------------------------------------------ *
     * Solve + render
     * ------------------------------------------------------------------ */
    recompute() {
      this.result = TrussSolver.analyze({
        nodes: this.model.nodes,
        members: this.model.members,
        supports: this.model.supports,
        loads: this.model.loads,
      });
      this.render();
      this.renderInsights();
      this.renderResultsTables();
      this.renderProps();
      $('undoBtn').disabled = !this.model.canUndo();
      $('redoBtn').disabled = !this.model.canRedo();
    },

    render() {
      this.view.render(this.model, this.result);
      // re-apply node selection class after redraw
      if (this.sel.type === 'node') {
        const c = $('canvas').querySelector(`circle[data-node="${this.sel.index}"]`);
        if (c) c.classList.add('selected');
      }
    },

    /* ------------------------------------------------------------------ *
     * Insights panel (summary + ranking + cards)
     * ------------------------------------------------------------------ */
    renderInsights() {
      const U = this.units;
      const r = this.result;
      const strip = $('summaryStrip');
      const rankBox = $('rankingBox');
      const cardBox = $('insightCards');

      if (!r || !r.ok) {
        const msg = (r && r.messages.join(' ')) || 'Draw a truss to begin.';
        strip.innerHTML = `<div class="stat full"><div class="k">Status</div><div class="v" style="font-size:14px">${msg}</div></div>`;
        rankBox.innerHTML = '';
        const ex = TrussExplain.explain(this.model, r, U);
        cardBox.innerHTML = ex.cards.map((c) => this.cardHtml(c)).join('');
        return;
      }

      const det = r.determinacy;
      const maxAbs = r.members.reduce((a, m) => Math.max(a, Math.abs(m.N)), 0);
      const maxMember = r.members.find((m) => Math.abs(m.N) === maxAbs);
      const vw = r.virtualWork;
      const maxDefl = vw ? Math.abs(vw.total) : Math.max(...r.displacements.map((d) => d.mag), 0);

      strip.innerHTML = `
        <div class="stat"><div class="k">Stability</div><div class="v"><span class="badge ${det.verdict}">${det.verdict}${det.verdict === 'indeterminate' ? ' °' + det.degree : ''}</span></div></div>
        <div class="stat"><div class="k">Max member force</div><div class="v">${U.force(maxAbs)}</div></div>
        <div class="stat"><div class="k">Max joint deflection</div><div class="v">${U.defl(maxDefl)}</div></div>
        <div class="stat"><div class="k">Members / Joints</div><div class="v">${det.m} / ${det.j}</div></div>
      `;
      void maxMember;

      // Ranking: contribution to deflection (preferred) else by force.
      if (vw && vw.ranked.length) {
        const max = Math.abs(vw.ranked[0].contribution) || 1;
        const rows = vw.ranked.slice(0, 8).map((c) => {
          const w = Math.max(2, 100 * Math.abs(c.contribution) / max);
          return `<div class="bar-row" data-member="${c.index}">
            <span class="bar-label">Member ${c.index + 1}</span>
            <span class="bar-track"><span class="bar-fill" style="width:${w}%"></span></span>
            <span class="bar-val">${(100 * Math.abs(c.contribution) / Math.abs(vw.total || 1)).toFixed(0)}%</span>
          </div>`;
        }).join('');
        rankBox.innerHTML = `<h3>Who controls the deflection of Joint ${vw.targetNode + 1}?</h3>${rows}
          <div class="cite" style="font-size:11px;color:var(--ink-dim);margin-top:4px">Share of total deflection via N·n·L/(EA). Hover a bar to spotlight the member.</div>`;
        rankBox.querySelectorAll('.bar-row').forEach((row) => {
          row.addEventListener('mouseenter', () => {
            this.view.opts.highlightMember = +row.dataset.member; this.render();
          });
          row.addEventListener('mouseleave', () => {
            this.view.opts.highlightMember = this.sel.type === 'member' ? this.sel.index : null;
            this.render();
          });
          row.addEventListener('click', () => this.select('member', +row.dataset.member));
        });
      } else {
        rankBox.innerHTML = '';
      }

      const ex = TrussExplain.explain(this.model, r, U);
      cardBox.innerHTML = ex.cards.map((c) => this.cardHtml(c)).join('');
    },

    cardHtml(c) {
      return `<div class="card ${c.type}"><h4>${c.title}</h4><div class="body">${c.html}</div></div>`;
    },

    /* ------------------------------------------------------------------ *
     * Results tables
     * ------------------------------------------------------------------ */
    renderResultsTables() {
      const U = this.units;
      const r = this.result;
      const box = $('resultsTables');
      if (!r || !r.ok) { box.innerHTML = '<p class="prop-empty">No results yet.</p>'; return; }

      const memRows = r.members.map((m) => `
        <tr data-member="${m.index}">
          <td>M${m.index + 1}</td>
          <td>${U.len(m.L)}</td>
          <td class="${m.state}">${U.force(m.N)}</td>
          <td class="${m.state}">${m.state[0].toUpperCase()}</td>
          <td>${U.area(m.A)}</td>
        </tr>`).join('');

      const vw = r.virtualWork;
      let vwTable = '';
      if (vw) {
        const rows = vw.contributions.map((c) => `
          <tr data-member="${c.index}">
            <td>M${c.index + 1}</td>
            <td>${U.force(c.N)}</td>
            <td>${fmtNum(c.n, 3)}</td>
            <td>${U.defl(c.contribution)}</td>
            <td>${(100 * c.contribution / (vw.total || 1)).toFixed(1)}%</td>
          </tr>`).join('');
        vwTable = `
          <table class="res-table">
            <caption>Virtual-work table — deflection of Joint ${vw.targetNode + 1} (δ = Σ N·n·L/EA = ${U.defl(vw.total)})</caption>
            <thead><tr><th>Member</th><th>N (real)</th><th>n (unit)</th><th>δ contrib</th><th>%</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
      }

      const reacRows = r.reactions
        .filter((x) => x.rx != null || x.ry != null)
        .map((x) => `<tr><td>Joint ${x.node + 1}</td><td>${x.rx != null ? U.force(x.rx) : '—'}</td><td>${x.ry != null ? U.force(x.ry) : '—'}</td></tr>`).join('');

      box.innerHTML = `
        <table class="res-table">
          <caption>Member forces (+ tension / − compression)</caption>
          <thead><tr><th>Member</th><th>Length</th><th>Force</th><th>State</th><th>Area</th></tr></thead>
          <tbody>${memRows}</tbody>
        </table>
        <table class="res-table">
          <caption>Support reactions</caption>
          <thead><tr><th>Joint</th><th>Rx</th><th>Ry</th></tr></thead>
          <tbody>${reacRows || '<tr><td colspan="3">none</td></tr>'}</tbody>
        </table>
        ${vwTable}`;

      box.querySelectorAll('tr[data-member]').forEach((tr) => {
        tr.addEventListener('click', () => this.select('member', +tr.dataset.member));
      });
    },

    /* ------------------------------------------------------------------ *
     * Properties editor
     * ------------------------------------------------------------------ */
    renderProps() {
      const node = this.sel.type === 'node';
      const member = this.sel.type === 'member';
      $('propEmpty').hidden = node || member;
      $('nodeProps').hidden = !node;
      $('memberProps').hidden = !member;
      if (node) this.refreshNodeProps();
      if (member) this.refreshMemberProps();
    },

    refreshNodeProps() {
      if (this.sel.type !== 'node') return;
      const U = this.units;
      const i = this.sel.index;
      const n = this.model.nodes[i];
      if (!n) { this.clearSelection(); return; }
      $('npId').textContent = i + 1;
      $('npX').value = (+U.lenFromBase(n.x).toFixed(3));
      $('npY').value = (+U.lenFromBase(n.y).toFixed(3));
      $('npSupport').value = this.model.supportKind(i);
      const ld = this.model.loadFor(i);
      $('npFx').value = ld ? +U.forceFromBase(ld.fx).toFixed(2) : 0;
      $('npFy').value = ld ? +U.forceFromBase(ld.fy).toFixed(2) : 0;
      $('npForceUnit').textContent = U.forceUnit;
      $('npForceUnit2').textContent = U.forceUnit;
    },

    refreshMemberProps() {
      if (this.sel.type !== 'member') return;
      const U = this.units;
      const i = this.sel.index;
      const m = this.model.members[i];
      if (!m) { this.clearSelection(); return; }
      $('mpId').textContent = i + 1;
      $('mpSection').value = m.section || '';
      $('mpArea').value = +U.areaFromBase(m.A).toFixed(3);
      $('mpE').value = +U.eFromBase(m.E).toFixed(1);
      $('mpAreaUnit').textContent = `(${U.areaUnit})`;
      $('mpEUnit').textContent = `(${U.eUnit})`;
      let readout = '';
      if (this.result && this.result.ok && this.result.members[i]) {
        const mr = this.result.members[i];
        readout += `Force: <b class="${mr.state}">${U.force(mr.N)}</b> (${mr.state})<br>`;
        readout += `Length: <b>${U.len(mr.L)}</b> &nbsp; Stress: <b>${U.stress(mr.stress)}</b><br>`;
        const vw = this.result.virtualWork;
        if (vw) {
          const c = vw.contributions[i];
          if (c) {
            const share = 100 * c.contribution / (vw.total || 1);
            readout += `Virtual force n: <b>${fmtNum(c.n, 3)}</b><br>`;
            readout += `Deflection contribution: <b>${U.defl(c.contribution)}</b> (${share.toFixed(1)}% of Joint ${vw.targetNode + 1})`;
          }
        }
      } else {
        readout = 'Solve the truss to see this member’s force and deflection contribution.';
      }
      $('mpReadout').innerHTML = readout;
    },

    bindPanels() {
      // tabs
      document.querySelectorAll('.tab').forEach((t) =>
        t.addEventListener('click', () => this.switchTab(t.dataset.tab)));

      // node prop edits
      const commitNode = () => {
        if (this.sel.type !== 'node') return;
        const U = this.units, i = this.sel.index;
        this.model.commit();
        this.model.moveNode(i, U.lenToBase(+$('npX').value || 0), U.lenToBase(+$('npY').value || 0));
        this.model.setSupport(i, $('npSupport').value);
        this.model.setLoad(i, U.forceToBase(+$('npFx').value || 0), U.forceToBase(+$('npFy').value || 0));
        this.recompute();
      };
      ['npX', 'npY', 'npSupport', 'npFx', 'npFy'].forEach((id) =>
        $(id).addEventListener('change', commitNode));
      $('npDelete').addEventListener('click', () => {
        this.model.commit(); this.model.deleteNode(this.sel.index);
        this.clearSelection(); this.recompute();
      });

      // member prop edits
      $('mpSection').addEventListener('change', () => {
        if (this.sel.type !== 'member') return;
        const name = $('mpSection').value;
        this.model.commit();
        const m = this.model.members[this.sel.index];
        if (name && AISC_BY_NAME[name]) {
          m.section = name;
          m.A = this.units.aiscAreaToBase(AISC_BY_NAME[name].areaIn2);
          // AISC shapes are steel: pin E at 29,000 ksi (in base kN/m^2).
          m.E = 29000 * 6894.757293;
        } else {
          m.section = null;
        }
        this.recompute();
      });
      const commitMember = () => {
        if (this.sel.type !== 'member') return;
        const U = this.units, m = this.model.members[this.sel.index];
        this.model.commit();
        m.A = Math.max(1e-9, U.areaToBase(+$('mpArea').value || 0));
        m.E = Math.max(1e-3, U.eToBase(+$('mpE').value || 0));
        m.section = null; // manual override
        $('mpSection').value = '';
        this.recompute();
      };
      ['mpArea', 'mpE'].forEach((id) => $(id).addEventListener('change', commitMember));
      $('mpDelete').addEventListener('click', () => {
        this.model.commit(); this.model.deleteMember(this.sel.index);
        this.clearSelection(); this.recompute();
      });

      // display toggles
      const sync = () => {
        this.view.opts.showForces = $('tgForces').checked;
        this.view.opts.showDeflection = $('tgDefl').checked;
        this.view.opts.showLabels = $('tgLabels').checked;
        this.view.opts.showGrid = $('tgGrid').checked;
        this.view.opts.userDeflScale = +$('deflScale').value;
        this.render();
      };
      ['tgForces', 'tgDefl', 'tgLabels', 'tgGrid', 'deflScale'].forEach((id) =>
        $(id).addEventListener('input', sync));
    },

    switchTab(name) {
      document.querySelectorAll('.tab').forEach((t) =>
        t.classList.toggle('active', t.dataset.tab === name));
      document.querySelectorAll('.tab-pane').forEach((p) =>
        p.classList.toggle('active', p.dataset.pane === name));
    },

    /* ------------------------------------------------------------------ *
     * Global controls
     * ------------------------------------------------------------------ */
    bindGlobal() {
      $('unitSelect').addEventListener('change', () => {
        this.units.set($('unitSelect').value);
        this.recompute();
      });
      $('undoBtn').addEventListener('click', () => { if (this.model.undo()) { this.clearSelection(); this.recompute(); } });
      $('redoBtn').addEventListener('click', () => { if (this.model.redo()) { this.clearSelection(); this.recompute(); } });
      $('clearBtn').addEventListener('click', () => {
        if (!confirm('Clear the whole truss?')) return;
        this.model.commit(); this.model.clear(); this.clearSelection(); this.recompute();
      });
      $('saveBtn').addEventListener('click', () => this.saveModel());
      $('loadBtn').addEventListener('click', () => $('fileInput').click());
      $('fileInput').addEventListener('change', (e) => this.loadFile(e));
      $('helpBtn').addEventListener('click', () => { $('helpModal').hidden = false; });
      $('helpClose').addEventListener('click', () => { $('helpModal').hidden = true; });
      $('helpModal').addEventListener('click', (e) => { if (e.target === $('helpModal')) $('helpModal').hidden = true; });

      document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); if (this.model.undo()) { this.clearSelection(); this.recompute(); } }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); if (this.model.redo()) { this.clearSelection(); this.recompute(); } }
        else if (e.key === 'Delete' || e.key === 'Backspace') {
          if (this.sel.type === 'node') { this.model.commit(); this.model.deleteNode(this.sel.index); this.clearSelection(); this.recompute(); }
          else if (this.sel.type === 'member') { this.model.commit(); this.model.deleteMember(this.sel.index); this.clearSelection(); this.recompute(); }
        }
        const keys = { s: 'select', n: 'node', m: 'member', r: 'support', l: 'load', d: 'delete' };
        if (keys[e.key]) this.setTool(keys[e.key]);
      });
    },

    saveModel() {
      const data = JSON.stringify(this.model.toJSON(), null, 2);
      const blob = new Blob([data], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'truss.json';
      a.click();
      URL.revokeObjectURL(a.href);
    },

    loadFile(e) {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const obj = JSON.parse(reader.result);
          this.model.commit();
          this.model.loadFrom(obj);
          this.clearSelection();
          this.view.fit(this.model);
          this.recompute();
        } catch (err) { alert('Could not read that file: ' + err.message); }
      };
      reader.readAsText(file);
      e.target.value = '';
    },

    loadExample(key) {
      const ex = TRUSS_EXAMPLES[key];
      if (!ex) return;
      this.model.commit();
      this.model.loadFrom(ex.build(this.units));
      this.clearSelection();
      this.view.fit(this.model);
      this.recompute();
      this.setStatus(ex.blurb);
      $('exampleSelect').value = key;
    },

    setStatus(text) { $('statusBar').innerHTML = text; },
  };

  window.addEventListener('DOMContentLoaded', () => App.init());
  window.TrussApp = App;
})();
