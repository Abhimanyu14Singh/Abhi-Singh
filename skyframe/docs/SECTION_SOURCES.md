# Steel section database sources

Every steel section database ships as `skyframe/skyframe/data/steel/<id>.json`.
`skyframe.core.steel_db` loads every file in that folder (see CONTRACT
"Steel section database and parametric shapes").

Rules for the data:

* A value is either **tabulated**, meaning copied from a published table, or
  **computed exactly** from published values.
* Each file states which in its `source` field. Each record lists its derived
  keys in `computed`.
* No value is invented.

This file has one section per database.

---

## AISC — `aisc.json`

**Edition.** AISC Shapes Database v16.0, August 2023, consistent with the
*Steel Construction Manual*, 16th Edition. The file holds **all 2,299 shapes**.

| Family | Count | `type` |
|---|---:|---|
| W / M / S / HP | 289 / 16 / 28 / 22 | `I` |
| C / MC | 32 / 40 | `channel` |
| L | 137 | `angle` |
| 2L | 639 | `double_angle` |
| WT / MT / ST | 289 / 14 / 28 | `tee` |
| HSS Rect / HSS Square | 399 / 126 | `rect_tube` |
| HSS Round / Pipe | 189 / 51 | `round_tube` |

### Where the data came from

aisc.org is not reachable from the build environment. The workbook was
therefore taken from two independent open redistributions of the same file.

1. **Primary.** `data/aisc-v16.csv` in
   [github.com/cmccomb/structural-shapes](https://github.com/cmccomb/structural-shapes),
   commit `afacff69cc69fb098d8eee6a26b11022a81a5c50`, MIT licence.
   * It is an extract of the US-customary columns A:CF of
     `aisc-shapes-database-v160-2.xlsx`, sheet "Database v16.0".
   * The workbook's SHA-256 is recorded there:
     `82d0ceb96a0d938ae1a6bd9637cb10a1e269225b5d668dce5b0bdc8d86013496`.
   * It has 33 columns: designations, W, A, d, Ht, OD, bf, B, tw, tf, tnom,
     tdes, Ix, Iy, J, b, t, Sx, Sy, Zx, Zy, rx, ry, Cw, C, eo, rts, ho, Iw,
     Iz and tan(α).
2. **Supplement.** The `steelsnakes/US/data/*.json` files of the PyPI wheel
   `steelsnakes-0.0.1a11` (W. Maranga). This is a separate extract of the same
   v16.0 workbook with the full column set.
   * It is used **only** for the four columns the CSV omits: `kdes`, `x`, `y`
     and `eo`.
   * Before any supplement value is used, the builder compares every column
     the two extracts share. That is 36,301 values over all 2,299 shapes, and
     **they agree exactly**: 0 differences.

The build is reproducible and deterministic:

```
python3 tools/build_aisc_db.py --csv <structural-shapes>/data/aisc-v16.csv \
    --supplement steelsnakes-0.0.1a11-py3-none-any.whl \
    --out skyframe/data/steel/aisc.json
```

`aisc.json` has SHA-256
`1adb312bf6ed8d19a8b1feeab2fa318a9466663b4ad7740bcbe409cb7e447448`.

AISC's disclaimer applies to the data. It is published "without warranty".
It must not be relied on for a specific application without competent
professional examination. SkyFrame does analysis only and makes no design
checks.

### Units and verbatim values

* Conversion uses exact factors: 1 in = 0.0254 m, so in² = 0.0254², in⁴ =
  0.0254⁴ and in⁶ = 0.0254⁶. Mass is W [lb/ft] × 0.45359237 / 0.3048 kg/m.
* Every record keeps the published US values **verbatim** in its `aisc`
  block, so any SI number can be audited.

### Column mapping (tabulated, then converted)

| AISC | SkyFrame `props` |
|---|---|
| A | A |
| Ix / Iy | I33 / I22 (3 = major/strong, 2 = minor) |
| J | J |
| Cw | Cw |
| Sx / Sy | S33 / S22 |
| Zx / Zy | Z33 / Z22 |
| rx / ry | r33 / r22 |
| Iw / Iz (single angles) | I_major / I_minor |
| tan(α) | alpha = atan(tan α), in rad, from local 3 to the major axis |
| C, eo, rts, ho | C, eo, rts, ho |
| W | mass |

Single angles also keep the leg-parallel Ix/Iy as I33/I22.

### Computed values (listed in each record's `computed`)

* **Shear areas**, from the published dimensions:
  * I-shapes (W/M/S/HP) and channels: As2 = d·tw, As3 = 5/3·bf·tf (5/6 of
    both flanges);
  * tees: As2 = d·tw, As3 = 5/6·bf·tf;
  * angles: As2 = 5/6·d·t, As3 = 5/6·b·t;
  * double angles: twice the angle;
  * HSS rect/square: As2 = 2·Ht·tdes, As3 = 2·B·tdes;
  * HSS round and Pipe: As2 = As3 = A/2.
* **`dims.r`**, the fillet radius: kdes − tf (I, C, tee) or kdes − t (angle).
* **`dims.r_out`**: 2·tdes. This is the outside corner radius AISC uses for
  its tabulated HSS properties.
* **`dims.s`**: the 2L gap, decoded from the designation. For example,
  `…X3/4LLBB` gives 0.75 in, and no suffix gives 0.
* **2L `J`**: 2 × J of the matching single angle. v16.0 tabulates no 2L
  torsion constant, and the two angles are stitched, not fused.
* **Shear-centre offsets** `x0`/`y0`, relative to the centroid in the
  standard placement (`skyframe.core.shape_props`):
  * channel x0 = −(x + eo);
  * angle x0 = t/2 − x, y0 = t/2 − y;
  * tee and 2L y0 = y − tf/2 (or t/2).
* **Centroid** `cy` of tees and 2L: d − y.

### Dimension conventions

* **Single angles.** In AISC, `b` is the long leg, placed vertically, and `d`
  the short leg. SkyFrame stores `d` = the vertical (long) leg along local 2
  and `b` = the horizontal leg.
* **Double angles.** `d` is the back-to-back leg. `orientation` is LLBB, SLBB
  or EQUAL.
* **HSS.** `t` is the design thickness `tdes`. The nominal thickness is kept
  as `t_nom`.

### Spot checks

`tests/test_steel_db.py` checks 23 shapes across all 15 families against the
published values. That includes W14X90, W44X408, M12.5X12.4, S24X121,
HP18X204, C15X50, MC18X58, L8X6X1, L12X12X1-3/8, 2L8X6X1LLBB/SLBB, WT22X204,
MT6.25X6.2, ST12X60.5, HSS34X10X1, HSS16.000X0.625, Pipe26STD and Pipe8STD.

`tests/test_shape_props.py` compares the parametric properties computed from
the published dimensions against the tabulated values for every shape. The
tolerances are listed in CONTRACT, and the fillets explain the differences.

### Legacy library

`skyframe.core.sections_library`, the 22 W-shapes of v0.3, now reads this
database's `aisc` block. Its results are byte-identical to before, with one
documented pin. The old table had W10x49 `d = 9.98 in`, and v15.0 and v16.0
both publish 10.0 in. The old value is kept, because it sets the drawing and
panel-zone depth.

---

<!-- Other standards (eurocode.json, is808.json, bs.json, as_nzs.json): one
     "## <Standard> — `<id>.json`" section each, same structure. -->
