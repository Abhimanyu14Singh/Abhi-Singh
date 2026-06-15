/* ============================================================================
 * solver.js — Plane (2D) truss analysis engine
 *
 * Two complementary methods, run together so the app can *teach* the link
 * between them:
 *
 *   1. Direct Stiffness Method  ->  joint displacements + member axial forces.
 *      Works for statically determinate AND indeterminate trusses, and for any
 *      (stable) restraint layout.
 *
 *   2. Unit-Load / Virtual-Work Method  ->  the deflection at a chosen joint
 *      DOF, decomposed into a per-member contribution  N*n*L/(E*A).
 *      The sum of those contributions equals the stiffness-method displacement
 *      at the same DOF (to numerical precision). Showing that agreement is the
 *      heart of the educational story.
 *
 * Sign convention for axial force N: POSITIVE = TENSION, NEGATIVE = COMPRESSION.
 *
 * No external dependencies — small dense linear solver included below.
 * ==========================================================================*/

(function (global) {
  'use strict';

  /* --------------------------------------------------------------------------
   * Dense linear algebra: solve A x = b by Gauss elimination w/ partial pivot.
   * Truss systems here are small (tens to low-hundreds of DOF), so an O(n^3)
   * dense solve is plenty and keeps the code transparent for learners.
   * ------------------------------------------------------------------------*/
  function solveLinearSystem(A, b) {
    const n = b.length;
    // Work on copies so callers keep their matrices.
    const M = A.map((row) => row.slice());
    const x = b.slice();

    for (let col = 0; col < n; col++) {
      // Partial pivot: find the largest magnitude entry in this column.
      let pivotRow = col;
      let pivotVal = Math.abs(M[col][col]);
      for (let r = col + 1; r < n; r++) {
        const v = Math.abs(M[r][col]);
        if (v > pivotVal) {
          pivotVal = v;
          pivotRow = r;
        }
      }
      if (pivotVal < 1e-12) {
        // Singular (or near-singular) -> mechanism / unstable structure.
        return null;
      }
      if (pivotRow !== col) {
        [M[col], M[pivotRow]] = [M[pivotRow], M[col]];
        [x[col], x[pivotRow]] = [x[pivotRow], x[col]];
      }
      // Eliminate below.
      const pivot = M[col][col];
      for (let r = col + 1; r < n; r++) {
        const factor = M[r][col] / pivot;
        if (factor === 0) continue;
        for (let c = col; c < n; c++) {
          M[r][c] -= factor * M[col][c];
        }
        x[r] -= factor * x[col];
      }
    }
    // Back-substitution.
    for (let row = n - 1; row >= 0; row--) {
      let sum = x[row];
      for (let c = row + 1; c < n; c++) sum -= M[row][c] * x[c];
      x[row] = sum / M[row][row];
    }
    return x;
  }

  /* --------------------------------------------------------------------------
   * Geometry helpers for a member connecting node i -> node j.
   * Returns length L and direction cosines c = cos(theta), s = sin(theta).
   * ------------------------------------------------------------------------*/
  function memberGeometry(ni, nj) {
    const dx = nj.x - ni.x;
    const dy = nj.y - ni.y;
    const L = Math.hypot(dx, dy);
    return { dx, dy, L, c: dx / L, s: dy / L };
  }

  /* --------------------------------------------------------------------------
   * Determinacy & stability classification for a plane truss.
   *   m + r  vs  2j   (m members, r reaction components, j joints)
   *     < 2j  -> unstable (mechanism, too few constraints)
   *     = 2j  -> statically determinate (if also stable)
   *     > 2j  -> statically indeterminate, degree = (m + r) - 2j
   * Note: the count test is necessary but not sufficient — geometry can still
   * be unstable (e.g. collinear members). The stiffness solve catches that via
   * a singular global matrix, which we report separately.
   * ------------------------------------------------------------------------*/
  function classifyDeterminacy(nodeCount, memberCount, reactionCount) {
    const j = nodeCount;
    const m = memberCount;
    const r = reactionCount;
    const dof = 2 * j;
    const total = m + r;
    let verdict, degree;
    if (total < dof) {
      verdict = 'unstable';
      degree = total - dof; // negative
    } else if (total === dof) {
      verdict = 'determinate';
      degree = 0;
    } else {
      verdict = 'indeterminate';
      degree = total - dof; // positive = degree of static indeterminacy
    }
    return { j, m, r, dof, total, verdict, degree };
  }

  /* --------------------------------------------------------------------------
   * Build the assembled global stiffness matrix K (2j x 2j) and the per-member
   * bookkeeping needed to recover axial forces afterwards.
   *
   * Element stiffness in global coordinates (classic 2D bar):
   *   k = (E*A/L) * [[ c^2,  c*s, -c^2, -c*s],
   *                  [ c*s,  s^2, -c*s, -s^2],
   *                  [-c^2, -c*s,  c^2,  c*s],
   *                  [-c*s, -s^2,  c*s,  s^2]]
   * ------------------------------------------------------------------------*/
  function assembleStiffness(nodes, members) {
    const n = nodes.length;
    const ndof = 2 * n;
    const K = Array.from({ length: ndof }, () => new Array(ndof).fill(0));
    const memberInfo = [];

    for (const mem of members) {
      const ni = nodes[mem.i];
      const nj = nodes[mem.j];
      const g = memberGeometry(ni, nj);
      const EA_L = (mem.E * mem.A) / g.L;
      const c = g.c;
      const s = g.s;
      const cc = c * c;
      const ss = s * s;
      const cs = c * s;

      // DOF map: [ix, iy, jx, jy]
      const dofs = [2 * mem.i, 2 * mem.i + 1, 2 * mem.j, 2 * mem.j + 1];
      const ke = [
        [cc, cs, -cc, -cs],
        [cs, ss, -cs, -ss],
        [-cc, -cs, cc, cs],
        [-cs, -ss, cs, ss],
      ];
      for (let a = 0; a < 4; a++) {
        for (let b = 0; b < 4; b++) {
          K[dofs[a]][dofs[b]] += EA_L * ke[a][b];
        }
      }
      memberInfo.push({ ...g, EA_L, dofs, mem });
    }
    return { K, ndof, memberInfo };
  }

  /* --------------------------------------------------------------------------
   * Reduce the system by removing constrained DOFs, solve, then expand back to
   * the full displacement vector. `fixed` is a boolean array length ndof.
   * Returns { u, ok } where u is the full displacement vector.
   * ------------------------------------------------------------------------*/
  function solveConstrained(K, F, fixed, ndof) {
    // Map free DOFs -> reduced indices.
    const freeIdx = [];
    for (let d = 0; d < ndof; d++) if (!fixed[d]) freeIdx.push(d);

    const nf = freeIdx.length;
    const Kff = Array.from({ length: nf }, () => new Array(nf).fill(0));
    const Ff = new Array(nf).fill(0);
    for (let a = 0; a < nf; a++) {
      Ff[a] = F[freeIdx[a]];
      const ra = freeIdx[a];
      for (let b = 0; b < nf; b++) {
        Kff[a][b] = K[ra][freeIdx[b]];
      }
    }
    const uf = solveLinearSystem(Kff, Ff);
    if (uf === null) return { u: null, ok: false };

    const u = new Array(ndof).fill(0);
    for (let a = 0; a < nf; a++) u[freeIdx[a]] = uf[a];
    return { u, ok: true };
  }

  /* --------------------------------------------------------------------------
   * Recover member axial forces from a displacement field.
   *   N = (E*A/L) * [ -c, -s, c, s ] . [ui, vi, uj, vj]
   *     = (E*A/L) * ( (uj-ui)*c + (vj-vi)*s )
   * Positive => tension.
   * ------------------------------------------------------------------------*/
  function recoverMemberForces(memberInfo, u) {
    return memberInfo.map((info) => {
      const [dix, diy, djx, djy] = info.dofs;
      const elong = (u[djx] - u[dix]) * info.c + (u[djy] - u[diy]) * info.s;
      const N = info.EA_L * elong; // axial force, +tension
      return { N, elongation: elong };
    });
  }

  /* --------------------------------------------------------------------------
   * Reactions at constrained DOFs:  R = K*u - F   (restricted to fixed DOFs).
   * ------------------------------------------------------------------------*/
  function computeReactions(K, u, F, fixed, ndof) {
    const R = new Array(ndof).fill(0);
    for (let d = 0; d < ndof; d++) {
      if (!fixed[d]) continue;
      let ku = 0;
      const row = K[d];
      for (let c = 0; c < ndof; c++) ku += row[c] * u[c];
      R[d] = ku - F[d];
    }
    return R;
  }

  /* --------------------------------------------------------------------------
   * MAIN ENTRY: analyze(model)
   *
   * model = {
   *   nodes:   [{ x, y }, ...],
   *   members: [{ i, j, E, A }, ...],          // i,j are node indices
   *   supports:[{ node, dx:bool, dy:bool }],   // dx/dy restrained?
   *   loads:   [{ node, fx, fy }],             // applied joint loads
   * }
   *
   * Returns a rich result object consumed by the UI / explanation layer.
   * ------------------------------------------------------------------------*/
  function analyze(model) {
    const { nodes, members, supports, loads } = model;
    const result = { ok: false, messages: [] };

    if (nodes.length < 2 || members.length < 1) {
      result.messages.push('Add at least two joints and one member.');
      return result;
    }

    // --- Build constraint (fixed-DOF) vector + reaction count ---------------
    const ndof = 2 * nodes.length;
    const fixed = new Array(ndof).fill(false);
    let reactionCount = 0;
    for (const sup of supports || []) {
      if (sup.dx) { fixed[2 * sup.node] = true; reactionCount++; }
      if (sup.dy) { fixed[2 * sup.node + 1] = true; reactionCount++; }
    }

    // --- Determinacy classification ----------------------------------------
    result.determinacy = classifyDeterminacy(
      nodes.length, members.length, reactionCount
    );
    if (reactionCount < 3) {
      result.messages.push(
        'Fewer than 3 reaction components — the truss is not fully restrained ' +
        'against rigid-body motion and will be unstable.'
      );
    }

    // --- Load vector --------------------------------------------------------
    const F = new Array(ndof).fill(0);
    for (const ld of loads || []) {
      F[2 * ld.node] += ld.fx || 0;
      F[2 * ld.node + 1] += ld.fy || 0;
    }

    // --- Assemble & solve the REAL system ----------------------------------
    const { K, memberInfo } = assembleStiffness(nodes, members);
    const real = solveConstrained(K, F, fixed, ndof);
    if (!real.ok) {
      result.messages.push(
        'The structure is a mechanism (global stiffness matrix is singular). ' +
        'Check for missing members, collinear supports, or unconnected joints.'
      );
      return result;
    }

    const memberForces = recoverMemberForces(memberInfo, real.u);
    const reactions = computeReactions(K, real.u, F, fixed, ndof);

    // Attach per-member results.
    result.members = members.map((mem, k) => {
      const info = memberInfo[k];
      const N = memberForces[k].N;
      return {
        index: k,
        i: mem.i,
        j: mem.j,
        L: info.L,
        E: mem.E,
        A: mem.A,
        N,
        stress: N / mem.A,
        state: N > 1e-9 ? 'tension' : N < -1e-9 ? 'compression' : 'zero',
        // Per-member axial stiffness EA/L — governs how much load it attracts
        // in an indeterminate structure, and how much it stretches.
        axialStiffness: info.EA_L,
      };
    });

    // Node displacements (full field).
    result.displacements = nodes.map((_, k) => ({
      node: k,
      ux: real.u[2 * k],
      uy: real.u[2 * k + 1],
      mag: Math.hypot(real.u[2 * k], real.u[2 * k + 1]),
    }));
    result.reactions = nodes.map((_, k) => ({
      node: k,
      rx: fixed[2 * k] ? reactions[2 * k] : null,
      ry: fixed[2 * k + 1] ? reactions[2 * k + 1] : null,
    }));

    result.rawDisplacement = real.u;
    result.K = K;
    result.F = F;
    result.fixed = fixed;
    result.ndof = ndof;
    result.memberInfo = memberInfo;
    result.ok = true;

    // --- Pick the most-displaced free joint as the default "deflection of
    //     interest" target, then run the virtual-work decomposition there. --
    let target = null;
    let bestMag = -1;
    for (const d of result.displacements) {
      if (d.mag > bestMag) { bestMag = d.mag; target = d; }
    }
    if (target && bestMag > 0) {
      // Direction of the actual displacement at that joint (unit vector).
      const dirx = target.ux / target.mag;
      const diry = target.uy / target.mag;
      result.virtualWork = virtualWorkDecomposition(
        result, target.node, dirx, diry
      );
      result.virtualWork.targetNode = target.node;
      result.virtualWork.dir = { x: dirx, y: diry };
    }

    return result;
  }

  /* --------------------------------------------------------------------------
   * Unit-Load / Virtual-Work decomposition of the deflection at a chosen
   * joint DOF (node + unit direction vector). Returns each member's
   * contribution  delta_k = N_k * n_k * L_k / (E_k * A_k)  whose sum is the
   * deflection of the real structure at that DOF (component along dir).
   *
   * n_k are member forces under a *virtual* unit load applied at the target.
   * Using the real (possibly indeterminate) structure for the virtual system
   * is valid by the unit-load theorem — any equilibrium virtual force field
   * works — and it makes the two methods agree exactly.
   * ------------------------------------------------------------------------*/
  function virtualWorkDecomposition(result, targetNode, dirx, diry) {
    const { K, fixed, ndof, memberInfo, members } = result;

    // Virtual load: unit force at targetNode along (dirx, diry).
    const Fv = new Array(ndof).fill(0);
    Fv[2 * targetNode] = dirx;
    Fv[2 * targetNode + 1] = diry;

    const virt = solveConstrained(K, Fv, fixed, ndof);
    if (!virt.ok) return { contributions: [], total: 0 };

    const nForces = recoverMemberForces(memberInfo, virt.u);

    let total = 0;
    const contributions = members.map((mem, k) => {
      const info = memberInfo[k];
      const N = result.members[k].N; // real axial force
      const nbar = nForces[k].N; // virtual axial force
      const flex = info.L / (mem.E * mem.A); // L/(EA)
      const contrib = N * nbar * flex; // N * n * L / (EA)
      total += contrib;
      return {
        index: k,
        N,
        n: nbar,
        L: info.L,
        EA: mem.E * mem.A,
        flexibility: flex,
        contribution: contrib,
      };
    });

    // Rank by absolute contribution (which member controls the deflection).
    const ranked = contributions
      .map((c) => ({ ...c, absContribution: Math.abs(c.contribution) }))
      .sort((a, b) => b.absContribution - a.absContribution);

    return { contributions, ranked, total };
  }

  /* Public API ------------------------------------------------------------ */
  global.TrussSolver = {
    analyze,
    classifyDeterminacy,
    virtualWorkDecomposition,
    // exposed for testing
    _solveLinearSystem: solveLinearSystem,
    _memberGeometry: memberGeometry,
  };
})(typeof window !== 'undefined' ? window : globalThis);
