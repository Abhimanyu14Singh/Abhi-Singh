# Items flagged for the owner's review

Values or behaviours that were implemented from memory, approximated, or that
need a check against the source documents or a real third-party program.
Each entry says where it lives.

## Code data entered from memory (verify against the standard text)
- Materials (`skyframe/data/materials/*.json`): AS/NZS 3678/3679 thickness-band
  yield stresses; IS 14268 / AS strand proof-stress ratios; ASTM A416 strand
  E = 28,500 ksi; Aluminum Design Manual table refs; BS 648 concrete density;
  AISC 341 Ry values.
- Wind/seismic generators (`core/wind_codes_ext.py`, `core/seismic_codes_ext.py`):
  IS 875-3 k2 table; AS/NZS 1170.2 Mz,cat; TSC 2018 FS/F1; NSCP Ca/Cv;
  GB 50011 alpha_max/Tg. Behaviour assumptions: NZS/AS short-period plateau
  below 0.1 s; AS 1170.4 minimum kpZ = 0.08; AS/NZS 1170.2 30 m/s floor on the
  design wind speed; ASCE 7-22 topography/gust constants unchanged from 7-16;
  GB 50009 beta_z uses a linear mode shape; EN 1991 Annex B damping input
  includes aerodynamic damping; KDS Fa/Fv are user inputs.
- Design combinations (`core/combos_codes.py`): IS 800 Table 4 rows; NBCC
  companion-load note for storage areas.

## Section databases
- IS 808 file holds the 2021 edition (e.g. MB 300 = 46.02 kg/m); 1989 ISMB
  values and ISNT tees are not included (owner chose to skip).
- Licences: several open transcriptions used for the EU/IS/BS/AS steel tables
  are GPL/LGPL/AGPL (`docs/SECTION_SOURCES.md`) - review before commercial sale.
- Published values kept as printed although 1-13 % off an exact calc: UPE330-400
  Wz, Orange Book L300 angles, IS MPC minor-axis I.

## Interoperability to confirm in third-party software
- E2K export: `LINK`/`LINKASSIGN`, `SEISMIC ... STORY FX FY`, `MASSUX..MASSRZ`,
  TH `TIMESTEP`, and between-level `POINT ... dz` keywords were not confirmed
  against a real ETABS import - import one exported file into ETABS.
- E2K maps SkyFrame's default `shell` element to ETABS `ShellThin`; rectangular
  concrete sections export without shear areas (ETABS adds shear deformation).
- Grasshopper plugin (`rhino/`): compiled and unit-tested only; must be loaded in
  Rhino 8 and exercised (checklist in `rhino/README.md`).

## Engine behaviours worth knowing
- Built-up / designer exact torsion uses nu = 0 shear areas by default
  (5/6 A for a rectangle); set nu for ETABS-style values.
- Spectral matching uses a frequency-domain approximation (not the Al Atik &
  Abrahamson wavelet method); verified within +-10 %.
- Live-load reduction applies to columns and piers, not beams.
- Release springs use stiff ties at 1e6 x member stiffness (~1e-6 softening).
- Pushover performance panel (ADRS): W for ASCE 41 there is total push-direction
  mass x g (pairs with alpha1); the older performance-point endpoint uses story +
  nodal masses, so the two ASCE 41 numbers can differ slightly. FEMA 440 may
  return no performance point on strongly hardening curves (method behaviour).
  Long-period spectrum tails are scaled by ATC-40 SRV (approximation).
- Links (wave B): Wen in direct integration treats shear directions uncoupled
  (FNA couples them); a one-step linear static case on Wen is ~2 % off the
  monotonic curve (use multi-step nonlinear static). Composite damping ratios
  are applied by mode index to FNA's linearised model (exact without device
  links). Mock composite preview uses volume shares, not strain energy.

## Added in Wave B loose ends
- Mexican NTC-Sismo long-period distribution (T > Tb) is now the two-term reduced
  form (`kh1 = [1-0.5r(1-q)]Sw/Swh`, `kh2 = 0.75r(1-q)Sw/Swh^2`, `q=(Tb/T)^r`,
  r = 2) from memory of NTC-2004 8.2 - check against the 2017/2020 text.
  `long_period="linear"` restores the simple w*h rule.
- KDS 41 wind K_zr / alpha table and default gust factor G_D = 2.0; CFE wind
  alpha / delta / c by terrain category - from memory, verify.
- E2K: `PARTIALFIXITY` value format and `ANGLEA/B/C` joint-axis keywords are
  SkyFrame spellings, not verified against a real ETABS file.
- EC8 stiffness/mass "gradual change" check uses a +-25 % judgement band
  (editable); EC8 4.2.3.2(6) torsional-radius criterion reported n/a.
- Weak-story strength estimate is nominal (no reinforcement); user overrides.
- Creep (wave B): step-by-step creep applies to frames; shells get modulus
  ageing only (no creep/shrinkage strain); members with end releases creep
  axially only; step method needs user-defined stages. Tendon-element
  hyperstatic primary uses the force after friction/anchor losses.

## Hinges (Wave B) - IMPORTANT
- ASCE 41-17 / FEMA 356 / EC8-3 hinge table values were transcribed from memory
  (standards not reachable). Each table carries a `verified` flag; the
  "preliminary" flag stays on for: ASCE 41-17 steel-column acceptance fractions,
  brace multiples, all ASCE 41-23 tables (reuse 41-17 numbers), FEMA 356 tables,
  and the wall-hinge acceptance states (Table 10-19). Verify against the code
  tables before relying on acceptance-criteria outputs.
- Interaction (PMM) hinges hold P at its gravity value by default (Wave C adds
  axial source "analysis" and drop_strength "drops"); the M2-M3 yield contour
  is an ellipse through the 0 and 90 degree curves.
- Wall fiber hinges keep wall shells at 1e-4 stiffness; loads on interior wall
  nodes go into that soft shell. RC shear hinges are force-controlled.
- Caltrans and TBI hinge tables (Wave C, design/hinge_tables_c.py) are
  TRANSCRIBED FROM MEMORY and all flagged preliminary: Caltrans SDC 2.0
  expected materials (1.3 f'c >= 34.5 MPa, fye 475 MPa), phi_y = 2.1 eps_y/h,
  Mander eps_cu with rho_s = 2 rho_t, eps_su^R = 0.09, f'cc = 1.5 f'ce, the SI
  plastic hinge length 0.08L + 0.022 fye dbl >= 0.044 fye dbl, residual c = 0.2
  and IO/LS/CP = 0.25/0.75/1.0 theta_p (Caltrans itself has no IO/LS/CP); TBI
  2017 expected-strength factors 1.3 / 1.17 and the diagonal coupling-beam row
  a/b/c = 0.03/0.05/0.8, IO/LS/CP = 0.006/0.018/0.030. Verify before use.
- Wave C PMM capacity update (axial "analysis", drop_strength "drops") is an
  explicit update: the capacity lags the axial force and the drop by one
  committed step (first-yield overshoot of one step); use small steps.

## Links (Wave C)
- Coupled 6x6 links with a non-zero trace of the U-R coupling block (axial-
  torsion "screw" coupling) are rejected: OpenSees has no general-matrix
  element; the realisation by parallel springs is exact otherwise.
- multilinear_isotropic: Steel01 isotropic hardening calibrated to the ETABS
  rule at the backbone's last point only (other amplitudes ~10 % off).
- multilinear_degrading defaults (unload_beta 0.5, no damage) and the concrete
  pinching defaults (0.8 / 0.2, the hinge "concrete" values) are our choice,
  not ETABS-published values.
- HDR isolator validated against the element's own Grant elastic component and
  an energy check only - no published bearing loop was available here.
- T/C isolator per-direction friction (mu3/R3) is uncoupled per direction and
  FNA-only; tension friction uses a negative-stiffness bearing trick (verified).
- Effective damping c_eff acts in linear direct-integration TH (Elastic eta);
  it is NOT converted to modal damping for response spectrum or FNA.

## Interop / API (Wave B)
- SDNF v3 and CIS/2 (LPM6 subset) readers were tested only by round trip and
  hand-written fixtures - check one real file from your detailing package.
- `.s2k` export writes two `SKYFRAME ...` extension tables; SAP2000 should report
  them as unrecognised and continue (untested in SAP2000).
- OAPI facade: all cases/combos are selected for output until the first
  selection call (CSI starts with none); distributed moments (MyType=2) and
  RS/TH/pushover case setters are not in the facade (use REST / skyframe.client).
- Revit: IFC4 Reference View + structural view with Revit-style names; a native
  Revit add-in is a separate product (not buildable here).
- Plugins run as trusted in-process code (failures contained, not sandboxed).

## Interface tools (Wave B)
- Waffle/ribbed slabs are one equivalent shell per bay (Huber m12); they warn
  that the shell mass modifier is ignored in "weight" mass mode (mass is
  still right via the weight modifier).
- General grids are not written to E2K and cannot be the primary grid; vertex
  reshaper and dimension lines are plan-view only; shell divide/merge/chop/expand
  refuse shells with openings.


## E2K native nonlinear records (Wave C)
- Confirmed against real ETABS v16-v22 exports (public GitHub samples):
  Nonlinear Static `LOADCASE` keywords (INITCOND, LOADPAT, MODE, NLGEOMTYPE
  "PDelta"/"LargeDispl", LOADCONTROL, DISPLTYPE, MONITOREDDISPL, DISPLMAG,
  DOF, JOINT "pt" "story", RESULTSSAVED, MINSAVED, MAXSAVED), `HINGE` user
  hinges (BEHAVIOR, DOF, TYPE, HYSTYPE, labelled points "B".."E"/"-B"..,
  MOMENTSFP/ROTATIONSFP, IO/LS/CP, FiberPM2M3 FROMSECTION), `HINGEASSIGN`
  (HINGEPROP, AUTOHINGETYPE, TABLEITEM, DOF, RDISTANCE, LOCATIONTYPE),
  `HINGEOVERWRITE AUTOSUBDIVIDERELLENGTH`, `LINKPROP ... TYPE
  "DAMPER - EXPONENTIAL"` with `DOF "U1" NONLIN "Yes" K C EXP`, and
  `PDELTA LOAD "x" FACTOR f`.
- NOT confirmed (written in the established style, check by importing into
  ETABS): the SkyFrame keywords on Nonlinear Static rows (LOADDIST, DIR,
  TARGETDRIFT, K, LOADMAG, TOWER, HINGEMODE, HARDENING, DEFAULTMY, HINGEMY,
  HINGEPARAM, MONITOREDDISPL "Story"/"Roof" + STORY), the `~GRAV` auxiliary
  gravity case of a pushover, every Nonlinear Staged Construction record
  (STAGE / OPERATION / OBJTYPE / OBJNAME / AGE / LOADTYPE / LOADNAME /
  AUTOSTAGE / FINALLOADPAT), Steady State / PSD case rows (FREQRANGE,
  FREQEND, NUMFREQ, MODALFREQ, ADDFREQ, SOLUTIONMETHOD, DAMPTYPE,
  OUTPUTJOINT, PHASE) and FUNCTYPE "STEADYSTATE"/"PSD" + FREQ rows, HINGE
  BEYONDE / KELASTIC / PINCHX.. / FORCESFP-DISPSFP / "Auto" deformation SF /
  AUTOPARAM / GRAVLOADPAT / AXIALP, HINGEOVERWRITE AUTOSUBDIVIDE, the
  TABLEITEM labels written ("Steel Beams", "Concrete Columns - Shear", ...),
  all non-exponential-damper LINKPROP type names and parameter keywords
  (KV, POSTK, YIELD, FSLOW, RADIUS, OPEN, KT, MU, ALPHA1.., DISPL/FORCE
  multilinear points, HYSTYPE discriminators "Bilinear"/"Bouc-Wen"/"BRB"),
  and TEMPERATURE / TEMPERATUREGRADIENT load rows (T, TGRAD, DIR).
- ETABS ASCE 41-13 hinge tables import as the ASCE 41-17 generator (one
  warning); "Degrading" hysteresis imports as takeda; an asymmetric
  kinematic backbone imports as takeda (warning).
- A nonlinear static chain must share one geometric option in SkyFrame (one
  OpenSees domain): on import the chain takes its most nonlinear NLGEOMTYPE
  (warning). Nonlinear Static cases with MODE / ACCEL loads import as
  pushover cases; their INITCOND full-load case supplies the held gravity.
- Pre-Wave C files exported a triple pendulum as a parameterless
  `T/C FRICTION ISOLATOR`; the reader keeps that meaning when no parameter
  rows follow.
- JSON number typing: values a model keeps raw (user stage durations / ages
  / scales, nonlinear-static default_My) that arrive as ints from a browser
  round trip still travel as type-only `$SF` patches.
- DWG: no converter here (see INTEROP "DWG drawings"); DWG files are only
  detected and refused with save-as-DXF guidance.

## Wave C dynamics
- ASCE 41-17 Table 7-4 Cm values (0.9 / 0.8 / 1.0 by system, 3+ stories, Te <= 1 s) and
  FEMA 440 Eqs. 5-1 / 5-2 / 5-4 / 5-5 (lambda 0.8 / 0.2) entered from memory - verify
  (core/pushover_coeff.py).
- FNA BRB law is Menegotto-Pinto with the Steel02 R rule; it equals Steel4 on full cycles
  but differs up to ~5 % on small noisy reversals (Steel4 branch memory). BRB and the
  bilinear damper carry no basis stiffness (use Ritz with link starts for braced frames).
- NL TH from a staged case starts from the staged displacements with the full-structure
  internal forces of those displacements (staged locked-in forces are not carried).
- Eigen shift output modes are sorted by ascending frequency (ETABS lists them in the
  order found). Shell creep uses element-average strains (O(h^2) on curvature gradients)
  and is opt-in (time_dependent.shell_creep) so legacy step-staged shell results are kept.
- RS spectral damping correction uses the EN 1998-1 eta; ETABS' own adjustment rule is
  not published.
