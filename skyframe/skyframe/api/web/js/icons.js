/* SkyFrame — inline SVG icon palette (analysis chrome).
   Every glyph is authored in a 20×20 viewBox in the "house" stroke style
   (fill:none, stroke:currentColor, stroke-width 1.5, round caps/joins) so it
   inherits the surrounding text colour and is automatically light/dark correct.
   The few solid glyphs (pointer, run triangle, tag/rebar dots) opt back into
   fill via `fill="currentColor" stroke="none"` on their own path.

   No external assets — the CSP blocks them. Consumers either call `icon(id)`
   to get a full <svg> string, or read the raw inner markup from ICONS[id]. */

export const ICONS = {
  /* ---- left tool palette ---- */
  "tool-select": '<path d="M5 3 L5 16 L8.5 12.8 L10.7 17.2 L12.4 16.4 L10.2 12 L14.5 12 Z" fill="currentColor" stroke="none"/>',
  "tool-column": '<rect x="6" y="6" width="8" height="8"/><circle cx="10" cy="10" r="1" fill="currentColor" stroke="none"/>',
  "tool-beam": '<line x1="4.5" y1="14.5" x2="15.5" y2="5.5" stroke-width="2"/><circle cx="4.5" cy="14.5" r="1.6"/><circle cx="15.5" cy="5.5" r="1.6"/>',
  "tool-brace": '<line x1="4.5" y1="15.5" x2="15.5" y2="4.5" stroke-width="2" stroke-dasharray="3 2.4"/>',
  "tool-wall": '<rect x="4" y="6.5" width="12" height="7"/><line x1="4" y1="10" x2="16" y2="10"/><line x1="10" y1="6.5" x2="10" y2="10"/><line x1="7" y1="10" x2="7" y2="13.5"/><line x1="13" y1="10" x2="13" y2="13.5"/>',
  "tool-slab": '<path d="M4 13 L8 8 L16 8 L12 13 Z"/>',
  "tool-link": '<rect x="4.5" y="7.5" width="6" height="5"/><line x1="2" y1="10" x2="4.5" y2="10"/><line x1="10.5" y1="10" x2="16.5" y2="10"/>',
  "tool-spring": '<path d="M8 2.5 L11 4.5 L8 6.5 L11 8.5 L8 10.5" stroke-width="1.3"/><path d="M5 13 H15" stroke-width="1.3"/><path d="M6 13 L4.5 15 M9 13 L7.5 15 M12 13 L10.5 15" stroke-width="1.3"/>',
  "tool-linespring": '<path d="M3 7 H17"/><path d="M5 7 L3.5 10 M8 7 L6.5 10 M11 7 L9.5 10 M14 7 L12.5 10"/>',
  "tool-erase": '<path d="M6.5 3.5 L13 10 L10 13 H7 L3.5 9.5 Z"/><line x1="7" y1="13" x2="13" y2="13"/>',

  /* ---- top menu bar ---- */
  "menu-file": '<path d="M6 3 H12 L16 7 V17 H6 Z"/><path d="M12 3 V7 H16"/>',
  "menu-define": '<path d="M6 5 H14 M6 15 H14 M10 5 V15"/>',
  "menu-draw": '<path d="M4 16 L4.2 13 L13 4 L16 7 L7 15.8 Z"/><line x1="12" y1="5" x2="15" y2="8"/>',
  "menu-assign": '<path d="M4 8 L9 3 H16 V10 L11 15 Z"/><circle cx="12.3" cy="6.7" r="1.1" fill="currentColor" stroke="none"/>',
  "menu-analyze": '<path d="M7 5 L15 10 L7 15 Z"/>',
  "menu-display": '<path d="M3 10 C6 5 14 5 17 10 C14 15 6 15 3 10 Z"/><circle cx="10" cy="10" r="2.2"/>',
  "menu-design": '<path d="M10 3 L16 5 V10 C16 14 13 16 10 17 C7 16 4 14 4 10 V5 Z"/><path d="M7.4 10 L9.3 12 L12.8 8"/>',
  "menu-options": '<circle cx="10" cy="10" r="3"/><path d="M10 3 V5 M10 15 V17 M3 10 H5 M15 10 H17 M5 5 L6.5 6.5 M13.5 13.5 L15 15 M15 5 L13.5 6.5 M5 15 L6.5 13.5"/>',

  /* ---- status bar ---- */
  "view-plan": '<rect x="4" y="4" width="12" height="12" rx="0.5"/><line x1="10" y1="4" x2="10" y2="16"/><line x1="4" y1="10" x2="16" y2="10"/>',
  "view-elev": '<rect x="6" y="3" width="8" height="14"/><line x1="6" y1="7.7" x2="14" y2="7.7"/><line x1="6" y1="12.3" x2="14" y2="12.3"/>',
  "view-3d": '<path d="M10 2.5 L16.5 6.25 L16.5 13.75 L10 17.5 L3.5 13.75 L3.5 6.25 Z"/><path d="M3.5 6.25 L10 10 L16.5 6.25 M10 10 V17.5"/>',
  "status-run": '<path d="M6 4 L16 10 L6 16 Z" fill="currentColor" stroke="none"/>',
  "status-snap": '<path d="M5.5 5 V10.5 A4.5 4.5 0 0 0 14.5 10.5 V5"/><path d="M5.5 5 H8 M14.5 5 H12"/>',

  /* ---- model explorer: groups ---- */
  "exgroup-model": '<path d="M4 17 V6 L10 3 L16 6 V17 Z"/><path d="M7 17 V11 H13 V17"/>',
  "exgroup-definitions": '<path d="M6 5 H14 M6 15 H14 M10 5 V15"/>',
  "exgroup-assignments": '<path d="M4 8 L9 3 H16 V10 L11 15 Z"/><circle cx="12.3" cy="6.7" r="1.1" fill="currentColor" stroke="none"/>',
  "exgroup-results": '<path d="M3 17 H17"/><rect x="5" y="10" width="2.5" height="7"/><rect x="9" y="6" width="2.5" height="11"/><rect x="13" y="12" width="2.5" height="5"/>',

  /* ---- model explorer: leaves ---- */
  "exleaf-stories": '<path d="M4 6 H16 M4 10 H16 M4 14 H16"/>',
  "exleaf-grids": '<path d="M7 4 V16 M13 4 V16 M4 7 H16 M4 13 H16"/><circle cx="7" cy="4" r="1.4"/><circle cx="4" cy="7" r="1.4"/>',
  "exleaf-frames": '<path d="M6 5 H14 M6 15 H14 M10 5 V15"/>',
  "exleaf-shells": '<path d="M4 12 L8 8 L16 8 L12 12 Z"/><path d="M4 14 L8 10 L16 10 L12 14 Z"/>',
  "exleaf-supports": '<path d="M8 3 L11 5 L8 7 L11 9 L8 11"/><path d="M5 13 H13"/><path d="M6 13 L4.5 15 M9 13 L7.5 15 M12 13 L10.5 15"/>',
  "exleaf-links": '<rect x="4.5" y="7.5" width="6" height="5"/><line x1="2" y1="10" x2="4.5" y2="10"/><line x1="10.5" y1="10" x2="16.5" y2="10"/>',
  "exleaf-materials": '<circle cx="10" cy="10" r="6"/><path d="M7 8 A4 4 0 0 1 12 6"/>',
  "exleaf-designer": '<path d="M5 6 L14 5 L16 12 L9 16 L4 11 Z"/><circle cx="9" cy="10" r="1.3" fill="currentColor" stroke="none"/>',
  "exleaf-patterns": '<path d="M4 6 H16"/><path d="M6 6 V10 M10 6 V10 M14 6 V10"/><path d="M6 10 L5 8.6 M6 10 L7 8.6 M10 10 L9 8.6 M10 10 L11 8.6 M14 10 L13 8.6 M14 10 L15 8.6"/>',
  "exleaf-cases": '<path d="M10 4 V14 M6.5 10.5 L10 14 L13.5 10.5"/>',
  "exleaf-combos": '<circle cx="10" cy="10" r="6.5"/><path d="M10 6 V14 M6 10 H14"/>',
  "exleaf-functions": '<path d="M3 12 Q6 4 9 10 T15 8"/>',
  "exleaf-mass": '<path d="M6 7 H14 L15 16 H5 Z"/><path d="M8.5 7 A1.5 1.5 0 0 1 11.5 7"/>',
  "exleaf-deformed": '<path d="M4 6 V15 H16"/><path d="M4 12 Q9 6 16 8"/>',
  "exleaf-story": '<path d="M6 4 V16"/><path d="M6 6 H11 M6 10 H13 M6 14 H9"/>',
  "exleaf-modal": '<path d="M5 16 Q7 4 10 10 Q13 16 15 5"/>',
  "exleaf-reactions": '<path d="M10 4 V11 M7 7 L10 4 L13 7"/><path d="M6 15 L10 11 L14 15 Z"/>',
  "exleaf-forces": '<path d="M3 13 H17"/><path d="M5 13 Q10 4 15 13"/>',
  "exleaf-th": '<path d="M3 10 H5 L6.5 5 L8.5 15 L10.5 7 L12 12 L13.5 9 H17"/>',
  "exleaf-pushover": '<path d="M4 4 V16 H16"/><path d="M4 15 C8 15 10 7 16 5"/>',
  "exleaf-buckling": '<path d="M8 3 C14 8 6 12 12 17"/>',
  "exleaf-cuts": '<path d="M3.5 8 H16.5" stroke-dasharray="3 2"/><path d="M6 8 V12 M4.7 10.8 L6 12.2 L7.3 10.8"/><path d="M14 8 V12 M12.7 10.8 L14 12.2 L15.3 10.8"/>',
};

/** Full <svg> element string for icon `id` in the house stroke style. */
export function icon(id, cls) {
  const inner = ICONS[id] || "";
  return `<svg${cls ? ` class="${cls}"` : ""} viewBox="0 0 20 20" fill="none" `
    + `stroke="currentColor" stroke-width="1.5" stroke-linecap="round" `
    + `stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

/* ---- name → glyph lookups for the chrome builders ---- */

// tool palette: data-tool → glyph id
export const TOOL_ICON = {
  select: "tool-select", column: "tool-column", beam: "tool-beam",
  brace: "tool-brace", wall: "tool-wall", slab: "tool-slab",
  link: "tool-link", spring: "tool-spring", linespring: "tool-linespring",
  erase: "tool-erase",
};

// menu bar: menu name → glyph id
export const MENU_ICON = {
  File: "menu-file", Define: "menu-define", Draw: "menu-draw",
  Assign: "menu-assign", Analyze: "menu-analyze", Display: "menu-display",
  Design: "menu-design", Options: "menu-options",
};

// model explorer group heads: group label → glyph id
export const EXGROUP_ICON = {
  "Model": "exgroup-model",
  "Definitions": "exgroup-definitions",
  "Assignments": "exgroup-assignments",
  "Analysis Results": "exgroup-results",
};

// model explorer leaves: leaf label → glyph id
export const EXLEAF_ICON = {
  "Stories": "exleaf-stories",
  "Grid Systems": "exleaf-grids",
  "Frame Members": "exleaf-frames",
  "Frame Sections": "exleaf-frames",
  "Frame Assignments": "exleaf-frames",
  "Shells (walls / slabs)": "exleaf-shells",
  "Shell Sections": "exleaf-shells",
  "Shell Assignments": "exleaf-shells",
  "Wall Piers": "exleaf-shells",
  "Supports & Springs": "exleaf-supports",
  "Supports": "exleaf-supports",
  "Links": "exleaf-links",
  "Materials": "exleaf-materials",
  "Section Designer": "exleaf-designer",
  "Load Patterns": "exleaf-patterns",
  "Loads": "exleaf-patterns",
  "Load Cases": "exleaf-cases",
  "Combinations": "exleaf-combos",
  "Functions": "exleaf-functions",
  "Mass Source": "exleaf-mass",
  "Load Takedown": "exleaf-cases",
  "3D / Deformed": "exleaf-deformed",
  "Serviceability": "exleaf-deformed",
  "Story Results": "exleaf-story",
  "Drift Optimizer": "exleaf-story",
  "Modal": "exleaf-modal",
  "Reactions": "exleaf-reactions",
  "Member Forces": "exleaf-forces",
  "Time History": "exleaf-th",
  "Pushover": "exleaf-pushover",
  "Buckling": "exleaf-buckling",
  "Section Cuts": "exleaf-cuts",
  "Design": "menu-design",
};

// status-bar view segment: data-view → glyph id
export const VIEW_ICON = { plan: "view-plan", elev: "view-elev", view3d: "view-3d" };
