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
    pan: null, // { x, y, moved } — dragging empty space with Select
    memberStart: -1,
    // where the virtual unit load goes: node -1 = most-displaced joint
    vwTarget: { node: -1, mode: 'motion' },

    init() {
      this.view = new TrussView($('canvas'));
      this.view.units = this.units; // same instance: unit switches propagate
      this.view.gridStep = this.units.gridBase;
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
        // Snap back to the placeholder so the same preset can be picked again
        // (re-selecting the current option would fire no change event).
        sel.value = '';
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
      this.cancelMemberStart();
      $('toolrail').querySelectorAll('.tool').forEach((b) =>
        b.classList.toggle('active', b.dataset.tool === tool));
      const hints = {
        select: 'Drag joints to move them (drop one on another to join). Click a member or joint to edit it. Drag empty space to pan, scroll to zoom.',
        node: 'Click anywhere on the grid to drop a joint (snaps to grid).',
        member: 'Click one joint, then another, to connect them. Clicking empty grid makes a new joint there. Esc cancels.',
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
      // Leaving the canvas mid-drag must still finish the drag (re-solve).
      svg.addEventListener('pointerleave', (e) => this.onPointerUp(e));
      svg.addEventListener('pointercancel', (e) => this.onPointerUp(e));
      svg.addEventListener('wheel', (e) => {
        e.preventDefault();
        const [px, py] = this.localXY(e);
        this.view.zoomAt(px, py, Math.exp(-e.deltaY * 0.0015));
        this.render();
      }, { passive: false });
    },

    capture(e) {
      // Pointer capture keeps a drag alive outside the canvas. Not every
      // environment implements it, so never let it break the interaction.
      try { $('canvas').setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    },

    cancelMemberStart() {
      this.memberStart = -1;
      if (this.view && this.view.opts.pendingNode !== null) {
        this.view.opts.pendingNode = null;
        this.render();
      }
    },

    fitView() {
      this.view.fit(this.model);
      this.render();
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
          this.select('node', idx, { showProps: false });
          this.recompute();
          break;
        }
        case 'member': {
          let target = nodeHit;
          let committed = false;
          if (target < 0) {
            // Empty grid: drop a joint there so members can be drawn in one go.
            const [wx, wy] = this.snapWorld(px, py);
            this.model.commit();
            committed = true;
            target = this.model.addNode(wx, wy);
          }
          if (this.memberStart < 0) {
            this.memberStart = target;
            this.view.opts.pendingNode = target;
            this.setStatus(`Member starts at Joint ${target + 1} — now click the far joint (Esc to cancel).`);
            if (committed) this.recompute(); else this.render();
          } else if (target !== this.memberStart) {
            if (!committed) this.model.commit();
            const mi = this.model.addMember(this.memberStart, target,
              this.units.defaultE, this.units.defaultA);
            // Chain: the far joint becomes the start of the next member.
            this.memberStart = target;
            this.view.opts.pendingNode = target;
            this.select('member', mi, { showProps: false });
            this.setStatus(`Member ${mi + 1} added. Keep clicking to chain members, or Esc to stop.`);
            this.recompute();
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
            this.capture(e);
          } else if (memberHit >= 0) {
            this.select('member', memberHit);
          } else {
            // Empty space: start a pan; a plain click (no move) deselects.
            this.pan = { x: px, y: py, moved: false };
            this.capture(e);
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
        if (n && (n.x !== wx || n.y !== wy)) {
          if (!this.drag.moved) { this.model.commit(); this.drag.moved = true; }
          this.model.moveNode(this.drag.index, wx, wy);
          // Live re-solve: forces & insights update as the joint moves
          // (immediate feedback is what makes cause -> effect visible).
          this.recompute();
        }
        return;
      }
      if (this.pan) {
        const dx = px - this.pan.x, dy = py - this.pan.y;
        if (this.pan.moved || Math.hypot(dx, dy) > 3) {
          this.pan.moved = true;
          this.view.panBy(dx, dy);
          this.pan.x = px; this.pan.y = py;
          this.render();
        }
        return;
      }
      // Hover status with world coords.
      const [wx, wy] = this.view.toWorld(px, py);
      this.setStatus(`x=${this.units.len(wx)}  y=${this.units.len(wy)}`);
    },

    onPointerUp(e) {
      if (this.drag) {
        const { index, moved } = this.drag;
        this.drag = null;
        try { $('canvas').releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ }
        if (moved) {
          // Dropped onto another joint? Join them instead of leaving a
          // zero-length member behind. (Already inside the drag's undo step.)
          const n = this.model.nodes[index];
          const other = this.model.nodes.findIndex(
            (m, k) => k !== index && Math.hypot(m.x - n.x, m.y - n.y) < 1e-9);
          if (other >= 0) {
            const kept = this.model.mergeNode(index, other);
            this.select('node', kept);
            this.setStatus(`Joined into Joint ${kept + 1}.`);
          }
          this.recompute();
        }
      }
      if (this.pan) {
        const moved = this.pan.moved;
        this.pan = null;
        try { $('canvas').releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ }
        if (!moved && e.type === 'pointerup') this.clearSelection();
      }
    },

    /* ------------------------------------------------------------------ *
     * Selection
     * ------------------------------------------------------------------ */
    // showProps: jump to the Properties tab (true for deliberate selections;
    // false while drawing, so the Insights you're watching stay on screen).
    select(type, index, { showProps = true } = {}) {
      this.sel = { type, index };
      if (showProps) this.switchTab('properties');
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
      // A chosen joint that no longer exists (deleted/undone) falls back to auto.
      if (this.vwTarget.node >= this.model.nodes.length) this.vwTarget.node = -1;
      this.result = TrussSolver.analyze({
        nodes: this.model.nodes,
        members: this.model.members,
        supports: this.model.supports,
        loads: this.model.loads,
      }, { target: this.vwTarget });
      const vw = this.result.ok ? this.result.virtualWork : null;
      this.view.opts.probe = vw ? { node: vw.targetNode, dir: vw.dir } : null;
      this.syncTargetPicker();
      this.render();
      this.renderInsights();
      this.renderResultsTables();
      this.renderProps();
      $('undoBtn').disabled = !this.model.canUndo();
      $('redoBtn').disabled = !this.model.canRedo();
    },

    // Keep the "Deflection of" joint list in step with the model.
    syncTargetPicker() {
      const sel = $('vwNode');
      const want = this.model.nodes.length + 1;
      if (sel.options.length !== want) {
        while (sel.options.length > 1) sel.remove(1);
        for (let k = 0; k < this.model.nodes.length; k++) {
          const o = document.createElement('option');
          o.value = String(k); o.textContent = `Joint ${k + 1}`;
          sel.appendChild(o);
        }
      }
      sel.value = String(this.vwTarget.node);
      $('vwDir').value = this.vwTarget.mode;
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
      // Largest movement of any joint (independent of the chosen probe joint).
      const maxDefl = Math.max(0, ...r.displacements.map((d) => d.mag));

      strip.innerHTML = `
        <div class="stat"><div class="k">Stability</div><div class="v"><span class="badge ${det.verdict}">${det.verdict}${det.verdict === 'indeterminate' ? ' °' + det.degree : ''}</span></div></div>
        <div class="stat"><div class="k">Max member force</div><div class="v">${U.force(maxAbs)}</div></div>
        <div class="stat"><div class="k">Max joint deflection</div><div class="v">${U.defl(maxDefl)}</div></div>
        <div class="stat"><div class="k">Members / Joints</div><div class="v">${det.m} / ${det.j}</div></div>
      `;
      void maxMember;

      // Ranking: contribution to deflection (preferred) else by force.
      const vwUsable = vw && vw.ranked.length && !vw.restrained && Math.abs(vw.total) > 0;
      if (vwUsable) {
        const max = Math.abs(vw.ranked[0].contribution) || 1;
        const rows = vw.ranked.slice(0, 8).map((c) => {
          const w = Math.max(2, 100 * Math.abs(c.contribution) / max);
          const share = 100 * c.contribution / vw.total; // signed: <0 = works against
          const neg = share < -0.05;
          return `<div class="bar-row${neg ? ' negative' : ''}" data-member="${c.index}"
              title="${neg ? 'Negative: this member moves the joint the other way' : 'Adds to the deflection'}">
            <span class="bar-label">Member ${c.index + 1}</span>
            <span class="bar-track"><span class="bar-fill" style="width:${w}%"></span></span>
            <span class="bar-val">${share.toFixed(0)}%</span>
          </div>`;
        }).join('');
        const hasNeg = vw.contributions.some((c) => 100 * c.contribution / vw.total < -0.05);
        rankBox.innerHTML = `<h3>Who controls the deflection of Joint ${vw.targetNode + 1}?</h3>${rows}
          <div class="cite" style="font-size:11px;color:var(--ink-dim);margin-top:4px">Share of δ = ${U.defl(vw.total)} from each member's N·n·L/(EA).${hasNeg ? ' Striped bars are negative (they work against the deflection).' : ''} Hover a bar to spotlight the member; click to edit it.</div>`;
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
      ['tgForces', 'tgDefl', 'tgLabels', 'tgGrid', 'deflScale'].forEach((id) => {
        $(id).addEventListener('input', sync);
        $(id).addEventListener('change', sync); // older browsers fire only change for checkboxes
      });
      $('fitBtn').addEventListener('click', () => this.fitView());

      // Which joint/direction the virtual unit load probes.
      $('vwNode').addEventListener('change', () => {
        this.vwTarget.node = parseInt($('vwNode').value, 10);
        this.recompute();
      });
      $('vwDir').addEventListener('change', () => {
        this.vwTarget.mode = $('vwDir').value;
        this.recompute();
      });
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
        this.view.gridStep = this.units.gridBase; // grid follows snap spacing
        this.recompute();
        this.setStatus(`Units: ${this.units.sys.label}. Grid & snapping now every 1 ${this.units.lenUnit}.`);
      });
      $('undoBtn').addEventListener('click', () => this.undo());
      $('redoBtn').addEventListener('click', () => this.redo());
      $('clearBtn').addEventListener('click', () => {
        if (!confirm('Clear the whole truss?')) return;
        this.cancelMemberStart();
        this.model.commit(); this.model.clear(); this.clearSelection(); this.recompute();
      });
      $('saveBtn').addEventListener('click', () => this.saveModel());
      $('loadBtn').addEventListener('click', () => $('fileInput').click());
      $('fileInput').addEventListener('change', (e) => this.loadFile(e));
      $('helpBtn').addEventListener('click', () => { $('helpModal').hidden = false; });
      $('helpClose').addEventListener('click', () => { $('helpModal').hidden = true; });
      $('helpModal').addEventListener('click', (e) => { if (e.target === $('helpModal')) $('helpModal').hidden = true; });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          if (!$('helpModal').hidden) { $('helpModal').hidden = true; return; }
          this.cancelMemberStart();
          this.setStatus('Cancelled.');
          return;
        }
        if (!$('helpModal').hidden) return; // don't edit the model behind the help screen
        const tag = e.target.tagName;
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
        const mod = e.ctrlKey || e.metaKey;
        const k = e.key.toLowerCase();
        if (mod && k === 'z' && !e.shiftKey) { e.preventDefault(); this.undo(); return; }
        if (mod && (k === 'y' || (k === 'z' && e.shiftKey))) { e.preventDefault(); this.redo(); return; }
        if (mod || e.altKey) return; // leave browser shortcuts (Ctrl+S, Ctrl+R…) alone
        if (e.key === 'Delete' || e.key === 'Backspace') {
          e.preventDefault();
          if (this.sel.type === 'node') { this.model.commit(); this.model.deleteNode(this.sel.index); this.clearSelection(); this.recompute(); }
          else if (this.sel.type === 'member') { this.model.commit(); this.model.deleteMember(this.sel.index); this.clearSelection(); this.recompute(); }
          return;
        }
        if (k === 'f') { this.fitView(); return; }
        const keys = { s: 'select', n: 'node', m: 'member', r: 'support', l: 'load', d: 'delete' };
        if (keys[k]) this.setTool(keys[k]);
      });
    },

    undo() {
      this.cancelMemberStart();
      if (this.model.undo()) { this.clearSelection(); this.recompute(); }
    },
    redo() {
      this.cancelMemberStart();
      if (this.model.redo()) { this.clearSelection(); this.recompute(); }
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
          this.validateModelJSON(obj); // throws a readable message if bad
          this.cancelMemberStart();
          this.vwTarget = { node: -1, mode: 'motion' };
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

    // Check a loaded file before replacing the current truss with it.
    validateModelJSON(obj) {
      const fail = (msg) => { throw new Error(msg); };
      if (!obj || typeof obj !== 'object') fail('it is not a Truss Lab model.');
      const { nodes, members = [], supports = [], loads = [] } = obj;
      if (!Array.isArray(nodes)) fail('it has no "nodes" list.');
      if (![members, supports, loads].every(Array.isArray)) fail('members/supports/loads must be lists.');
      const nOk = (i) => Number.isInteger(i) && i >= 0 && i < nodes.length;
      nodes.forEach((n, k) => {
        if (!n || !Number.isFinite(+n.x) || !Number.isFinite(+n.y)) fail(`joint ${k + 1} has no valid x/y.`);
      });
      members.forEach((m, k) => {
        if (!m || !nOk(m.i) || !nOk(m.j)) fail(`member ${k + 1} points at a joint that does not exist.`);
      });
      supports.forEach((s, k) => { if (!s || !nOk(s.node)) fail(`support ${k + 1} is on a missing joint.`); });
      loads.forEach((l, k) => { if (!l || !nOk(l.node)) fail(`load ${k + 1} is on a missing joint.`); });
    },

    loadExample(key) {
      const ex = TRUSS_EXAMPLES[key];
      if (!ex) return;
      this.cancelMemberStart();
      this.vwTarget = { node: -1, mode: 'motion' }; // new truss: back to auto target
      this.model.commit();
      this.model.loadFrom(ex.build(this.units));
      this.clearSelection();
      this.view.fit(this.model);
      this.recompute();
      this.setStatus(`<b>${ex.name}:</b> ${ex.blurb}`);
    },

    setStatus(text) { $('statusBar').innerHTML = text; },
  };

  window.addEventListener('DOMContentLoaded', () => App.init());
  window.TrussApp = App;
})();
