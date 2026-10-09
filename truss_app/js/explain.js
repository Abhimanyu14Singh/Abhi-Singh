/* ============================================================================
 * explain.js — Turns analysis numbers into plain-language teaching.
 *
 * Produces "insight cards" the UI renders in the Learn / Results panel, plus
 * the data behind the contribution chart and the member ranking tables.
 *
 * The educational backbone:
 *   - Virtual work / unit-load method:  delta = sum( N*n*L / (E*A) ).
 *   - The "controlling" member is the one whose term dominates that sum.
 *   - Why members attract force: load path, global bending (chords) vs shear
 *     (webs), proximity to supports, orientation.
 *   - Determinate vs indeterminate: in a determinate truss member forces are
 *     independent of E and A (equilibrium alone); deflections still depend on
 *     E and A. In an indeterminate truss, *relative* stiffness EA/L decides how
 *     force is shared — stiffer paths attract more load.
 *   - Bill Baker's virtual-work view of efficiency: each member's N*n product
 *     is its "virtual strain energy"; the most material-efficient way to
 *     control a chosen deflection is to add material where that product per
 *     unit volume is highest, driving the structure toward a uniform
 *     virtual-strain-energy density.  (See report/Baker references.)
 * ==========================================================================*/

(function (global) {
  'use strict';

  const fmtNum = global.fmtNum;

  // ---- helpers ----
  function pct(part, whole) {
    if (!whole) return 0;
    return (100 * part) / whole;
  }

  function memberName(idx) { return `Member ${idx + 1}`; }

  function describeWhyForce(result, m) {
    // Heuristic narrative for why this member carries large force.
    const bits = [];
    if (m.state === 'tension') bits.push('it is being pulled apart (tension)');
    else if (m.state === 'compression') bits.push('it is being squeezed (compression)');
    return bits.join(', ');
  }

  /* --------------------------------------------------------------------------
   * Build the full explanation payload from a solver result + model + units.
   * ------------------------------------------------------------------------*/
  function explain(model, result, units) {
    const cards = [];
    if (!result || !result.ok) {
      cards.push({
        type: 'error',
        title: 'Not solvable yet',
        html: (result && result.messages && result.messages.join(' ')) ||
          'Add joints, members, at least three reaction components, and a load.',
      });
      return { cards, ranking: [], contributions: null };
    }

    const U = units;
    const det = result.determinacy;

    /* ---- 1. Determinacy & stability ------------------------------------- */
    let detHtml = '';
    if (det.verdict === 'determinate') {
      detHtml =
        `With <b>m=${det.m}</b> members, <b>r=${det.r}</b> reactions and ` +
        `<b>j=${det.j}</b> joints, <b>m + r = ${det.total} = 2j</b>. ` +
        `This truss is <b>statically determinate</b>: member forces follow from ` +
        `equilibrium <i>alone</i>. A key consequence — changing a member's area ` +
        `or material will <b>not</b> change the member forces, but it <b>will</b> ` +
        `change the deflections.`;
    } else if (det.verdict === 'indeterminate') {
      detHtml =
        `With <b>m=${det.m}</b>, <b>r=${det.r}</b>, <b>j=${det.j}</b>, ` +
        `<b>m + r = ${det.total} &gt; 2j = ${det.dof}</b>, so this truss is ` +
        `<b>statically indeterminate to degree ${det.degree}</b>. Equilibrium is ` +
        `no longer enough — the members <i>share</i> load in proportion to their ` +
        `relative stiffness EA/L. <b>Stiffer load paths attract more force.</b> ` +
        `Try changing a member's area and watch the forces redistribute.`;
    } else {
      detHtml =
        `<b>m + r = ${det.total} &lt; 2j = ${det.dof}</b>: there are too few ` +
        `members/reactions, so the truss is a <b>mechanism (unstable)</b>. ` +
        `Add members or restraints.`;
    }
    cards.push({ type: 'info', title: 'Is it stable & determinate?', html: detHtml });

    /* ---- No load yet: nothing else to explain --------------------------- */
    const hasLoad = (model.loads || []).some((l) => Math.hypot(l.fx, l.fy) > 0);
    if (!hasLoad) {
      cards.push({
        type: 'tip',
        title: 'Add a load to see the story',
        html: 'The truss is stable but nothing is pushing on it yet, so every ' +
          'member force and deflection is zero. Pick the <b>Load</b> tool, click ' +
          'a joint, and type a load in the Properties tab.',
      });
      return { cards, ranking: [], contributions: null, sortedByForce: [] };
    }

    /* ---- 2. Maximum-force members --------------------------------------- */
    const sortedByForce = result.members
      .map((m) => ({ ...m, abs: Math.abs(m.N) }))
      .sort((a, b) => b.abs - a.abs);
    const maxT = result.members.filter((m) => m.state === 'tension')
      .sort((a, b) => b.N - a.N)[0];
    const maxC = result.members.filter((m) => m.state === 'compression')
      .sort((a, b) => a.N - b.N)[0];
    const top = sortedByForce[0];

    let forceHtml =
      `The most heavily loaded member is <b>${memberName(top.index)}</b> at ` +
      `<b>${U.force(top.N)}</b> (${top.state}). `;
    if (maxT) forceHtml += `Largest tension: ${memberName(maxT.index)} = ${U.force(maxT.N)}. `;
    if (maxC) forceHtml += `Largest compression: ${memberName(maxC.index)} = ${U.force(maxC.N)}. `;
    forceHtml +=
      `<br><br>Members attract force according to the <b>load path</b>: ` +
      `chord members resist the overall bending of the truss as a tension/` +
      `compression couple (largest near midspan of a simple span, or near the ` +
      `support of a cantilever), while diagonal and vertical web members ferry ` +
      `<b>shear</b> down to the supports. A member ends up with high force when ` +
      `it sits on the most direct route the load must travel to reach a support.`;
    cards.push({ type: 'force', title: 'Which member attracts the most force — and why', html: forceHtml });

    /* ---- 3. Zero-force members ------------------------------------------ */
    const zeros = result.members.filter((m) => m.state === 'zero');
    if (zeros.length) {
      cards.push({
        type: 'info',
        title: `${zeros.length} zero-force member${zeros.length > 1 ? 's' : ''}`,
        html:
          `Members ${zeros.map((z) => z.index + 1).join(', ')} carry essentially ` +
          `no force in this load case. They are not useless — they brace other ` +
          `members against buckling and stabilise the geometry, and they may pick ` +
          `up force under a different load.`,
      });
    }

    /* ---- 4. Virtual work: which member controls the deflection ---------- */
    let ranking = [];
    let contributions = null;
    const vwAll = result.virtualWork;
    const maxDisp = Math.max(0, ...result.displacements.map((d) => d.mag));
    const vwTiny = vwAll && Math.abs(vwAll.total) <= 1e-9 * Math.max(maxDisp, 1e-30);
    if (vwAll && (vwAll.restrained || vwTiny)) {
      cards.push({
        type: 'info',
        title: `Joint ${vwAll.targetNode + 1} doesn't move that way`,
        html: vwAll.restrained
          ? `A support holds Joint ${vwAll.targetNode + 1} in the chosen direction, ` +
            `so the virtual unit load goes straight into the support: every n is ` +
            `zero and the deflection is zero. Pick a free joint or another direction ` +
            `in the <b>Deflection of</b> picker.`
          : `Under this loading Joint ${vwAll.targetNode + 1} has (practically) no ` +
            `movement in the chosen direction, so there is nothing to break down. ` +
            `Try another joint or direction.`,
      });
    } else if (vwAll && vwAll.ranked.length) {
      const vw = vwAll;
      const targetNode = vw.targetNode;
      const total = vw.total;
      ranking = vw.ranked;
      contributions = vw;

      const ctrl = vw.ranked[0];
      const share = pct(Math.abs(ctrl.contribution), Math.abs(total));
      const dirText = vw.mode === 'vertical' ? 'vertically (down = +)'
        : vw.mode === 'horizontal' ? 'horizontally (right = +)'
          : 'along the direction it actually moves';
      const who = vw.auto
        ? `the most-displaced joint, <b>Joint ${targetNode + 1}</b>,`
        : `<b>Joint ${targetNode + 1}</b>`;
      const negatives = vw.contributions.filter(
        (c) => c.contribution * total < 0 && Math.abs(c.contribution) > 1e-6 * Math.abs(total));

      let vwHtml =
        `Deflection is found with the <b>unit-load (virtual work) method</b>. ` +
        `We solve the truss twice: once for the <i>real</i> loads (member forces ` +
        `<b>N</b>), and once for a <i>virtual</i> unit load placed at the joint ` +
        `whose movement we care about (member forces <b>n</b>). Each member then ` +
        `contributes` +
        `<div class="formula">&delta;<sub>k</sub> = N<sub>k</sub> &middot; n<sub>k</sub> &middot; L<sub>k</sub> / (E<sub>k</sub> A<sub>k</sub>)</div>` +
        `and the total joint deflection is the sum of these contributions, ` +
        `<b>&delta; = &Sigma; N n L /(EA)</b>.<br><br>` +
        `Measuring ${who} ${dirText}, the unit load (purple arrow on the ` +
        `drawing) gives <b>&delta; = ${U.defl(total)}</b>. The single member ` +
        `contributing most to that movement is <b>${memberName(ctrl.index)}</b>, ` +
        `responsible for <b>${share.toFixed(0)}%</b> of it.<br><br>` +
        (negatives.length
          ? `Note: ${negatives.map((c) => memberName(c.index)).join(', ')} ` +
            `${negatives.length > 1 ? 'have' : 'has'} a <b>negative</b> contribution ` +
            `(N and n have opposite signs) — ${negatives.length > 1 ? 'they push' : 'it pushes'} ` +
            `the joint the other way and partly cancel the rest, which is why the ` +
            `percentages can add up to more than 100%.<br><br>`
          : '') +
        `A member dominates the deflection when it has <b>large real force N</b>, ` +
        `<b>large virtual force n</b> (it lies on the direct path between the load ` +
        `and the joint of interest), is <b>long</b>, and/or is <b>slender</b> ` +
        `(small EA). That is exactly the recipe in the formula above.`;
      cards.push({ type: 'deflection', title: 'Which member controls the deflection — and why', html: vwHtml });

      /* ---- 5. Baker efficiency / how to fix it most efficiently -------- */
      // Each member's |N*n| is its virtual-strain-energy intensity; ranking by
      // contribution tells you where adding material buys the most stiffness.
      // Stiffening only helps for members pushing the joint the SAME way as the
      // total; stiffening a negative contributor would increase deflection.
      const helpful = vw.ranked.filter((c) => c.contribution * total > 0);
      const best = helpful[0] || ctrl;
      const second = helpful[1];
      let bakerHtml =
        `<b>How would you most efficiently stiffen this joint?</b> Not by adding ` +
        `steel everywhere — by adding it where it does the most work. Each ` +
        `member's contribution N&middot;n&middot;L/(EA) is its share of the ` +
        `<b>virtual strain energy</b> for this deflection. ` +
        `Bill Baker (structural engineer of the Burj Khalifa) made this the basis ` +
        `of his <i>energy-based design of lateral systems</i> — using the ` +
        `virtual-work sum to size a tall building's bracing for a drift limit. ` +
        `The material-efficient target is a <b>uniform virtual-strain-energy ` +
        `density</b>: pour material into the members doing the most virtual work, ` +
        `and stop feeding the ones doing little. ` +
        `<span class="cite">(W.F. Baker, “Energy-Based Design of Lateral ` +
        `Systems,” Structural Engineering International, 1992; the uniform-energy ` +
        `optimality idea itself traces back to Michell.)</span><br><br>` +
        `Right now <b>${memberName(best.index)}</b> is the best target` +
        (second ? `, followed by <b>${memberName(second.index)}</b>` : '') +
        `. Because deflection &prop; 1/A for a member, doubling that member's ` +
        `area roughly halves <i>its</i> contribution — so increasing the area of ` +
        `the top one or two ranked members reduces the joint deflection far more ` +
        `per kilogram of steel than thickening a low-contribution member. ` +
        `Conversely, members near the bottom of the ranking are good candidates ` +
        `to make <i>lighter</i>.`;
      cards.push({ type: 'baker', title: "Bill Baker's efficiency lens: where to add material", html: bakerHtml });
    }

    /* ---- 6. Determinate vs indeterminate teaching nudge ----------------- */
    if (det.verdict === 'determinate') {
      cards.push({
        type: 'tip',
        title: 'Experiment to try',
        html:
          `Because this truss is determinate, change a member's <b>area</b> in ` +
          `the member panel and re-solve: the <b>forces stay identical</b>, but ` +
          `the <b>deflection changes</b>. Now switch to the indeterminate ` +
          `X-braced example and repeat — this time the forces move too.`,
      });
    }

    void describeWhyForce; // reserved for richer per-member tooltips
    return { cards, ranking, contributions, sortedByForce };
  }

  global.TrussExplain = { explain };
  void fmtNum;
})(typeof window !== 'undefined' ? window : globalThis);
