/* ============================================================================
 * sections.js — AISC steel shape library (lookup + search)
 *
 * The data itself is in aisc-data.js (generated from the AISC Shapes Database
 * v15.0: W, HSS, pipe, angles, double angles, tees, channels, HP, M and S).
 * Areas are in square inches; the truss solver only needs A (and E), and the
 * weight is shown so learners can see the steel cost of a choice (Baker's
 * efficiency story).
 *
 * Internally the model stores A in m^2; use Units.aiscAreaToBase(in2) to
 * convert. Steel E = 29,000 ksi.
 * ==========================================================================*/

(function (global) {
  'use strict';

  const DATA = global.AISC_DATA || { source: '', families: [] };

  // Normalise a designation for lookups and search: case- and space-blind,
  // so "W12x26", "w12X26" and "W12X26" match, and so do names saved by
  // earlier versions of the app (e.g. "Pipe 4 Std" -> "PIPE4STD").
  const norm = (s) => String(s || '').toUpperCase().replace(/\s+/g, '');

  const BY_NAME = {}; // normalised name -> entry
  const SECTIONS = DATA.families.map((f) => ({
    id: f.id,
    group: f.name,
    items: f.shapes.map(([name, areaIn2, weight]) => {
      const it = { name, areaIn2, weight, family: f.id, key: norm(name) };
      BY_NAME[it.key] = it;
      return it;
    }),
  }));

  // Find a shape by any spelling of its designation (null if unknown).
  function find(name) { return BY_NAME[norm(name)] || null; }

  // Shapes in a family (or all, for 'ALL') whose designation contains the
  // query. "W12" finds W12X14…W12X336; "3/8" finds every 3/8"-thick shape.
  function search(familyId, query) {
    const q = norm(query).replace(/×/g, 'X');
    const groups = familyId === 'ALL' ? SECTIONS : SECTIONS.filter((g) => g.id === familyId);
    return groups.map((g) => ({
      id: g.id, group: g.group,
      items: q ? g.items.filter((it) => it.key.includes(q)) : g.items,
    })).filter((g) => g.items.length);
  }

  global.AISC_SECTIONS = SECTIONS;
  global.AISC_BY_NAME = BY_NAME;
  global.AISC = { find, search, norm, source: DATA.source, count: Object.keys(BY_NAME).length };
  global.AISC_E_KSI = 29000;
})(typeof window !== 'undefined' ? window : globalThis);
