/* SkyFrame E3 — GPU 3D renderer: shader programs + batch primitives (three.js r170, WebGL2).

   Everything here is geometry uploaded ONCE into GPU buffers; per frame only
   uniforms change (camera, displacement scale, contour limits, alpha).

     FatLines      instanced screen-space quads, one instance per segment:
                   per-instance endpoints, rgba (alpha < 0 = depth-faded like
                   the Classic depthAlpha), width in CSS px, optional pick id,
                   optional displacement vectors (deformed / mode shapes in the
                   vertex shader: p = p0 + uDisp·d). Near-plane clipped in view
                   space exactly like Viewer3D._projSeg (near = 0.05).
     makeMeshMat   triangles: per-vertex rgba, optional displacement, optional
                   per-vertex scalar → blue-white-red colormap in the fragment
                   shader (same stops and 8-bit rounding as viewer3d
                   divergingColor), optional lit mode (quantised head-light
                   Lambert identical to the Classic extruded view).
     makePrismMat  instanced extruded members: unit prism (s, y, z) per section
                   profile; per-instance columns iX (axis, incl. interpolated
                   joint offsets), iY / iZ (local 2 / 3 axes), iT (origin);
                   normals by inverse-transpose; optional 5-station deformed
                   displacement (iD0..iD4 × uDisp) for extruded deformed /
                   animated mode shapes.
*/
import * as THREE from "../../vendor/three/0.170.0/three.module.min.js";

export { THREE };

/* ------------------------------------------------------------ colours */
const _cssCache = new Map();
/** css colour → [r, g, b, a] in 0..1 (hex, rgb(), rgba()). */
export function cssRGBA(c) {
  if (Array.isArray(c)) return c;
  let v = _cssCache.get(c);
  if (v) return v;
  v = [0.6, 0.63, 0.69, 1];
  if (typeof c === "string") {
    if (c[0] === "#") {
      const h = c.length === 4 ? c.slice(1).split("").map(x => x + x).join("") : c.slice(1, 7);
      v = [parseInt(h.slice(0, 2), 16) / 255, parseInt(h.slice(2, 4), 16) / 255, parseInt(h.slice(4, 6), 16) / 255, 1];
    } else {
      const m = c.match(/rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+))?/);
      if (m) v = [+m[1] / 255, +m[2] / 255, +m[3] / 255, m[4] != null ? +m[4] : 1];
    }
  }
  _cssCache.set(c, v);
  return v;
}

/* ------------------------------------------------------------ fat lines */
const LINE_VS = `
attribute vec3 iA;
attribute vec3 iB;
attribute vec4 iCol;
attribute float iW;
#ifdef DISP
attribute vec3 iDA;
attribute vec3 iDB;
uniform float uDisp;
#endif
#ifdef PICK
attribute float iId;
flat out vec3 vId;
#endif
uniform vec2 uRes;
uniform float uPx;
uniform float uNear;
uniform vec2 uFade;
uniform float uGhost;
uniform float uWScale;
uniform float uBias;
uniform float uCap;
varying vec4 vCol;
varying float vDist;
void main() {
  vec3 a = iA, b = iB;
#ifdef DISP
  a += uDisp * iDA; b += uDisp * iDB;
#endif
  vec4 ca = modelViewMatrix * vec4(a, 1.0);
  vec4 cb = modelViewMatrix * vec4(b, 1.0);
  float za = -ca.z, zb = -cb.z;
  if (za < uNear && zb < uNear) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
  if (za < uNear) { float t = (uNear - za) / (zb - za); ca = mix(ca, cb, t); ca.z = -uNear; za = uNear; }
  else if (zb < uNear) { float t = (uNear - zb) / (za - zb); cb = mix(cb, ca, t); cb.z = -uNear; zb = uNear; }
  vec4 pa = projectionMatrix * ca, pb = projectionMatrix * cb;
  vec2 sa = pa.xy / pa.w * 0.5 * uRes, sb = pb.xy / pb.w * 0.5 * uRes;
  vec2 d = sb - sa;
  float L = length(d);
  vec2 dir = L > 1e-6 ? d / L : vec2(1.0, 0.0);
  vec2 nrm = vec2(-dir.y, dir.x);
  float hw = 0.5 * max(iW * uWScale, 0.75) * uPx;
  bool atB = position.x > 0.5;
  vec4 p = atB ? pb : pa;
  vec2 s = (atB ? sb : sa) + nrm * position.y * hw + dir * (atB ? 1.0 : -1.0) * hw * uCap;
  p.xy = s / (0.5 * uRes) * p.w;
  p.z -= uBias * p.w;
  gl_Position = p;
  vDist = atB ? L / uPx : 0.0;
  float al = iCol.a;
  if (al < 0.0) {
    float zm = 0.5 * (za + zb);
    float t = clamp((zm - uFade.x) / max(uFade.y - uFade.x, 1e-6), 0.0, 1.0);
    float fa = 1.0 - t * 0.5;
    al = uGhost > 0.0 ? uGhost : (al < -1.5 ? max(fa, 0.95) : fa);
  }
  vCol = vec4(iCol.rgb, al);
#ifdef PICK
  vId = vec3(mod(iId, 256.0), mod(floor(iId / 256.0), 256.0), floor(iId / 65536.0)) / 255.0;
#endif
}`;
const LINE_FS = `
varying vec4 vCol;
varying float vDist;
uniform float uDash;
uniform float uAlpha;
#ifdef PICK
flat in vec3 vId;
#endif
void main() {
  if (uDash > 0.0 && mod(vDist, uDash) > uDash * 0.6) discard;
#ifdef PICK
  gl_FragColor = vec4(vId, 1.0);
#else
  gl_FragColor = vec4(vCol.rgb, vCol.a * uAlpha);
#endif
}`;

let _quad = null;
function quadGeo() {
  if (_quad) return _quad;
  _quad = new THREE.BufferGeometry();
  _quad.setAttribute("position", new THREE.Float32BufferAttribute([0, -1, 0, 0, 1, 0, 1, -1, 0, 1, 1, 0], 3));
  _quad.setIndex([0, 2, 1, 1, 2, 3]);
  return _quad;
}

export function lineUniforms() {
  return {
    uRes: { value: new THREE.Vector2(1, 1) }, uPx: { value: 1 }, uNear: { value: 0.05 },
    uFade: { value: new THREE.Vector2(0, 1) }, uGhost: { value: 0 }, uWScale: { value: 1 },
    uBias: { value: 0 }, uCap: { value: 1 }, uDash: { value: 0 }, uAlpha: { value: 1 }, uDisp: { value: 0 },
  };
}

/** A batch of screen-space wide segments in ONE draw call. */
export class FatLines {
  /** n segments; opts {disp, pick, transparent, depthWrite, cap, dash, bias, order} */
  constructor(n, opts = {}) {
    this.n = n;
    const g = new THREE.InstancedBufferGeometry();
    const q = quadGeo();
    g.setAttribute("position", q.getAttribute("position"));
    g.setIndex(q.getIndex());
    const mk = (name, size) => { const a = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * size), size); g.setAttribute(name, a); return a.array; };
    this.A = mk("iA", 3); this.B = mk("iB", 3); this.C = mk("iCol", 4); this.W = mk("iW", 1);
    if (opts.disp) { this.DA = mk("iDA", 3); this.DB = mk("iDB", 3); }
    if (opts.pick) this.ID = mk("iId", 1);
    g.instanceCount = n;
    this.geo = g;
    const defines = {};
    if (opts.disp) defines.DISP = 1;
    this.uniforms = lineUniforms();
    this.uniforms.uCap.value = opts.cap == null ? 1 : opts.cap;
    this.uniforms.uDash.value = opts.dash || 0;
    this.uniforms.uBias.value = opts.bias || 0;
    this.mat = new THREE.ShaderMaterial({
      vertexShader: LINE_VS, fragmentShader: LINE_FS, uniforms: this.uniforms, defines,
      transparent: true, depthWrite: opts.depthWrite !== false, depthTest: opts.depthTest !== false,
    });
    this.mesh = new THREE.Mesh(g, this.mat);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = opts.order || 0;
    if (opts.pick) {
      const pu = lineUniforms();
      pu.uCap.value = this.uniforms.uCap.value;
      this.pickUniforms = pu;
      this.pickMat = new THREE.ShaderMaterial({
        vertexShader: LINE_VS, fragmentShader: LINE_FS, uniforms: pu, defines: { ...defines, PICK: 1 },
        transparent: false, depthWrite: true,
      });
      this.pickMesh = new THREE.Mesh(g, this.pickMat);
      this.pickMesh.frustumCulled = false;
    }
  }
  set(i, a, b, rgba, w) {
    const o = i * 3, c = i * 4;
    this.A[o] = a[0]; this.A[o + 1] = a[1]; this.A[o + 2] = a[2];
    this.B[o] = b[0]; this.B[o + 1] = b[1]; this.B[o + 2] = b[2];
    this.C[c] = rgba[0]; this.C[c + 1] = rgba[1]; this.C[c + 2] = rgba[2]; this.C[c + 3] = rgba[3];
    this.W[i] = w;
  }
  touch(...names) {
    for (const nm of names.length ? names : ["iA", "iB", "iCol", "iW", "iDA", "iDB", "iId"]) {
      const a = this.geo.getAttribute(nm);
      if (a) a.needsUpdate = true;
    }
  }
  dispose() { this.geo.dispose(); this.mat.dispose(); if (this.pickMat) this.pickMat.dispose(); }
}

/* ------------------------------------------------------------ triangle meshes */
const MESH_VS = `
attribute vec4 aCol;
#ifdef DISP
attribute vec3 aDisp;
uniform float uDisp;
#endif
#ifdef SCALAR
attribute float aVal;
uniform float uVmax;
varying float vT;
#endif
#ifdef LIT
uniform vec3 uLight;
flat out float vF;
#endif
uniform float uBias;
varying vec4 vCol;
void main() {
  vec3 p = position;
#ifdef DISP
  p += uDisp * aDisp;
#endif
  vec4 pos = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  pos.z -= uBias * pos.w;
  gl_Position = pos;
  vCol = aCol;
#ifdef SCALAR
  vT = aVal / uVmax;
#endif
#ifdef LIT
  float d = dot(normalize(normal), uLight);
  float s = d > 0.0 ? d : 0.12 * -d;
  float q = min(15.0, floor(s * 15.0 + 0.5));
  vF = 0.40 + 0.72 * q / 15.0;
#endif
}`;
const COLORMAP = `
vec3 cmap(float t) {
  t = clamp(t, -1.0, 1.0);
  vec3 w = vec3(242.0, 245.0, 248.0);
  vec3 e = t < 0.0 ? vec3(44.0, 127.0, 214.0) : vec3(224.0, 82.0, 82.0);
  return floor(w + (e - w) * abs(t) + 0.5) / 255.0;
}`;
const MESH_FS = `
varying vec4 vCol;
uniform float uAlpha;
#ifdef SCALAR
varying float vT;
${COLORMAP}
#endif
#ifdef LIT
flat in float vF;
#endif
void main() {
  vec4 c = vCol;
#ifdef SCALAR
  c.rgb = cmap(vT);
#endif
#ifdef LIT
  c.rgb = min(vec3(1.0), floor(c.rgb * 255.0 * vF + 0.5) / 255.0);
#endif
  gl_FragColor = vec4(c.rgb, c.a * uAlpha);
}`;

export function makeMeshMat(opts = {}) {
  const defines = {};
  if (opts.disp) defines.DISP = 1;
  if (opts.scalar) defines.SCALAR = 1;
  if (opts.lit) defines.LIT = 1;
  const uniforms = { uAlpha: { value: 1 }, uDisp: { value: 0 }, uVmax: { value: 1 }, uLight: { value: new THREE.Vector3(0, 0, 1) }, uBias: { value: opts.bias || 0 } };
  return new THREE.ShaderMaterial({
    vertexShader: MESH_VS, fragmentShader: MESH_FS, uniforms, defines,
    transparent: !!opts.transparent, depthWrite: opts.depthWrite !== false, side: THREE.DoubleSide,
    polygonOffset: !!opts.polyOffset, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
  });
}

/** Non-indexed triangle mesh builder (flat per-vertex rgba, optional disp / scalar / normal). */
export class TriBuilder {
  constructor() { this.P = []; this.C = []; this.N = null; this.D = null; this.V = null; }
  tri(a, b, c, rgba, extra) {
    this.P.push(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]);
    for (let k = 0; k < 3; k++) this.C.push(rgba[0], rgba[1], rgba[2], rgba[3]);
    if (extra) {
      if (extra.n) { (this.N || (this.N = [])).push(...extra.n, ...extra.n, ...extra.n); }
      if (extra.d) { (this.D || (this.D = [])).push(...extra.d[0], ...extra.d[1], ...extra.d[2]); }
      if (extra.v != null) { const v = extra.v; (this.V || (this.V = [])).push(...(Array.isArray(v) ? v : [v, v, v])); }
    }
  }
  get count() { return this.P.length / 9; }
  build() {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.P, 3));
    g.setAttribute("aCol", new THREE.Float32BufferAttribute(this.C, 4));
    if (this.N) g.setAttribute("normal", new THREE.Float32BufferAttribute(this.N, 3));
    if (this.D) g.setAttribute("aDisp", new THREE.Float32BufferAttribute(this.D, 3));
    if (this.V) g.setAttribute("aVal", new THREE.Float32BufferAttribute(this.V, 1));
    return g;
  }
}

/** Triangulate a planar 3D polygon (+ holes) via a 2D frame {to2}; returns index triples into [outer, ...holes] flat list. */
export function triangulate2(outer2, holes2) {
  return THREE.ShapeUtils.triangulateShape(outer2.map(p => new THREE.Vector2(p[0], p[1])),
    (holes2 || []).map(h => h.map(p => new THREE.Vector2(p[0], p[1]))));
}

/* ------------------------------------------------------------ instanced prisms */
const PRISM_VS = `
attribute vec3 iX;
attribute vec3 iY;
attribute vec3 iZ;
attribute vec3 iT;
attribute vec3 iC;
#ifdef DEF
attribute float aSt;
attribute vec3 iD0;
attribute vec3 iD1;
attribute vec3 iD2;
attribute vec3 iD3;
attribute vec3 iD4;
uniform float uDisp;
#endif
uniform vec3 uLight;
uniform float uBias;
#ifdef EDGE
varying vec4 vCol;
uniform vec4 uEdge;
#else
flat out vec3 vC;
#endif
void main() {
  mat3 M = mat3(iX, iY, iZ);
  vec3 w = iT + M * position;
#ifdef DEF
  vec3 dd = aSt < 0.5 ? iD0 : aSt < 1.5 ? iD1 : aSt < 2.5 ? iD2 : aSt < 3.5 ? iD3 : iD4;
  w += uDisp * dd;
#endif
  vec4 pos = projectionMatrix * modelViewMatrix * vec4(w, 1.0);
  pos.z -= uBias * pos.w;
  gl_Position = pos;
#ifdef EDGE
  vCol = uEdge;
#else
  vec3 n = normalize(transpose(inverse(M)) * normal);
  float d = dot(n, uLight);
  float s = d > 0.0 ? d : 0.12 * -d;
  float q = min(15.0, floor(s * 15.0 + 0.5));
  float f = 0.40 + 0.72 * q / 15.0;
  vC = min(vec3(1.0), floor(iC * 255.0 * f + 0.5) / 255.0);
#endif
}`;
const PRISM_FS = `
uniform float uAlpha;
#ifdef EDGE
varying vec4 vCol;
#else
flat in vec3 vC;
#endif
void main() {
#ifdef EDGE
  gl_FragColor = vCol;
#else
  gl_FragColor = vec4(vC, uAlpha);
#endif
}`;

export function makePrismMat(opts = {}) {
  const defines = {};
  if (opts.def) defines.DEF = 1;
  if (opts.edge) defines.EDGE = 1;
  const uniforms = { uLight: { value: new THREE.Vector3(0, 0, 1) }, uAlpha: { value: 1 }, uDisp: { value: 0 },
    uBias: { value: opts.edge ? 2e-5 : 0 }, uEdge: { value: new THREE.Vector4(8 / 255, 12 / 255, 18 / 255, 0.22) } };
  return new THREE.ShaderMaterial({
    vertexShader: PRISM_VS, fragmentShader: PRISM_FS, uniforms, defines,
    transparent: !!opts.edge, depthWrite: !opts.edge, side: THREE.DoubleSide,
  });
}

/** Unit prism of a profile {pts:[[y,z]...] CCW, holes, smooth} along s ∈ stations
    (default [0, 1]): sides with outward normals, caps triangulated with holes,
    hole walls facing the hole. Returns {mesh: BufferGeometry, edges: BufferGeometry}. */
export function unitPrism(prof, stations = [0, 1]) {
  const P = [], N = [], S = [];
  const K = stations.length;
  const ring = (loop, inward) => {
    const n = loop.length;
    for (let k = 0; k < K - 1; k++) for (let e = 0; e < n; e++) {
      const a = loop[e], b = loop[(e + 1) % n];
      const ey = b[0] - a[0], ez = b[1] - a[1];
      let ny = ez, nz = -ey;                       // outward of a CCW loop
      const l = Math.hypot(ny, nz) || 1; ny /= l; nz /= l;
      if (inward) { ny = -ny; nz = -nz; }
      const s0 = stations[k], s1 = stations[k + 1];
      const v = [[s0, a[0], a[1], k], [s0, b[0], b[1], k], [s1, b[0], b[1], k + 1], [s1, a[0], a[1], k + 1]];
      for (const ix of [0, 1, 2, 0, 2, 3]) { P.push(v[ix][0], v[ix][1], v[ix][2]); N.push(0, ny, nz); S.push(v[ix][3]); }
    }
  };
  const ccwOf = p => { let a = 0; for (let i = 0, j = p.length - 1; i < p.length; j = i++) a += p[j][0] * p[i][1] - p[i][0] * p[j][1]; return a < 0 ? p.slice().reverse() : p; };
  const outer = ccwOf(prof.pts);
  const holes = (prof.holes || []).map(ccwOf);
  ring(outer, false);
  for (const h of holes) ring(h, true);
  const tris = triangulate2(outer, holes);
  const flat = [...outer, ...holes.flat()];
  for (const [cap, s, k] of [[-1, stations[0], 0], [1, stations[K - 1], K - 1]]) {
    for (const t of tris) for (const ix of t) { P.push(s, flat[ix][0], flat[ix][1]); N.push(cap, 0, 0); S.push(k); }
  }
  const mesh = new THREE.BufferGeometry();
  mesh.setAttribute("position", new THREE.Float32BufferAttribute(P, 3));
  mesh.setAttribute("normal", new THREE.Float32BufferAttribute(N, 3));
  mesh.setAttribute("aSt", new THREE.Float32BufferAttribute(S, 1));
  // edges: longitudinal (non-smooth profiles) + both end rims
  const E = [], ES = [];
  const seg = (a, b) => { E.push(...a.slice(0, 3), ...b.slice(0, 3)); ES.push(a[3], b[3]); };
  for (const loop of [outer, ...holes]) {
    const n = loop.length;
    for (let e = 0; e < n; e++) {
      const a = loop[e], b = loop[(e + 1) % n];
      seg([stations[0], a[0], a[1], 0], [stations[0], b[0], b[1], 0]);
      seg([stations[K - 1], a[0], a[1], K - 1], [stations[K - 1], b[0], b[1], K - 1]);
      if (!prof.smooth) for (let k = 0; k < K - 1; k++) seg([stations[k], a[0], a[1], k], [stations[k + 1], a[0], a[1], k + 1]);
    }
  }
  const edges = new THREE.BufferGeometry();
  edges.setAttribute("position", new THREE.Float32BufferAttribute(E, 3));
  edges.setAttribute("aSt", new THREE.Float32BufferAttribute(ES, 1));
  return { mesh, edges, tris: P.length / 9 };
}

/** Instanced prisms of one profile: solid mesh + edge lines sharing the instance buffers. */
export class PrismBatch {
  constructor(unit, n, opts = {}) {
    this.n = n;
    const mk = (geo) => {
      const g = new THREE.InstancedBufferGeometry();
      for (const [k, a] of Object.entries(geo.attributes)) g.setAttribute(k, a);
      return g;
    };
    this.gMesh = mk(unit.mesh); this.gEdge = mk(unit.edges);
    const attr = (name, size) => {
      const a = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * size), size);
      this.gMesh.setAttribute(name, a); this.gEdge.setAttribute(name, a);
      return a.array;
    };
    this.X = attr("iX", 3); this.Y = attr("iY", 3); this.Z = attr("iZ", 3); this.T = attr("iT", 3); this.C = attr("iC", 3);
    if (opts.def) this.D = [0, 1, 2, 3, 4].map(k => attr("iD" + k, 3));
    this.gMesh.instanceCount = n; this.gEdge.instanceCount = n;
    this.mat = makePrismMat({ def: opts.def });
    this.edgeMat = makePrismMat({ def: opts.def, edge: true });
    this.mesh = new THREE.Mesh(this.gMesh, this.mat);
    this.edges = new THREE.LineSegments(this.gEdge, this.edgeMat);
    this.mesh.frustumCulled = false; this.edges.frustumCulled = false;
    this.edges.renderOrder = 1;
    this.uids = new Array(n);
    this.tris = unit.tris;
  }
  set(i, X, Y, Z, T, rgb) {
    const o = i * 3;
    for (let k = 0; k < 3; k++) { this.X[o + k] = X[k]; this.Y[o + k] = Y[k]; this.Z[o + k] = Z[k]; this.T[o + k] = T[k]; this.C[o + k] = rgb[k]; }
  }
  touchColors() { this.gMesh.getAttribute("iC").needsUpdate = true; }
  dispose() { this.gMesh.dispose(); this.gEdge.dispose(); this.mat.dispose(); this.edgeMat.dispose(); }
}
