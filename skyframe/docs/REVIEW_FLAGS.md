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
- Interaction (PMM) hinges hold P at its gravity value; the M2-M3 yield contour
  is an ellipse through the 0 and 90 degree curves; no strength drop past C.
- Wall fiber hinges keep wall shells at 1e-4 stiffness; loads on interior wall
  nodes go into that soft shell. RC shear hinges are force-controlled.
- Caltrans and TBI hinge tables not implemented.

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

## Wave C: regional codes, spectra and libraries (values entered from memory)
No copy of these standards was reachable from the build environment. Every
value below was typed from memory and must be checked against the standard
before it is relied on. Each one can be overridden by an explicit parameter.
- **EC8 national annexes** (`core/seismic_codes_wavec.EC8_NA`, used by the
  `ec8_na` generator and spectrum):
  - UK, Italy, Greece and Spain store the EN 1998-1 recommended S / TB / TC / TD.
    The Spanish values are a placeholder; NCSE-02 governs in Spain.
  - Germany: the DIN 4149 subsoil-class table (A-R ... C-S).
  - France: the national Type 2 table and the agR zone values 0.4 / 0.7 / 1.1 /
    1.6 / 3.0 m/s².
  - Portugal: the Type 1 / Type 2 Smax, TB, TC, TD values, the ag-dependent S
    rule and the importance factors 0.65 / 1.0 / 1.45 / 1.95.
  - Romania: the P100-1/2013 control-period zones TC = 0.7 / 1.0 / 1.6 s, with
    TB = 0.2 TC and TD = 3 / 3 / 2 s.
  - All annexes: beta = 0.2 and the importance-factor lists.
- **Legacy IBC / UBC**:
  - ASCE 7-05/7-10 Tables 11.4-1/2 (Fa, Fv) and Table 12.8-2 (Ct, x).
  - The edition rule for the minimum Cs: 0.01 for IBC 2006 / ASCE 7-05 as first
    published, and 0.044 SDS Ie for IBC 2009 (ASCE 7-05 Supplement 2) and IBC
    2012.
  - UBC 97 Tables 16-Q / 16-R (Ca, Cv) and the Ct values.
- **Italian NTC 2018**:
  - Table 3.2.IV SS / CC expressions, Table 3.2.V ST, TD = 4 ag + 1.6, the
    design floor 0.2 ag and C1 0.085 / 0.075 / 0.050.
  - Wind: Table 3.3.I zones (vb0, a0, ks), Table 3.3.II categories (kr, z0, zmin),
    cr(TR), and the wall cp of +0.8 / -0.4.
- **BS 6399-2**:
  - Table 5 front / rear Cpe (+0.8 to +0.6 and -0.3 to -0.1 over D/H = 1 to 4)
    and the 0.85 divergence factor for H/D <= 1.
  - Sb (Table 4) is not tabulated in SkyFrame; the user enters it.
- **API 4F**: F = 0.00338 Ki V² Cs A, the 1/8 elevation exponent, z_ref = 10 m
  and the Cs default of 1.5.
- **Code spectra**:
  - KDS 41 17 00 TL = 5 s.
  - NSCP 2015 response spectrum shape (UBC form: Ts = Cv / 2.5 Ca, T0 = 0.2 Ts).
  - The TSC / NSCP / NTC-CDMX coefficient tables reuse the existing generators'
    tables, which are already flagged above.
- **USGS design-maps lookup**: the response key names (`ss`, `s1`, `sds`, `sd1`,
  `tl`, `pgam`, `multiPeriodDesignSpectrum{periods, ordinates}`) are from memory.
  The service was unreachable here (proxy 403), so only a mocked response was
  tested.
- **Material libraries** (`data/materials/{gb,csa,jis,ks,gost}.json`, all
  records carry `from_memory`):
  - GB: GB/T 1591-2018 / GB/T 700 grades, GB 50010 fck and Ec tables, and
    HPB / HRB rebar.
  - CSA: G40.21 260W / 300W / 350W, Ec = 4500 sqrt(f'c), and G30.18 rebar.
  - JIS: SS400 / SN / SM / STKR / BCR F-values and fu, the AIJ concrete E
    formula and unit weights, and SD rebar.
  - KS: SS / SM / SN 275-460 grades, KDS Ec = 8500 cbrt(fcm) with the delta-f
    rule, and SD400-600.
  - GOST / SP: C245-C390 Ryn / Run, SP 63 Rb,n and Eb tables, and A240 / A400 /
    A500 with the tensile strengths 373 / 590 / 600 MPa.
  - Concrete `fc`: GB uses fck (axial) and GOST uses Rb,n (prism). Neither is a
    cylinder strength.
- **Not from memory (sourced)**:
  - NBCC 2020 Table 4.1.8.11 (Mv / J), the 4.1.8.11 rules and Table 4.1.8.4.-C
    were transcribed from the Ontario 2024 Building Code Compendium (O. Reg.
    163/24) as published in negarghasemi/tmp-bc-kb-2026-05-26 @3abd8ad. That
    text reproduces the NBCC 2020 provisions. It is not checked against the
    NBCC 2020 print.
  - The steel tables are sourced (docs/SECTION_SOURCES.md, "Wave C").
- **Steel table licences and gaps**:
  - The JIS table (JFE handbook CSV, z-ichinohe/jfe_binran_csv) and the KS
    table (hnanmal/mQ Revit type catalogue) come from repositories that state
    no licence. Only factual dimension and property values were taken. Review
    this before redistributing.
  - GB/T 11263 H-shapes are **not included**. No reliable open transcription
    was found: the only candidates were unlicensed or contradicted the
    standard, e.g. HW100x100 Ix 406 against 378 cm⁴.
  - In 21 CISC rows the source gives no root radius, so the AISC twin is not
    matched and dims.r = 0. Their exact-outline gate is 3 % on A.
  - CISC `mass` is the designation mass, which is rounded (up to 3 % from
    A x 7850).
