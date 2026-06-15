/* ============================================================================
 * model.js — Truss data model + edit history (undo/redo)
 *
 * The model is intentionally plain data so it is easy to reason about, JSON
 * serialise (save/load/share), and feed straight into TrussSolver.analyze().
 *
 * Units: the app is unit-agnostic but consistent. Defaults assume:
 *   length  -> metres (m)
 *   force   -> kilonewtons (kN)
 *   E       -> kN/m^2   (e.g. steel ~ 2.0e8 kN/m^2 = 200 GPa)
 *   A       -> m^2
 * The UI lets the learner change E and A; the *story* (which member controls
 * deflection / attracts force) is what matters, not the absolute numbers.
 * ==========================================================================*/

(function (global) {
  'use strict';

  // Sensible structural-steel defaults so a freshly drawn member already
  // produces realistic-looking results.
  const DEFAULT_E = 2.0e8; // kN/m^2  (200 GPa)
  const DEFAULT_A = 0.0020; // m^2     (20 cm^2)

  class TrussModel {
    constructor() {
      this.nodes = []; // { id, x, y }
      this.members = []; // { id, i, j, E, A }   (i,j = node array indices)
      this.supports = []; // { node, dx, dy }
      this.loads = []; // { node, fx, fy }
      this._nextNodeId = 1;
      this._nextMemberId = 1;

      this._history = [];
      this._future = [];
    }

    /* ----- history --------------------------------------------------------- */
    snapshot() {
      return JSON.stringify({
        nodes: this.nodes,
        members: this.members,
        supports: this.supports,
        loads: this.loads,
        n: this._nextNodeId,
        m: this._nextMemberId,
      });
    }

    _restore(snap) {
      const s = JSON.parse(snap);
      this.nodes = s.nodes;
      this.members = s.members;
      this.supports = s.supports;
      this.loads = s.loads;
      this._nextNodeId = s.n;
      this._nextMemberId = s.m;
    }

    // Call BEFORE a mutating edit to make it undoable.
    commit() {
      this._history.push(this.snapshot());
      if (this._history.length > 100) this._history.shift();
      this._future.length = 0;
    }

    undo() {
      if (!this._history.length) return false;
      this._future.push(this.snapshot());
      this._restore(this._history.pop());
      return true;
    }

    redo() {
      if (!this._future.length) return false;
      this._history.push(this.snapshot());
      this._restore(this._future.pop());
      return true;
    }

    canUndo() { return this._history.length > 0; }
    canRedo() { return this._future.length > 0; }

    /* ----- node ops -------------------------------------------------------- */
    addNode(x, y) {
      // Reuse an existing coincident node if very close (avoids duplicates).
      const existing = this.nodeAt(x, y, 1e-6);
      if (existing) return this.nodes.indexOf(existing);
      const node = { id: this._nextNodeId++, x, y };
      this.nodes.push(node);
      return this.nodes.length - 1;
    }

    nodeAt(x, y, tol) {
      for (const n of this.nodes) {
        if (Math.hypot(n.x - x, n.y - y) <= tol) return n;
      }
      return null;
    }

    moveNode(index, x, y) {
      this.nodes[index].x = x;
      this.nodes[index].y = y;
    }

    deleteNode(index) {
      // Remove members touching this node, then fix up indices.
      this.members = this.members.filter((m) => m.i !== index && m.j !== index);
      this.supports = this.supports.filter((s) => s.node !== index);
      this.loads = this.loads.filter((l) => l.node !== index);
      this.nodes.splice(index, 1);
      const reindex = (k) => (k > index ? k - 1 : k);
      this.members.forEach((m) => { m.i = reindex(m.i); m.j = reindex(m.j); });
      this.supports.forEach((s) => { s.node = reindex(s.node); });
      this.loads.forEach((l) => { l.node = reindex(l.node); });
    }

    /* ----- member ops ------------------------------------------------------ */
    addMember(i, j, E = DEFAULT_E, A = DEFAULT_A, section = null) {
      if (i === j) return null;
      // Avoid duplicate members between the same pair.
      const dup = this.members.find(
        (m) => (m.i === i && m.j === j) || (m.i === j && m.j === i)
      );
      if (dup) return this.members.indexOf(dup);
      const member = { id: this._nextMemberId++, i, j, E, A, section };
      this.members.push(member);
      return this.members.length - 1;
    }

    deleteMember(index) {
      this.members.splice(index, 1);
    }

    /* ----- supports -------------------------------------------------------- */
    // kind: 'pin' (dx+dy), 'roller-x' (dy only), 'roller-y' (dx only), 'none'
    setSupport(node, kind) {
      this.supports = this.supports.filter((s) => s.node !== node);
      if (kind === 'pin') this.supports.push({ node, dx: true, dy: true });
      else if (kind === 'roller-x') this.supports.push({ node, dx: false, dy: true });
      else if (kind === 'roller-y') this.supports.push({ node, dx: true, dy: false });
    }

    supportFor(node) {
      return this.supports.find((s) => s.node === node) || null;
    }

    supportKind(node) {
      const s = this.supportFor(node);
      if (!s) return 'none';
      if (s.dx && s.dy) return 'pin';
      if (!s.dx && s.dy) return 'roller-x'; // restrains vertical, rolls in x
      if (s.dx && !s.dy) return 'roller-y';
      return 'none';
    }

    /* ----- loads ----------------------------------------------------------- */
    setLoad(node, fx, fy) {
      this.loads = this.loads.filter((l) => l.node !== node);
      if (fx !== 0 || fy !== 0) this.loads.push({ node, fx, fy });
    }

    loadFor(node) {
      return this.loads.find((l) => l.node === node) || null;
    }

    /* ----- bulk ------------------------------------------------------------ */
    clear() {
      this.nodes = [];
      this.members = [];
      this.supports = [];
      this.loads = [];
      this._nextNodeId = 1;
      this._nextMemberId = 1;
    }

    loadFrom(obj) {
      this.clear();
      (obj.nodes || []).forEach((n) => this.addNode(n.x, n.y));
      (obj.members || []).forEach((m) =>
        this.addMember(m.i, m.j, m.E ?? DEFAULT_E, m.A ?? DEFAULT_A)
      );
      (obj.supports || []).forEach((s) => {
        const kind = s.dx && s.dy ? 'pin' : !s.dx && s.dy ? 'roller-x'
          : s.dx && !s.dy ? 'roller-y' : 'none';
        this.setSupport(s.node, kind);
      });
      (obj.loads || []).forEach((l) => this.setLoad(l.node, l.fx, l.fy));
    }

    toJSON() {
      return {
        nodes: this.nodes.map((n) => ({ x: n.x, y: n.y })),
        members: this.members.map((m) => ({ i: m.i, j: m.j, E: m.E, A: m.A })),
        supports: this.supports.map((s) => ({ node: s.node, dx: s.dx, dy: s.dy })),
        loads: this.loads.map((l) => ({ node: l.node, fx: l.fx, fy: l.fy })),
      };
    }
  }

  global.TrussModel = TrussModel;
  global.TRUSS_DEFAULTS = { E: DEFAULT_E, A: DEFAULT_A };
})(typeof window !== 'undefined' ? window : globalThis);
