/* SkyFrame Wave F — dependency-free registry of every Viewer3D (main 3D view
   and View › Windows panes). js/viewer3d.js calls fOnCreate(this) from its
   constructor (one hook line); js/f_select3d.js registers the attach
   callback once the app has booted and attaches every viewer, including the
   ones created before it. Kept import-free so viewer3d.js gains no cycle. */

export const fViewers = new Set();
let fAttach = null;

export function fOnCreate(v) {
  fViewers.add(v);
  const d0 = v.destroy && v.destroy.bind(v);
  if (d0) v.destroy = () => { fViewers.delete(v); try { if (v._fDetach) v._fDetach(); } catch (e) { /* never break */ } d0(); };
  if (fAttach) { try { fAttach(v); } catch (e) { /* never break the view */ } }
}

export function fSetAttach(fn) {
  fAttach = fn;
  for (const v of fViewers) { try { fn(v); } catch (e) { /* never break the view */ } }
}
