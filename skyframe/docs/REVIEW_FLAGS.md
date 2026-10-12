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
