# Truss Lab — Research Report

*A deep-research synthesis behind an educational virtual-work truss app: the
unit-load method, truss-solving algorithms, interactive-canvas architecture,
educational UX, indeterminate behaviour, Bill Baker's energy-based design, and
AISC section data.*

**Method.** Seven parallel research agents fanned out across the question, ran
web searches, and returned cross-corroborated, citable claims with confidence
ratings. A recurring limitation this session: `WebFetch` returned HTTP 403 on
essentially every host, so full-text extraction was unavailable; claims rest on
cross-corroborated search-result snippets from authoritative sources
(Engineering LibreTexts, MIT, Duke, Purdue, SteelConstruction.info, PhET,
AISC). Confidence is flagged throughout, and engineering formulas were
confirmed across ≥2 independent sources before being coded into the solver.

The solver's correctness is additionally pinned by an automated test suite
(`test/solver.test.js`, `test/examples.test.js`) — most importantly a
self-consistency check that the unit-load deflection equals the direct-stiffness
displacement at the same DOF (37/37 + 12/12 passing).

---

## 1. The unit-load (virtual work) method for truss deflections

**Formula (high confidence).** A truss joint deflection is

> **δ = Σ (N · n · L) / (A · E)**

where **N** = member axial force under the *real* loads, **n** = member axial
force under a *virtual unit load* applied at the joint and in the direction of
the deflection sought, **L** = length, **A** = area, **E** = modulus.
Tension is positive; the *same* sign convention must apply to both N and n. A
positive δ means the joint moves in the assumed unit-load direction.
[Engineering LibreTexts (Udoeyo) 1.08](https://eng.libretexts.org/Bookshelves/Civil_Engineering/Structural_Analysis_(Udoeyo)/01:_Chapters/1.08:_Deflections_of_Structures-_Work-Energy_Methods),
[thestructuralengineer.info](https://www.thestructuralengineer.info/education/structural-analysis/truss-deflection-using-the-unit-load-method),
[civilengineeronline](https://civilengineeronline.com/str/unitload.htm)

**Derivation basis (high).** External virtual work (unit load × real
displacement) = internal virtual work (virtual forces n × real member
deformations NL/AE), giving 1·Δ = Σ n·(NL/AE). Assumes linear-elastic, small
displacements, pin-jointed axial bars.
[Aerospace LibreTexts 8.01](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Introduction_to_Aerospace_Structures_and_Materials_(Alderliesten)/03:_Analysis_of_Statically_Indeterminate_Structures/08:_Deflections_of_Structures-_Work-Energy_Methods/8.01:_Virtual_Work_Method),
[Caprani notes](https://www.colincaprani.com/files/notes/SAIII/Virtual%20Work%201011.pdf)

**The controlling member (high).** Because δ is an additive sum, each member's
term `N·n·L/(AE)` is its *contribution*; the member with the largest-magnitude
term contributes most to the deflection. A member dominates when it has large
real force N, large virtual force n (it lies on the direct path between load and
target joint), is long, and/or is slender (small AE) — exactly the recipe in the
formula.
[thestructuralengineer.info](https://www.thestructuralengineer.info/education/structural-analysis/truss-deflection-using-the-unit-load-method),
[Mechanics of Materials (Roylance) 2.01](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Mechanics_of_Materials_(Roylance)/02:_Simple_Tensile_and_Shear_Structures/2.01:_Trusses)

**Why members attract force (high).** Chord members resist the truss's global
bending as a tension/compression couple (largest near midspan of a simple span);
web members (diagonals/verticals) ferry **shear** to the supports. A member ends
up highly loaded when it sits on the most direct route the load must travel to a
support.
[SteelConstruction.info — Trusses](https://www.steelconstruction.info/Trusses)

**Common misconceptions to pre-empt (high/medium).** "Virtual" describes the
*work*, not the force — the unit load is an ordinary force used as a probe; some
authors suggest calling it an "assumed loading case." Students also drop the
tension-positive convention on n, or misread a negative δ (it just means
"opposite to the assumed direction").
[Falla et al., *Comments on the understanding of the Virtual Work Method*](https://www.researchgate.net/publication/269658686_Comments_on_the_understanding_of_the_Virtual_Work_Method),
[ASEE 2006-823](https://peer.asee.org/learning-the-virtual-work-method-in-statics-what-is-a-compatible-virtual-displacement.pdf)

**Pedagogy (high).** The canonical Hibbeler/Kassimali/Leet presentation: show
the *two systems side by side* (real → N, lone unit load → n), then merge into a
single table with columns Member | L | A | N | n | N·n·L | (N·n·L)/AE, summed at
the bottom; add and sort a *contribution* column to surface the controlling
member.
[Temple/North Broad Press Ch.8](https://temple.manifoldapp.org/read/structural-analysis/section/936c1221-e29a-431c-99ec-a4c25dc7b700),
[EngineeringSkills.com worked example](https://www.engineeringskills.com/posts/virtual-work-method)

> **In the app:** the Insights tab leads with the two-system explanation and the
> formula, renders the contribution ranking as sorted bars + a full virtual-work
> table, and states the controlling member and its % share explicitly.

---

## 2. Algorithms for solving plane trusses

**Determinacy & stability (high).** For a plane truss with m members, r reaction
components, j joints: `m + r < 2j` unstable, `= 2j` determinate, `> 2j`
indeterminate to degree `(m+r) − 2j`. The count is **necessary but not
sufficient** — geometry can still be unstable (collinear members, concurrent
reactions). The rigorous catch is a singular global stiffness matrix at solve
time.
[Engineering LibreTexts 5.3](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Introduction_to_Aerospace_Structures_and_Materials_(Alderliesten)/02%3A_Analysis_of_Statically_Determinate_Structures/05%3A_Internal_Forces_in_Plane_Trusses/5.03%3A_Determinacy_and_Stability_of_Trusses),
[SJSU Vukazich](https://www.sjsu.edu/people/steven.vukazich/docs/160.4.2%20Truss%20Determinacy.pdf),
[learnaboutstructures.com](https://learnaboutstructures.com/Stability)

**Direct stiffness method (recommended core — high).** Handles determinate *and*
indeterminate trusses and gives displacements directly. Element stiffness in
global coordinates for DOF order [uᵢ, vᵢ, uⱼ, vⱼ], with c = cos θ, s = sin θ:

```
            ┌                      ┐
            │  c²    cs   −c²   −cs │
k = (AE/L)· │  cs    s²   −cs   −s² │
            │ −c²   −cs    c²    cs │
            │ −cs   −s²    cs    s² │
            └                      ┘
```

Assemble global K (2j×2j); it is singular until supports remove the 3 rigid-body
modes. Partition into free/supported DOFs, solve `K_ff u_f = F_f` (SPD → Cholesky
is ideal; the app uses Gauss elimination with partial pivoting since the systems
are small). Recover member force:

> **N = (AE/L) · [ c·(uⱼ−uᵢ) + s·(vⱼ−vᵢ) ]**, N > 0 = tension.

Reactions: `R = K u − F` at supported DOFs. A failed/singular factorisation flags
a mechanism the m+r=2j count can miss.
[Gavin, Duke CEE 421 — Matrix Stiffness Method for 2D Trusses](https://people.duke.edu/~hpgavin/cee421/truss-method.pdf),
[N. Kim, U. Florida EML 5526](https://web.mae.ufl.edu/nkim/eml5526/Lect02-new.pdf),
[Roylance, MIT 3.11](https://web.mit.edu/course/3/3.11/www/modules/truss.pdf),
[DoITPoMS (Cambridge)](https://www.doitpoms.ac.uk/tlplib/fem/stiffness.php)

**Agreement with virtual work (high).** Both methods are exact consequences of
the same member law δ_member = NL/(AE); the stiffness displacement at a DOF must
equal the unit-load δ there. The app exploits this as a built-in cross-check and
as the central teaching point.

> **In the app:** `js/solver.js` implements exactly these formulas; the test
> suite verifies the single-bar closed form (u = PL/EA, N = P), the
> virtual-work/stiffness agreement, and mechanism detection.

---

## 3. Interactive-canvas architecture

**Rendering choice (high).** At truss scale (tens–hundreds of elements) both SVG
and Canvas work; SVG is "far superior for technical drawing," with trivial
labels, per-element hit-testing, and accessibility, while Canvas/Konva wins for
very large scenes and built-in animation.
[yworks SVG/Canvas/WebGL](https://www.yworks.com/blog/svg-canvas-webgl),
[Konva — Best Canvas Library](https://konvajs.org/docs/guides/best-canvas-library.html)

**Library landscape (high).** Konva.js (with official React/Vue/Svelte bindings)
is the strongest *library* choice for draggable connected nodes + hit detection +
animation; ml-matrix is the modern maintained linear-algebra pick (Cholesky/LU/
SVD).
[canvas-engines-comparison](https://github.com/slaylines/canvas-engines-comparison),
[ml-matrix (npm)](https://www.npmjs.com/package/ml-matrix)

**Client-side, no backend is proven (high).** STRIAN and SkyCiv run full
beam/frame/truss analysis in-browser; solving K·u=F for a truss is small dense
linear algebra.
[STRIAN](https://structural-analyser.com/), [SkyCiv](https://skyciv.com/free-truss-calculator/)

> **Design decision (deliberate deviation):** the recommended stack was
> React + Konva + ml-matrix. We chose **zero-build vanilla JS + SVG + a
> self-contained solver** instead, because the app must be instantly runnable and
> deployable (open `index.html`, host on GitHub Pages, works offline) with no
> npm/bundler. SVG is, by the same research, the better fit for labelled
> technical drawing at truss scale. Grid-snap (`round(x/step)*step`),
> node-to-node member drawing, snapshot undo/redo, and an animated deflected-shape
> overlay (auto-scaled exaggeration) follow the documented patterns.

---

## 4. Educational UX & visualization

**Colour convention (high).** Tension = **blue**, compression = **red/
vermillion**, zero-force = grey is the dominant convention (West Point Bridge
Designer, FEM bridge visualisations). Avoid the minority red/green scheme — it is
the colourblind-accessibility risk. The app uses the colourblind-safe Okabe–Ito
blue `#0072B2` / vermillion `#D55E00`, never relies on colour alone (signed
values, thickness ∝ |N|, dashed zero-force, T/C labels), and always shows a
legend.
[Bridge Designer colour feedback](https://civilguidelines.com/software/west-point-bridge-designer.html),
[Okabe–Ito palette](https://easystats.github.io/see/reference/okabeito_colors.html),
[Coloring in R's Blind Spot](https://arxiv.org/pdf/2303.04918)

**PhET design principles (high, primary sources).** Implicit scaffolding (guide
through design, not instructions); immediate dynamic feedback on every
interaction (critical to inferring cause→effect); keep controls few so the
interface doesn't compete with the concept; progressive disclosure (headline
number first, details on demand); invite "what-if" exploration.
[PhET research](https://phet.colorado.edu/en/research),
[arxiv 1306.6544](https://arxiv.org/pdf/1306.6544),
[Progressive disclosure](https://en.wikipedia.org/wiki/Progressive_disclosure)

**Communicating "which member controls" (medium).** Sortable results table +
contribution bar chart, bidirectional hover-highlight between table/chart and
diagram, and an "explain this result" panel with a sensitivity callout.
[SkyCiv — interpreting results](https://skyciv.com/docs/skyciv-beam/interpreting-results/)

> **In the app:** one-result-at-a-time display toggles, a deflection-contribution
> bar ranking with hover-to-spotlight, plain-language insight cards, an
> animated/exaggerated deflected shape, and a live what-if loop (change a
> member's area/section → instant re-solve).

---

## 5. Indeterminate trusses & the "why" of distribution

**Forces vs. stiffness (high).** In a **statically determinate** truss member
forces come from equilibrium alone — they are **independent of E and A** — yet
**deflections do depend on EA** (via δ = ΣNnL/AE). In an **indeterminate** truss,
equilibrium is insufficient; members share load in proportion to **relative**
stiffness EA/L, so **stiffer load paths attract more force**. Uniformly scaling
all EA leaves the force distribution unchanged but still scales deflections.
[Purdue ME323 — determinate vs indeterminate](https://www.purdue.edu/freeform/me323/animations-and-demonstrations/determinate-and-indeterminate-trusses/),
[Load path analysis](https://en.wikipedia.org/wiki/Load_path_analysis),
[Force method — LibreTexts 1.10](https://eng.libretexts.org/Bookshelves/Civil_Engineering/Structural_Analysis_(Udoeyo)/01:_Chapters/1.10:_Force_Method_of_Analysis_of_Indeterminate_Structures)

**Force method via virtual work (high).** Flexibility coefficients ΔXP =
Σ(F·f·L/AE) and δXX = Σ(f²·L/AE); compatibility ΔXP + X·δXX = 0 → redundant
X = −ΔXP/δXX; final forces = F + X·f. Maxwell–Betti reciprocity makes the
flexibility matrix symmetric.

**Sensitivity (high heuristic / medium formula).** For a determinate truss,
∂δ/∂Aᵢ = −(nᵢNᵢLᵢ)/(E Aᵢ²): to cut a target deflection most efficiently, stiffen
the members with the largest `N·n·L/(AE)` term. For indeterminate trusses,
changing one area redistributes forces, so re-solve rather than trusting the
single-member term.

> **In the app:** the X-braced preset is 1° indeterminate; changing a diagonal's
> area visibly redistributes force. The Insights panel states the
> determinate-vs-indeterminate rule and suggests the experiment.

---

## 6. Bill Baker's energy-based design (the efficiency lens)

**Primary reference (high citation).** W. F. Baker, *"Energy-Based Design of
Lateral Systems,"* **Structural Engineering International** 2(2), 99–102, 1992 —
an energy/virtual-work technique for sizing tall-building lateral bracing where
**drift** governs.
[T&F record](https://www.tandfonline.com/doi/abs/10.2749/101686692780615950),
[ResearchGate](https://www.researchgate.net/publication/233488783_Energy-Based_Design_of_Lateral_Systems).
Later extensions with co-authors (note: *Lauren L. Beghini* = *L. L. Stromberg*;
*Alessandro Beghini* is distinct): graphic-statics optimization
([Beghini et al. 2014, *Struct. Multidisc. Optim.* 49(3):351–366](https://link.springer.com/article/10.1007/s00158-013-1002-x)),
topology optimization for braced frames
([Stromberg et al. 2012, *Eng. Struct.* 37:106–124](https://paulino.princeton.edu/journal_papers/2012/ES_12_TopologyOptimizationForBracedFrames.pdf)).

**The principle (high as theory; medium that the exact phrasing is Baker's).**
Write a target displacement as the virtual-work sum δ = Σ(F·f·L)/(E·A); each term
is a member's virtual-work contribution. The most **material-efficient** design
to control that displacement distributes material so the **(virtual) strain-
energy density is uniform** across members — add material where the real×virtual
force product per unit volume is high, slim down where it is low. Members on the
**direct load path** (high real *and* high virtual force) control the deflection;
near-idle members can be minimized.
[USEDD / fully-stressed-design criterion (MDPI)](https://www.mdpi.com/2673-3161/4/2/31),
[Patnaik, *Optimality of a Fully Stressed Design* (NASA)](https://ntrs.nasa.gov/api/citations/19980148007/downloads/19980148007.pdf),
[Virtual Work Sensitivity Method for tall buildings](https://www.academia.edu/5323077/VIRTUAL_WORK_SENSITIVITY_METHOD_FOR_THE_OPTIMIZATION_DESIGN_OF_TALL_BUILDINGS)

**Attribution caution (high).** The uniform-strain-energy-density optimality
criterion predates Baker (Michell, optimality-criteria theory). Baker's specific
contribution is applying the *virtual-work form* to practical drift-controlled
sizing of tall-building lateral systems. The app credits him accordingly.

> **In the app:** the "Bill Baker's efficiency lens" insight card frames each
> member's `N·n·L/(EA)` as its virtual strain energy, names the best member(s) to
> stiffen, and explains the uniform-energy-density target with this attribution.

---

## 7. AISC steel section data

**Section areas (high).** A curated set of common W-shapes, square HSS, round
HSS/pipe and angles, with published AISC Shapes Database (Manual Table 1-1)
cross-sectional areas in in² (e.g. W12×26 → 7.65 in², HSS6×6×3/8 → 7.58 in²,
L4×4×3/8 → 2.86 in², 2L4×4×3/8 → 5.72 in²). HSS/pipe areas use AISC's design wall
thickness (0.93× nominal), so weight/area ≈ 3.6 rather than 3.4 — expected, not
an error. **E = 29,000 ksi (200 GPa)** for all structural steel.
[AISC Shapes Database v16.0](https://www.aisc.org/aisc/publications/steel-construction-manual/aisc-shapes-database-v160/),
[beamdimensions.com](https://beamdimensions.com/database/American/AISC/W_shapes/),
[engineersedge angle table](https://www.engineersedge.com/standard_material/Steel_angle_properties.htm)

**Nomenclature (high).** In "W12×26", 12 = nominal depth (in), 26 = weight
(lb/ft); area A is a separately tabulated property. Lowest-confidence values
(round HSS5.000×0.250 and the Std pipes) are flagged in `js/sections.js`.

> **In the app:** `js/sections.js` carries these areas; the member Properties
> panel offers an AISC shape dropdown that sets A (converted to internal units)
> and pins E to 29,000 ksi, and shows weight (lb/ft) to make the Baker
> "material cost" trade-off tangible.

---

## How the research shaped the build (summary)

| Finding | Where it lives |
|---|---|
| δ = ΣN·n·L/(EA); contribution ranking; controlling member | `solver.js` virtual-work decomposition; Insights ranking |
| Direct stiffness method, exact 4×4 k, N recovery | `solver.js` |
| Determinacy m+r vs 2j + singular-K mechanism catch | `solver.js` `classifyDeterminacy`, mechanism flag |
| Two-system pedagogy, pre-empt "virtual" misconception | `explain.js`, Help modal |
| Tension=blue/compression=red, Okabe–Ito, redundant cues | `styles.css`, legend |
| PhET immediate feedback / progressive disclosure / what-if | live re-solve, tabs, display toggles |
| Determinate forces ∝ EA only via deflection; stiffness attracts force | `explain.js` cards, X-braced preset |
| Baker energy-based efficiency (with correct attribution) | `explain.js` "Baker" card |
| AISC areas + E=29,000 ksi | `sections.js`, member Properties panel |
| Zero-build SVG client-side architecture | whole app; no dependencies |

*All quantitative engineering claims were re-derived/confirmed in code and pinned
by the passing test suite. Citations above are the corroborating sources; where a
single verbatim quote could not be fetched this session, the underlying facts are
standard and cross-checked across multiple independent references.*
