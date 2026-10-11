# SkyFrame dialog standard

This is the single layout and behaviour standard for SkyFrame dialogs. It follows the
way ETABS builds its Define and Assign dialogs. It also covers the guided **Model
Workflow** checklist and the canvas **context menus**.

* Code: `skyframe/skyframe/api/web/js/dlgkit.js` is the kit. `dlgkit.css` holds the
  styles, and every rule in it is scoped to `.dk-dlg`.
* Migrated dialogs: `js/dlgstd_defs.js`, `js/dlgstd_cases.js` and `js/dlgstd_assign.js`.
* Routing, workflow and context menu: `js/dlgstd.js`, `js/workflow.js` and `workflow.css`.
* Hooks in shared files:
  * one import and one init line in `js/app.js`, placed after the UX pass;
  * one Help-menu line in `js/etabs.js` (`help-workflow`).
* Turning it off: `?dlg=legacy` turns the routing off, and every menu command then
  opens the previous editor again.

---

## 1. Audit of the existing dialogs (before)

An automated audit opened every Define, Assign, Analyze, Edit, Select, View and Options
command that shows a dialog. It ran in `?mock=1&dlg=legacy` with a beam and a wall
selected, and found 65 dialogs built from 55 different shells. There were **7
hand-rolled dialog shells**: `analysisdlg`, `casedlg`, `combodlg`, `assigndlg`,
`nls_ui`, `tendons` and `ux_common`, plus the static ones in `index.html`.

| Area | Finding |
|---|---|
| Button order | The UX pass had already normalised the order to **OK · Cancel · Apply**. Footer *semantics* still varied, as the rows below show. |
| Live-edit dialogs | 6 dialogs edit the model live and have only **Done**, so there is no Cancel: Section Manager ×3 (Materials / Frame / Shell), Grid / Story editor ×2 and Section Designer. A mistyped value could not be undone with Cancel. |
| List dialogs | Load Cases and Load Combinations had **OK only**, and their changes were applied immediately. |
| Other footers | 7 other footer sets: Preview·Generate·Cancel, All·Select·Cancel, Run Now·OK·Cancel, Reset·OK·Cancel·Apply, Preview·Cancel·Apply, Close, Select·Cancel. |
| Placeholder commands | 9 Assign menu commands (Frame Section, Releases, Local Axis, Rigid End Offsets, Axial Limit, Hinges, Shell Section, Wind Cp, Supports) only showed a toast pointing at the Properties panel. Explicit joint restraints had **no UI at all**. |
| Units | 184 numeric inputs. Only about 72 had a unit visible next to them; the rest showed it in a column header, in the placeholder or not at all (Replicate, Move, Shell Uniform Load, Mass multipliers). |
| Grouping | 21 of 65 dialogs had no `<fieldset>` grouping. The Section Manager used custom collapsible rows. |
| Long forms | Five dialogs scroll well past the viewport: Frame Sections (≈960 px), Nonprismatic (790), Nonlinear Static (845), Steady State / PSD (781). Rare parameters sat inline with common ones. |
| Validation | Mixed methods: a silent revert to the old value (material and section rows), a toast ("Rename failed"), or one error line at the bottom of the dialog. Errors were rarely shown next to the field. |
| Keys | Enter and Esc came from the global UX handler. "Done" dialogs ignore Enter. Each shell kept its own Esc stack. |
| Titles | Mostly Title Case. One title included its subtitle ("Section Designer polygon + rebar …"). The same object had different names in different places: "Diaphragms" and "Define Diaphragms"; "Edit Grid Data" and "Edit Story Data" for the same dialog. |
| Default OK | Most OK/Cancel dialogs already wrote only changed keys. The legacy Mass Source dialog did not: OK with defaults added `mass_source_mode: "weight"` to a model without it, so the saved model changed. |
| Navigation | Load Patterns, Static / RS / TH / Buckling cases and Functions opened a **scroll position in the Loads pane** instead of a dialog. Modify on a static case in the Load Cases list closed the list and scrolled that pane. |

---

## 2. The standard

### Anatomy (every dialog)

```
┌ [icon] Title In Title Case ────────────────────── [?] [×] ┐
│ One muted sentence: what this dialog does.   (optional)   │
│ [Tab] [Tab] [Tab]           (only when content > ~10 rows) │
│ ┌ Group legend ─────────────────────────────────────────┐ │
│ │ Label                         [   value ]  unit       │ │
│ │                               hint · Default x unit   │ │
│ │                               inline error (red)      │ │
│ └───────────────────────────────────────────────────────┘ │
│ ▸ Advanced — topic                 2 changed from default │
├───────────────────────────────────────────────────────────┤
│ note · [More…] [Legacy editor…]          OK  Cancel Apply │
└───────────────────────────────────────────────────────────┘
```

* **Title.** Use Title Case and the ETABS noun: "Define Materials", "Material Property
  Data", "Load Case Data — Response Spectrum", "Assign Frame Properties". Put no
  subtitle in the title; use the intro line for that.
* **Footer.** The commit buttons sit bottom-right in the ETABS order **OK · Cancel**,
  plus **Apply** when the dialog can preview its change live (Assign dialogs). OK is
  the accent button. Secondary actions ("Stress–Strain…", "Section Designer…",
  "Multiple Grid Systems…") go bottom-left, after the status note. A read-only dialog
  has a single **Close**.
* **Units.** Every numeric field has its unit in its own column on the right, taken from
  `units.js` (`U.label(kind)`). A dimensionless number shows **–**. Table columns put
  the unit in the header, for example "Period (s)". The model stays SI.
* **Grouping.** Fields are grouped in `<fieldset>`s with a short legend that follows the
  ETABS section names (General Data, Weight and Mass, Loads Applied, Other
  Parameters …).
* **Tabs vs Advanced.** Use tabs only when one dialog edits genuinely separate topics,
  for example Frame Assign (Section / Releases / Local Axes / End Offsets / Nonlinear)
  or Analysis Options (DOF / Modal / P-Delta / Model). Rarely used parameters go in a
  collapsed **Advanced** section. Each Advanced field shows its default ("Default
  0.05"), and the summary says how many values differ from default, so nothing is
  hidden silently.
* **Validation.** Check a field when it changes (a change event) and again on OK. The
  message appears **under the field**, in plain language, and gives the limit in the
  current units: "Must be greater than zero." / "Enter a number in kip." / "“DEAD” is
  already used — pick another name." OK stays enabled. Pressing it with an error
  switches to the right tab, opens Advanced if needed, focuses the field and shows a
  one-line summary above the footer. Cross-field rules, such as "Include at least one
  of lateral or vertical mass", appear in that same summary line.
* **Keys.**
  * **Enter** commits the field being typed in, then presses OK. The exceptions are
    textareas, buttons, tables and lists.
  * **Esc** is Cancel.
  * **Tab** stays inside the dialog (the UX focus trap).
  * In list dialogs, **↑/↓** move the selection, and double-click opens Modify/Show.
  * In tab strips, **←/→** switch tabs.

### Data semantics

* Each dialog edits a **draft**: `dkDraft(sky)` deep-clones the working model.
  * A sub-dialog (Modify/Show …) edits a copy of one item and writes it back into the
    draft on OK.
  * Only the **list's OK** commits the draft. Cancel throws the whole draft away.
* A field writes its key **only when the user changes it**. An untouched field never
  writes, not even a normalised default.
* `dkCommit` copies back only the top-level keys whose JSON differs. If nothing
  differs, it writes nothing and does not call `markDirty`. As a result:
  * **OK with defaults is byte-identical**, and that is verified for every migrated
    dialog;
  * one OK is **one undo step** (the per-key history in `history.js`);
  * renames go through the existing `modeledit.js` helpers, so references follow
    (sections → members, patterns → cases / mass source, cases → combos /
    cases_not_run, functions → cases).
* **Assign dialogs** act on the current selection:
  * Each field starts at the value common to the selection, or "— mixed —".
  * Only the fields the user changes are written, to every selected object.
  * **Apply** commits and keeps the dialog open. With nothing selected, the dialog
    says what to select and offers **Select All**.

### List dialogs (ETABS "Define …")

On the left is a table (name + 2–4 key columns). On the right is a **"Click to:"**
column:

* **Add:** Add New …, Add Copy of …, plus type pickers and library pickers.
* **Selected:** Modify/Show …, Delete …

Delete is refused in plain language while the item is still referenced, for example
"COL is used by 48 members — reassign first." The message names the users.

---

## 3. `dlgkit.js` API

```js
import { dkForm, dkGroup, dkAdvanced, dkTabs, dkList, dkDialog, dkDraft, dkCommit } from "./dlgkit.js";

const D = dkDraft(sky);                     // draft of the whole model
const F = dkForm();                         // field registry
const body = [
  dkGroup("Loads Applied", [
    F.select(c, "direction", [["X", "U1"], ["Y", "U2"]], { label: "Load Direction", id: "myDir" }),
    F.num(c, "scale", { label: "Scale Factor" }),                       // unit column "–"
    F.num(c, "dt", { label: "Time step", kind: "period", gt: 0 }),      // unit "s"
  ]),
  dkAdvanced([
    F.num(c, "damping", { label: "Damping", min: 0, lt: 1, def: 0.05 }),
  ], { countFn: () => (c.damping ?? 0.05) !== 0.05 ? 1 : 0 }),
];
dkDialog({ id: "myDlg", title: "Load Case Data — Time History", iconId: "exleaf-th", size: "md",
  form: F, body, onOk: () => { dkCommit(sky, D); } });
```

| Builder | Purpose |
|---|---|
| `F.num(obj, key, o)` | Number in display units. Options: `kind`, `min` `max` `gt` `lt` `int`, `allowNull` + `nullText` (empty → `null`, "auto"), `def` (default shown in the hint), `mixed`, `validate(v)`, `read()` / `write(v)` for computed bindings, `unit` (override). |
| `F.text`, `F.select`, `F.check`, `F.radio` | Same binding rules. `select` keeps an unknown current value as "(current)". |
| `F.readout(label, getSi, o)` | Read-only derived value with unit (G = E/2(1+ν), Fye, A/I/J). |
| `F.table(rows, columns, o)` | Editable rows (pattern × factor, T × Sa …). Supports Add / remove, `minRows`, `validateRow`, `validate`. `F.tableDirty(node)` reports whether the user touched it. |
| `dkGroup`, `dkAdvanced`, `dkTabs`, `dkList`, `dkInfo`, `dkNote` | Layout pieces. |
| `dkDialog(o)` | Shell (header, intro, body, footer, keys, validation focus). `onOk` returns `false` or a message to stay open. `onApply` adds Apply. `noCancel` makes a single Close. |
| `dkDraft`, `dkCommit`, `dkRefresh` | Draft / changed-keys commit / view refresh. |

Size classes: `sm` 420 px, `md` 560 px, `lg` 760 px. Every dialog is capped at 88vh,
and the UX pass adds drag, resize and remembered geometry.

---

## 4. Migrated dialogs (29)

Each dialog is routed from the menu command, the command palette / toolbar / shortcuts
(`__sky.etabs.clickItem`) and the Model Explorer. The legacy editor stays one click away.

| Menu command (`act`) | Standard dialog (id) | Legacy kept as |
|---|---|---|
| Define › Material Properties (`def-materials`) | Define Materials (`dsMaterials`) → Material Property Data (`dsMaterialData`) | "Stress–Strain…" → Section Manager |
| Define › Frame Sections (`def-frame`) | Frame Properties (`dsFrameSections`) → Frame Section Property Data (`dsFrameSectionData`), Import Frame Sections (`dsSectionLib`) | "Section Designer…", "Nonprismatic…" |
| Define › Slab / Wall Sections (`def-shell`) | Slab / Wall Properties (`dsShellSections`) → Slab / Wall Property Data (`dsShellSectionData`) | "Layered Sections…" |
| Define › Load Patterns (`def-patterns`) | Define Load Patterns (`dsLoadPatterns`) → Load Pattern Data (`dsLoadPatternData`) | "Modify Lateral Load…" (Auto Lateral) |
| Define › Load Cases (`def-loadcases`, `def-cases`, `def-rs`, `def-th`, `def-buckling`) | Define Load Cases (`dcLoadCases`) → Load Case Data — Linear Static / Response Spectrum / Time History / Buckling (`dcStaticCase`, `dcRsCase`, `dcThCase`, `dcBuckCase`) | Pushover, Nonlinear Static, Staged, Steady State, PSD and Hyperstatic open their existing editors from the same list |
| Define › Modal Case (`def-modal`) | Load Case Data — Modal (`dcModalCase`) | — |
| Define › Load Combinations (`def-combos`) | Define Load Combinations (`dcCombos`) → Load Combination Data (`dcComboData`) | "Add Default Design Combos…" → code tools |
| Define › Mass Source (`def-mass`) | Mass Source Data (`dcMassSource`) | — |
| Define › Diaphragms (`def-diaphragms`) | Define Diaphragms (`dcDiaphragms`) → Diaphragm Data (`dcDiaData`) | — |
| Define › Stories / Grid (`def-stories`, `def-grid`, `draw-grid`) | Stories and Grid Systems (`dsGridStories`, tabs Story Data / Grid Data) | "Multiple Grid Systems…" (rotated / radial + preview) |
| Define › Functions (`def-functions`) | Define Functions (`dsFunctions`, tabs RS / TH) → Response Spectrum Function (`dsRsFunctionData`, with EC8 generator) / Time History Function (`dsThFunctionData`) | — |
| Analyze › Analysis Options (`an-opts`), Set Active DOF (`an-dof`), Define › P-Delta Options (`def-pdelta`) | Analysis Options (`dcAnalysisOptions`, tabs DOF / Modal / P-Delta / Model) | "Load Cases to Run…" |
| Assign › Frame · Section / Releases / Local Axis / Rigid End Offsets / Axial Limit / Hinges (`asn-fsec` … `asn-fhinge`) | Assign Frame Properties (`daFrame`, opens on the matching tab) | was a toast only |
| Assign › Shell · Section / Wind Cp (`asn-ssec`, `asn-scp`) | Assign Shell Properties (`daShell`) | was a toast only |
| Assign › Supports / Springs (`asn-support`) | Assign Joint Restraints (`daSupports`: Fixed / Pinned / Roller / Free + per-DOF, base or all joints, automatic base fixity) | was a toast only |
| right-click › Object Information… | Object Information (`daInfo`) | — |

**Not yet migrated.** These keep their own shells and are already footer-normalised by
the UX pass. They are the next candidates, in order of use:

* Pushover and Nonlinear Static case data
* Staged construction
* Steady State / PSD and Frequency Functions
* Auto Lateral / Open Structure Wind
* the Assign load dialogs (joint, frame and shell loads, temperature, mass, diaphragm)
* the Edit dialogs (Replicate, Move, Divide, Merge, Align)
* Groups, Springs, Hinges, Tendons, Towers
* Units, Set Load Cases to Run

Migrating one means building its form with `dlgkit` and adding one line to `ROUTES` in
`js/dlgstd.js`.

---

## 5. Guided workflow and context menus

**Model Workflow checklist** (`js/workflow.js`). It is a slim card docked at the
bottom-right of the drawing canvas, clear of the Properties panel. It lists the ETABS
modelling flow and runs the matching menu command when a step is clicked.

| # | Step | Done when … (computed from the model) | Click runs |
|---|---|---|---|
| 1 | Grid & Stories | ≥ 1 story and a grid | `def-stories` |
| 2 | Materials | ≥ 1 material | `def-materials` |
| 3 | Sections | ≥ 1 frame or slab/wall section | `def-frame` |
| 4 | Draw | ≥ 1 frame or shell object | `draw-column` |
| 5 | Supports | objects exist and there is automatic base fixity or explicit / spring supports | `asn-support` |
| 6 | Load Patterns | ≥ 1 pattern | `def-patterns` |
| 7 | Assign Loads | any pattern has loads or a self-weight multiplier > 0 | `asn-l116-fdist` |
| 8 | Mass Source | self mass on, or patterns listed | `def-mass` |
| 9 | Load Cases | ≥ 1 non-modal case | `def-loadcases` |
| 10 | Run Analysis | results exist (shown as **re-run** when the model was edited after the run) | `an-run` (F5) |
| 11 | Results | results were viewed in the Analyze workspace | `dis-deformed` |

* The first open step is highlighted as **next**, and the footer button runs it
  ("Next: Draw", "Run analysis (F5)").
* The card collapses to a one-line pill: "Model workflow 6/11 · Next: Assign Loads".
  It collapses by itself once all steps are done.
* **×** hides the card, and **Help › Model Workflow** brings it back. Both states are
  remembered per browser in `localStorage`.
* It updates on `sky:model-changed` and `sky:results-changed` plus a light 1.2 s check.

**Empty states.** Results views before a run show **Run Analysis (F5)**, together with
"Define …" when the view needs a case type (`js/ux_empty.js`). Lists in the standard
dialogs say what to do ("No load patterns yet — add DEAD (self weight 1) and LIVE to
start").

**Context menus** work on the Plan, Elevation and 3D views.

* A right-click **without dragging** opens the menu; right-drag still pans.
* It selects the object under the cursor, unless that object is already part of the
  selection.
* Every entry is an existing menu `act`, so it behaves exactly like the menu.

The entries by object type:

* **Frame:** Frame Section, Releases, Local Axes, End Offsets, Insertion Point,
  distributed and concentrated loads, Additional Mass, Supports at Joints, Assign to
  Group; then Copy, Replicate, Move, Delete; Show Member Forces (after a run); Object
  Information.
* **Shell:** Slab / Wall Section, Uniform Load, Temperature Load, Diaphragm, Additional
  Mass, Supports, Group, plus the edit actions.
* **Link / spring:** their property dialogs.
* **Empty canvas:** Select All, Paste, Draw Column / Beam, Stories and Grid, Run
  Analysis, Model Workflow.

Keys: ↑/↓/Enter navigate the menu. Esc, a click elsewhere, scrolling or resizing close
it.

---

## 6. Verification

Playwright verification ran against `?mock=1` and the live backend (OpenSeesPy). It
checks:

* every migrated dialog opens from its menu;
* **OK with defaults is byte-identical** for 19 top-level entry points and all nested
  Modify → OK → OK paths, both in the working model and in `GET /api/model` after save;
* Cancel discards nested edits; Esc cancels; Enter commits the field and presses OK;
  inline validation appears;
* an edit plus save in every dialog round-trips through `POST /api/model`, and a second
  POST of the echoed model gives identical bytes;
* one OK is one undo step;
* **kip-in**: the unit labels read "in" and "ksi"; 0.55 m shows as 21.6535 in; typing
  24 in stores 0.6096 m and 29000 ksi stores 199 947 961.5 kPa;
* the context menu works in plan and 3D, right-drag still pans, and the Info dialog
  opens;
* the checklist goes materials → sections → draw as items are added, Run → done, and
  Results → done after viewing;
* the Model Explorer and the menubar route to the standard dialogs, and the legacy
  commands still open;
* there are **zero console errors**.

Results: mock 82/82, live 78/78. The live run solved the edited model, including pinned
supports, new cases and P-Delta options.

**Known limitation.** This is not new: the existing `modeledit.setStoryHeight` helper,
also used by the legacy grid editor, moves members and shells but not links and point
springs. Changing a lower story's height can therefore leave a link disconnected, and
Check Model reports it before the run.
