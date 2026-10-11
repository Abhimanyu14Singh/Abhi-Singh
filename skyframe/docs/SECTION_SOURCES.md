# Steel section database sources

## Eurocode, IS 808, BS and AS/NZS tables

Files: `skyframe/skyframe/data/steel/eurocode.json`, `is808.json`, `bs.json`,
`as_nzs.json` (format: the shared v1.21 spec, "Steel section database files").
Validated by `skyframe/tests/test_steel_sections_data.py`.

Contents:

| File | Shapes | Families (count) |
|---|---|---|
| `eurocode.json` | 1202 | IPE 23 (incl. IPE 750 x 134...220), IPEA 18, IPEAA 9, IPEO 13, IPEV 5, HEAA 24, HEA 24, HEB 24, HEM 24, UPN 18, UPE 14, L equal 194, L unequal 63, CHS 162, RHS 125, SHS 123, CFCHS 106, CFRHS 137, CFSHS 96 |
| `is808.json` | 723 | ISMB 14, ISWB 14, ISHB 17, ISLB 21, ISJB 4, ISSC 8, ISMC 20, ISMPC 20, ISLC 15, ISJC 5, NPB 70, WPB 118, PBP 29, ISA equal 91, ISA unequal 108, SHS 62, RHS 26, CHS 81 |
| `bs.json` | 1354 | UB 107, UC 46, UBP 17, PFC 16, L equal 42, L unequal 39, TUB 68, TUC 22, CHS 168, RHS 312, SHS 178, CFCHS 106, CFRHS 137, CFSHS 96 |
| `as_nzs.json` | 435 | UB 35, UC 16, WB 23, WC 18, TFB 2, PFC 10, EA 46, UA 19, CHS 84, RHS 90, SHS 92 |

### How the files were produced

1. **Published tables only.** Every shape and every designation comes from a published
   section table. The tables were taken from open redistributions on GitHub and PyPI
   (listed below with commit or version, file, and licence), because the manufacturer
   sites are not reachable from the build environment. No designation was invented and
   no value was interpolated.
2. **SI conversion.** Values were converted from the source's printed units (mm, cm,
   10^3/10^6 mm^n, dm^6) to m, m^2, m^3, m^4, m^6 and kg/m with exact factors. They are
   stored to 6 significant figures, which is more than any source prints.
3. **Computed values.** A spec key that the source does not print was computed from the
   published dimensions and appears in that shape's `computed` list. The methods:
   * **A, I, elastic and plastic moduli** (angles: Z33/Z22; Blue Book UC-tees: A): exact
     integration of the true outline. The outline includes root radii, toe radii and
     hollow-section corner radii, each as a circular arc. Plastic moduli use the
     equal-area axis.
   * **J (torsion constant):**
     * rolled I: El Darwish & Johnston (1965), the formula SCI P363 uses;
     * tees: the SCI P363 tee formula;
     * RHS/SHS: the EN 10210-2 / EN 10219-2 Annex formula
       `It = t^3 h/3 + 2 K Ah`;
     * CHS: `J = 2I`.
   * **Cw (warping constant):**
     * I: `Iz (h - tf)^2 / 4`;
     * channel: thin-walled formula (Bleich);
     * angle: `t^3 (b'^3 + d'^3) / 36`;
     * tee: `bf^3 tf^3/144 + d'^3 tw^3/36`;
     * closed sections: 0.
   * **Shear areas** (the SkyFrame convention already used by `core/modifiers.py`):
     * I/channel: `As2 = d*tw`, `As3 = 5/3*bf*tf`;
     * tee: `5/6*bf*tf`;
     * angle: `5/6*d*t`, `5/6*b*t`;
     * RHS: `2*d*t`, `2*b*t`;
     * CHS: `A/2`.
   * **Shear centre** `x0/y0` (shear centre minus centroid along local 3/2):
     * channels: published where printed (AM `ym`, ASI `Xo`). Otherwise the thin-walled
       shear-centre distance (Blue Book PFC: the published `e0` from the web centreline),
       measured from the published or exact centroid;
     * angles: leg-centreline intersection;
     * tees: flange mid-thickness.
   * **alpha** (angles): from the exact outline. Its tangent was checked against the
     published `tan(alpha)` wherever one is printed.
   * **r33/r22**: computed as `sqrt(I/A)` only where the printed radius contradicts the
     printed I and A by more than 2 % (listed under corrections).
4. **Validation at build time** (all numbers are maxima over the file):
   * *Exact-geometry gate.* The published A, I and Wpl were compared with the
     exact-outline values. Parallel-flange I shapes, UPE/PFC channels, angles, RHS/SHS
     and CHS agree within 2 %. The exceptions are explained under "Known deviations".
   * *Cross-source gate.* Each table was compared with an independent transcription of
     the same or an equivalent table:
     * eurocode: Orange Book vs the EN 10365 tables, ferroscale and structuralcodes.
       For I-sections, A, I and Wpl agree within 0.6 %; It and Iw differ by up to 5 %
       between catalogues. For angles, the Orange Book and EN 10056-1 rows agree within
       1.9 %, apart from the replaced L200x200x16 row.
     * bs: steelsnakes (Blue Book), structuralcodes and eurocodedesign.
       Blue Book vs Blue Book is identical. ArcelorMittal UB 610x178 and UC 254x254
       differ by up to 2.8 %, because ArcelorMittal and British Steel roll slightly
       different dimensions for those sizes.
     * as_nzs: the G300/G350/G450 tables are identical where they overlap. The CSI AusNZ
       v8 library differs by up to 4.8 % (older edition).
     * is808: no second reliable open transcription of IS 808:2021 exists, so the IS rows
       rely on the exact-geometry gate and on exact internal relations (see the IS
       section).
   * *Mass gate:* `A*7850` vs the printed mass. Worst cases: EU 3.2 % (IPE V 450,
     printed 107 kg/m for A = 132 cm^2), IS 1.0 %, BS 0.6 %, AS 1.2 %.

### Sources: `eurocode.json`

| id | Families | Source |
|---|---|---|
| `AM` | IPE80-600, HEA, HEB, HEM | EN 10365:2017 section tables as transcribed in **domthom21/eurocodedesign** @ `1b57c8d0f0f90adf358b55fc2eef28a8cfd1358d` (MPL-2.0), `eurocodedesign/geometry/steelsections/_data/{ipe,hea,heb,hem}_en10365_2017.csv`. A, I, Wel and Wpl are identical to the ArcelorMittal sales programme (checked against nedimperva/ferroscale @ `1f7326f`, "ArcelorMittal Sections and Merchant Bars, Sales programme 2024-1", https://sections.arcelormittal.com/repository2/Sections/Sections_MB_ArcelorMittal_FR_EN_DE.pdf). |
| `OB` | IPE A/AA/O/V, IPE 750, HE AA, UPN, L equal, L unequal | ArcelorMittal Europe "Orange Book" (EN 1993 properties, https://orangebook.arcelormittal.com) as transcribed in **waynemaranga/steelsnakes** @ `78744cd52948c6ed0aedb6b434ec287c439e78bd` (GPL-2.0; PyPI `steelsnakes` 0.0.1a11), `src/steelsnakes/EU/data/{IPE,HE,UPN,L_EQUAL,L_UNEQUAL}.json`. |
| `SC-UPE` | UPE | ArcelorMittal UPE table (EN 10365:2017) as transcribed in **fib-international/structuralcodes** @ `3e9c3f5cffb0c28e083257346006c7eac02384fd` (Apache-2.0), `tests/test_geometry/test_profiles/upe.json`. Cross-checked against the Orange Book UPE table. |
| `ECD-L` | 33 EN 10056-1 angle sizes that the Orange Book does not list, plus the L200x200x16 replacement | EN 10056-1:2017 tables as transcribed in eurocodedesign (as above), `L_en10056_2017.csv`. Cross-checked against structuralcodes `l.json`/`li.json`. |
| `HF` | CHS, RHS, SHS | EN 10210-2 hot-finished hollow sections (ro = 1.5t, ri = t) as transcribed in **eurocodepy 2027.0.8** (PyPI, GPL-3.0; https://github.com/pcachim/eurocodepy), `eurocodepy/data/{chs,rhs,shs}_profiles_euro.json`. |
| `CF` | CFCHS, CFRHS, CFSHS | EN 10219-2 cold-formed hollow sections (ro = 2t / 2.5t / 3t for t <= 6 / <= 10 / > 10 mm, ri = ro - t), from the SCI Blue Book tables (see `bs.json`, gazelle `cf-*.json`). |

Corrections and decisions:
* **L200x200x16:** the Orange Book transcription prints I = 2430 cm^4 and Iu = 3740 cm^4.
  The exact geometry gives 2341 / 3723, and three other tables print 2340 / 3720. The
  row failed the 2 % geometry gate, so the EN 10056-1 row (`ECD-L`) is used instead.
* **L200x150x12:** eurocodedesign lists it as "L200x15x12", with b = 15 in the dims
  column. Its A, I, Iu, Iv and mass reproduce the exact geometry of 200x150x12, so it is
  published as L200x150x12.
* **Orange Book Iw (I-sections):** where a value is printed to fewer than 3 significant
  figures (e.g. IPE AA 80: 9e-05 dm^6), the I-section value `Iz (h-tf)^2/4` is used
  (marked computed). The build asserts that it rounds to the printed value.
* **UPN50, UPN65 and UPN120:** Iw is printed to 1 significant figure and is kept as
  printed. The thin-walled formula is not substituted, because it over-estimates
  taper-flange channels.
* **Radii of gyration:** printed `iz` values of L200x100x14, L130x90x14 and L110x70x10
  contradict the printed Iz and A by 2.1 %. They are recomputed as `sqrt(I/A)`.

Known deviations (kept as published):
* UPE330-400 `Wpl,z`: the ArcelorMittal and Orange Book tables agree with each other but
  are 3-13 % below the exact equal-area plastic modulus.
* L300x300 Orange Book rows: about 2 % below the exact geometry for the printed radii.
* IPE V 450: printed mass 107 kg/m.
* L110x110x14: printed mass 23.3 kg/m (A*7850 = 22.8 kg/m).

### Sources: `bs.json`

| id | Families | Source |
|---|---|---|
| `BB` | all | SCI/BCSA "Steel Building Design: Design Data" (**Blue Book**, SCI P363, Eurocode / UK NA edition; https://www.steelforlifebluebook.co.uk): UB, UC, UBP and PFC (BS EN 10365 / BS 4-1); equal and unequal angles (BS EN 10056-1); tees split from UB and UC; hot-finished CHS/RHS/SHS (BS EN 10210-2) and cold-formed CHS/RHS/SHS (BS EN 10219-2). Transcribed in **jsbayley/gazelle** @ `eff4332da8c76a4e345a5fb02fceaef3ffb56fed` (AGPL-3.0), `src/.d/bluebook/properties/*.json`. Cross-checked against steelsnakes `src/steelsnakes/UK/data/*.json` (same Blue Book). The structuralcodes `ub.json` and eurocodedesign `ub/uc` tables (ArcelorMittal) are also used as cross-checks. |

Notes:
* The hot-finished hollow sections include the Blue Book's non-standard sizes; elliptical
  sections (EHS) are not included.
* Hollow-section `r_out`/`r_in` are the standards' calculation radii. They reproduce the
  tabulated A within 0.6 %.
* **UC-tee (TUC) rows:** the transcription has no A and no IT. A comes from the exact
  outline (it matches the printed mass within 0.1 %). J comes from the SCI P363 tee
  formula, which reproduces all 68 published UB-tee IT values within 0.5 %.
* **Tees:** I33 is about the axis parallel to the flange, so I33 < I22 for wide UC tees.
  `cut_from` gives the parent section.

### Sources: `is808.json`

| id | Families | Source |
|---|---|---|
| `OSDAG-808` | ISMB (MB), ISWB (WB), ISHB (HB), ISLB (LB incl. LB(P)), ISJB (JB), ISSC (SC), ISMC (MC), ISMPC (MPC), ISLC (LC incl. LC(P)), ISJC (JC), NPB, WPB, PBP, ISA equal, ISA unequal | **IS 808:2021** (4th revision; rows tagged `IS808_Rev`) as transcribed in the **Osdag** database (FOSSEE, IIT Bombay; LGPL-3.0), **osdag-admin/Osdag** @ `fd1a7d884d0e2d1c7cf64362418b875bec50fdc8`, `src/osdag/data/ResourceFiles/Database/Intg_osdag.sql`, tables `Beams`, `Columns`, `Channels`, `EqualAngle` and `UnequalAngle`. |
| `OSDAG-4923` | SHS, RHS | **IS 4923:1997**, same file, tables `SHS` and `RHS`. |
| `OSDAG-1161` | CHS | **IS 1161:2014**, same file, table `CHS` (`nominal_bore` kept). |

Naming: designations are printed as in IS 808:2021 (`MB 300`, `WB 600`, `MC 300`, ...).
The legacy `IS` prefix is used only in the family labels. Two exceptions:
* **Angles** are named `ISA <a>x<b>x<t>`, because the standard's `∠` symbol is not
  searchable.
* **Repeated designations** (IS 808 lists several weights under one designation) get the
  printed mass appended, e.g. `WB 600 (145.06 kg/m)` and `HB 150* (30.15 kg/m)`.

The values are those of IS 808:2021, not IS 808:1989. For example MB 300 is 46.02 kg/m
in the 2021 edition; the 1989 edition's ISMB 300 is 44.2 kg/m.

Corrections and exclusions:
* **Dropped rows:**
  * WPB 280x280x284.13: the printed A = 362 cm^2 contradicts its printed dims (131 cm^2).
  * WPB 340x300x290.64, WPB 360x300x91.04, WPB 360x300x125.81 and WPB 360x300x163.0: the
    mass embedded in the designation contradicts the row's own A and dims.
  * SC 150* is the only `IS808_Old` row.
  * UB/UC rows carried by Osdag: these BS 4 shapes live in `bs.json`.
* **Exact-geometry repairs**, applied only where the outline is fully defined (CHS; SHS
  and RHS with the verified radii). Each printed value differs from the exact value by
  more than 1 % (0.5 % for CHS) and by more than the rounding half-unit:
  * SHS 75x75x3.2: Wel
  * SHS 180x180x5.0: I, Wel
  * SHS 180x180x6.0: Wpl (printed 280 vs exact 264.4 cm^3)
  * CHS 76.1x2.9: A (printed 0.67 vs 6.67 cm^2)
  * CHS 88.9x4: I
  * CHS 101.6x4: Wel (printed 18.8 vs 28.8 cm^3)
  * CHS 165.1x5.9: I
* **Radii of gyration:** for SHS 38x38x3.2, RHS 96x48x4.0 and RHS 122x61x3.6, the printed
  r contradicts the printed I and A, so r is recomputed as `sqrt(I/A)`.
* **MC 175\*:** the printed mass (22.7 kg/m) contradicts A = 27.3 cm^2 and is replaced
  by `A*7850`.
* **OCR artefacts:** the letter `I` stands for the digit `1` in `270.I6` (CHS Z) and
  `113.5 x I13.5` (SHS designation).
* **SHS/RHS dims:** the Osdag D/B columns are truncated to whole mm (49.5 -> 49), so the
  dims are taken from the designation and confirmed by the exact A/I check.
* **SHS/RHS J and corner radii:** IS 4923 prints no torsion constant. J is computed with
  ro = 2t and ri = t, which reproduce the tabulated A and I to the printed precision.
* **WPB rows without Iw:** Iw is computed as `Iz (h - tf)^2 / 4`.
* **Osdag `Alpha` column:** its meaning is undocumented, so it is not used. `alpha` is
  computed from the exact outline.
* **Geometry gate for the remaining IS rows:** taper-flange I and channel rows are
  checked through exact internal relations instead (`S = I/c` and `r = sqrt(I/A)`, both
  within 2 %). MPC rows are kept as published: their Iy is 1-4 % below the parallel-flange
  outline, which omits the printed toe radius.

Not available: **ISNT tees**, which are not in any open IS 808 transcription that could
be verified, and the **IS 808:1989** (3rd revision) tables. The open transcriptions found
for the 1989 tables are inconsistent; for example, ISMB 100 Iy is printed as both 40.8
and 49.5 cm^4.

### Sources: `as_nzs.json`

| id | Families | Source |
|---|---|---|
| `ASI` | UB, UC, PFC, EA, UA (AS/NZS 3679.1); WB, WC (AS/NZS 3679.2); CHS, RHS, SHS (AS/NZS 1163) | ASI / InfraBuild (OneSteel) design-capacity-table section properties as transcribed in **daveh07/steel_tables_cli** @ `bf69739d1556c0aaee55e428620280ab76069f22` (MIT), `data/{UB,UC,WB,WC,PFC,EA,UA,RHS,SHS,CHS}*_PROPS.json`. The G300/G350/G400/G450 tables are merged; their properties are identical where they overlap. |
| `ASI-TFB` | TFB | OneSteel TFB table as transcribed in **jamesmaguire/site-structural-webapp** @ `ce060563d70e5ccd94b8595586341739558cd61c` (GPL-3.0), `static/steelsections.json`. |
| `SA-RO` | RHS/SHS `r_out` | AS/NZS 1163 outside corner radius per section from **Folded-Structures-Lab/steel-as** @ `3b650048a26ceb816b21431cfc8d723cadaba71e` (MIT), `src/steelas/data/AUS_hollow_sections.csv`. For 12 sizes not listed there, the AS/NZS 1163 / ASI calculation rule is used (2t for t <= 3 mm, 2.5t above). The exact A/I check confirms it within 0.7 %. |

Cross-check only (no values taken): the CSI "AusNZv8" frame-property library
(`Property Libraries/Australia-NewZealand.xml` in kangyishuvance/hand-calculations @
`757177b`).

Notes:
* **Axis and modulus naming:** ASI calls the elastic modulus `Z` and the plastic modulus
  `S`. They are stored as S (elastic) and Z (plastic) per the spec.
* **Angles:** I33/I22 are ASI's In/Ip (axes parallel to the legs). I_major/I_minor are
  ASI's Ix/Iy (principal). S33/S22 = In/(d - pB) and Ip/(b - nL) come from published
  values. Plastic moduli about the leg axes come from the exact outline.
* **Nominal vs actual thickness:** the EA/UA thickness is the actual one (e.g.
  100x100x10EA has t = 9.5 mm).

### Licences

The section properties are factual data from the publications named above. The open
transcriptions used carry these licences:
* permissive: MPL-2.0 (eurocodedesign), Apache-2.0 (structuralcodes), MIT
  (steel_tables_cli, steel-as);
* copyleft: GPL-2.0 (steelsnakes), GPL-3.0 (eurocodepy, site-structural-webapp),
  LGPL-3.0 (Osdag), AGPL-3.0 (gazelle).

Review that mix before redistributing the installer commercially. Every value can be
traced back to the primary publication through the per-shape `src` id.
