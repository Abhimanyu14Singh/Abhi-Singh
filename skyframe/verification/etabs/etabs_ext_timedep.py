"""ETABS side of the time-dependent group (cases_x_timedep.py).  Runs on the ETABS machine, imported
by run_etabs.py; ``R`` is the run_etabs module.

C1, C2  the concrete material gets ACI 209R-92 time-dependent properties and a one-stage staged
        construction case adds the structure at the given age, loads it, and holds it for the stage
        duration with time-dependent material properties on.
C3      N6's elastic-perfectly-plastic M3 column hinges.  ETABS 23's API has no FrameObj.SetHingeAssigns
        and no PropHinge, so the hinge tables are found at run time (every table key with "hinge"),
        their fields read (GetAllFieldsInTable) and a user hinge property and its assignments written
        through them; then the stock pushover case is defined.

ETABS 23's API sets neither of these directly, so the table keys and field names are discovered,
not assumed.  Every table that is tried is described in the log (key, import type, fields), and
when nothing works the run stops with what was offered and what ETABS answered.
"""

from __future__ import annotations

import math
import re

MAT_CONCRETE = 2                 # eMatType.Concrete
HINGE = "EPP_M3"                 # the user hinge property C3 defines


# ── small table helpers (never raise; return what happened) ─────────────────
def _norm(s) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum() or ch == "/")


def _import_types(sm) -> dict:
    """{table key: ImportType} (0 not importable, 1 not interactively, 2 unlocked model, 3 always) for
    EVERY table: GetAllTables (GetAvailableTables lists only tables that already hold rows)."""
    out = {}
    dt = sm.DatabaseTables
    for call in (lambda: dt.GetAllTables(0, [], [], [], []), lambda: dt.GetAvailableTables(0, [], [], [])):
        try:
            t = call()
            for k, v in zip(t[1], t[3]):
                out.setdefault(str(k), int(v))
        except Exception:
            pass
    return out


def all_keys(sm, R) -> list:
    """Every table key ETABS knows: the runner's full list, plus any the import-type listing adds."""
    keys = list(R.table_keys(sm))
    keys += [k for k in _import_types(sm) if k not in keys]
    return keys


def _fields(sm, key: str):
    """(field keys, importable flags) of a table, from GetAllFieldsInTable."""
    try:
        f = sm.DatabaseTables.GetAllFieldsInTable(key, 0, 0, [], [], [], [], [])
        keys = [str(x) for x in f[2]]
        imp = [bool(x) for x in f[6]] if len(f) > 7 else [True] * len(keys)
        return keys, imp
    except Exception as exc:
        return [f"(fields not read: {exc})"], []


def describe(sm, R, keys, types=None) -> list:
    """One line per table: key, import type, fields (importable ones marked *)."""
    types = types if types is not None else _import_types(sm)
    out = []
    for k in keys:
        fk, imp = _fields(sm, k)
        fl = ", ".join(f + ("*" if i else "") for f, i in zip(fk, imp)) if imp else ", ".join(fk)
        out.append(f"[{k}] import type {types.get(k, '?')}; fields (* importable): {fl}")
    for line in out:
        R.say("    " + line)
    return out


def _open(sm, key: str):
    """(version, fields, rows, data) for editing.  An empty table may refuse to open for editing (or
    open without fields): then the importable fields from GetAllFieldsInTable start an empty table."""
    err = ""
    try:
        got = sm.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
        code = got[-1] if isinstance(got, (list, tuple)) else got
        if code == 0 and len(got[1]):
            return (got[0], [str(f) for f in got[1]], int(got[2]), [str(v) for v in got[3]]), ""
        err = f"GetTableForEditingArray({key!r}) returned {code} with {len(got[1]) if code == 0 else 0} fields"
    except Exception as exc:
        err = f"GetTableForEditingArray({key!r}): {exc}"
    try:
        f = sm.DatabaseTables.GetAllFieldsInTable(key, 0, 0, [], [], [], [], [])
        if f[-1] == 0 and len(f[2]):
            imp = [bool(x) for x in f[6]] if len(f) > 7 else [True] * len(f[2])
            fields = [str(k) for k, i in zip(f[2], imp) if i] or [str(k) for k in f[2]]
            return (f[0], fields, 0, []), ""
        err += f"; GetAllFieldsInTable returned {f[-1]}"
    except Exception as exc:
        err += f"; GetAllFieldsInTable: {exc}"
    return None, err


_LOG_HEADER = ("*****", "etabs version", "import type and options", "deleted from db", "same name in model",
               "in the same location")


def import_log(text, limit: int = 2500) -> str:
    """ETABS's import log without its fixed header (database name, import options), so the errors
    and warnings themselves fit in the message."""
    lines = [ln.strip() for ln in str(text).replace("\r", "").split("\n")]
    keep = [ln for ln in lines if ln and not any(h in ln.lower() for h in _LOG_HEADER)]
    return " / ".join(keep)[:limit] or "(empty import log)"


def _write(sm, key: str, version, fields, n, data) -> str:
    """Set and apply an edited table; '' when ETABS took it, else what it said."""
    dt = sm.DatabaseTables
    try:
        r = dt.SetTableForEditingArray(key, version, fields, n, data)
        code = r[-1] if isinstance(r, (list, tuple)) else r
        if code != 0:
            err = f"SetTableForEditingArray returned {code}"
        else:
            r = dt.ApplyEditedTables(True, 0, 0, 0, 0, "")
            # out arguments: fatal errors, errors, warnings, info messages, import log
            err = "" if (r[-1] == 0 and not r[0] and not r[1]) else (
                f"ApplyEditedTables returned {r[-1]}: {r[0]} fatal errors, {r[1]} errors, {r[2]} warnings: "
                f"{import_log(r[4])}")
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    if err:
        try:
            dt.CancelTableEditing()                    # leave no half-edited table behind
        except Exception:
            pass
    return err


def _value_for(field: str, rules):
    """The first rule whose words all occur in the field (normalised) and whose 'not' words do not."""
    f = _norm(field)

    def has(w):                                        # "=w" exact, "w$" ends with, else contains
        return f == w[1:] if w.startswith("=") else f.endswith(w[:-1]) if w.endswith("$") else w in f
    for words, value, *nots in rules:
        if all(has(w) for w in words) and not any(n in f for n in (nots[0] if nots else ())):
            return value
    return None


def upsert(sm, key: str, rows: list, rules_of, name_words=("name", "material", "label", "unique")) -> tuple:
    """Write ``rows`` (one rules list each, see _value_for) into table ``key``: a row whose identifying
    field already holds the row's name is changed in place, else a row is appended (unmatched fields
    left at the table's value or blank).  Returns (error or '', what was written)."""
    opened, err = _open(sm, key)
    if opened is None:
        return err, ""
    version, fields, n, data = opened
    nf = len(fields)
    wrote = []
    for rules in rules_of(rows):
        vals = {i: _value_for(f, rules) for i, f in enumerate(fields)}
        idx = next((i for i, f in enumerate(fields) if any(w in _norm(f) for w in name_words)
                    and vals.get(i) is not None), None)
        target = None
        if idx is not None:
            target = next((k for k in range(n) if data[k * nf + idx].lower() == str(vals[idx]).lower()), None)
        if target is None:
            data += [""] * nf
            target, n = n, n + 1
        for i, v in vals.items():
            if v is not None:
                data[target * nf + i] = str(v)
        wrote.append("{" + ", ".join(f"{fields[i]}={v}" for i, v in vals.items() if v is not None) + "}")
    return _write(sm, key, version, fields, n, data), "; ".join(wrote)


def _row_of(sm, key: str, name: str) -> str:
    opened, err = _open(sm, key)
    if opened is None:
        return err
    version, fields, n, data = opened
    nf = len(fields)
    rows = [data[k * nf:(k + 1) * nf] for k in range(n) if name.lower() in [v.lower() for v in data[k * nf:(k + 1) * nf]]]
    return "; ".join("{" + ", ".join(f"{f}={v}" for f, v in zip(fields, r) if v != "") + "}" for r in rows) or "(no row)"


def _api_names(obj, *words) -> list:
    try:
        return [n for n in dir(obj) if not n.startswith("_") and any(w.lower() in n.lower() for w in words)]
    except Exception:
        return []


# ── C1, C2: time-dependent material ─────────────────────────────────────────
def set_concrete(sm, R, mt: dict) -> None:
    """Make the material a Concrete type (time-dependent properties are a concrete feature) with the
    definition's E and nu; read back."""
    nm = mt["name"]
    R.ok(sm.PropMaterial.SetMaterial(nm, MAT_CONCRETE), f"material {nm} as concrete")
    R.ok(sm.PropMaterial.SetMPIsotropic(nm, mt["E"], mt["nu"], float(mt.get("alpha", 0.0))), f"material {nm} E, nu")
    R.ok(sm.PropMaterial.SetWeightAndMass(nm, 1, mt.get("unitWeight", 0.0)), f"material {nm} weight")
    back = sm.PropMaterial.GetMPIsotropic(nm, 0.0, 0.0, 0.0, 0.0)
    R.ok(back, f"read back material {nm}")
    if abs(back[0] - mt["E"]) > 1e-6 * mt["E"] or abs(back[1] - mt["nu"]) > 1e-9:
        raise R.Stop(f"material {nm}: E, nu read back {back[0]}, {back[1]}; set {mt['E']}, {mt['nu']}")
    R.say(f"  material {nm}: concrete, E {back[0]}, nu {back[1]} (read back)")


def _td_rules(td: dict, law_name: str, yes="Yes", no="No"):
    yn = lambda b: yes if b else no
    vs = float(td["vs"])
    return [
        # ("shrinkage" itself contains "age": the start of drying is named by start / drying / ts)
        (("shrink", "start"), float(td["shrinkStart"])), (("drying",), float(td["shrinkStart"])),
        (("shrink", "time"), float(td["shrinkStart"]), ("consider", "include")), (("=ts",), float(td["shrinkStart"])),
        (("material",), td["material"]), (("name",), td["material"]),
        (("humid",), float(td["rh"])), (("=rh",), float(td["rh"])),
        (("notional",), 2.0 * vs), (("v/s",), vs), (("vs",), vs, ("vsfactor",)), (("volume",), vs),
        (("cement",), "Type " + td["cement"], ("content", "factor")),
        (("curing",), td["curing"].capitalize()), (("cure", "type"), td["curing"].capitalize()),
        (("creep", "type"), "Full Integration"), (("creep", "method"), "Full Integration"),
        (("creep", "analysis"), "Full Integration"),
        (("creep",), yn(td["creep"])), (("shrink",), yn(td["shrinkage"])),
        (("aging",), yn(td["aging"])), (("age",), yn(td["aging"]), ("percent", "average", "start", "load")),
        (("strength",), yn(td["aging"])),
        (("type",), law_name), (("model",), law_name), (("code",), law_name),
    ]


LAW_NAMES = {"ACI 209R-92": ("ACI 209R-92", "ACI209R-92", "ACI 209R-1992", "ACI 209")}


def set_time_dependent(sm, R, td: dict) -> str:
    """ACI 209R-92 creep / shrinkage on the material: the PropMaterial.TimeDep object's setters
    (ETABS 23 has the object; its methods and argument names are read from the type library at run
    time), else the material time-dependent tables; R.Stop with both diagnostics when neither takes."""
    try:
        return set_time_dependent_api(sm, R, td)
    except R.Stop as exc:
        api_err = str(exc)
    R.say(f"  PropMaterial.TimeDep did not take the properties ({api_err[:300]}); trying tables")
    try:
        return set_time_dependent_tables(sm, R, td)
    except R.Stop as exc:
        raise R.Stop(f"time-dependent material: API: {api_err} ||| tables: {exc}")


# ── PropMaterial.TimeDep: signatures from the comtypes type library ─────────
_OTHER_LAWS = ("ceb", "fip", "gl2000", "euro", "ec2", "as3600", "jtg", "user", "relax", "tendon", "2010")


def _specs(obj):
    """(class name, spec) of every _methods_ / _disp_methods_ entry on the object's interface classes."""
    out = []
    for cls in type(obj).__mro__:
        for attr in ("_methods_", "_disp_methods_"):
            try:
                for spec in cls.__dict__.get(attr, ()) or ():
                    out.append((cls.__name__, spec))
            except Exception:
                pass
    return out


def _spec_name(spec):
    try:
        return str(spec.name if hasattr(spec, "name") else spec[1])
    except Exception:
        return ""


def com_params(obj, method: str):
    """[(name, flags, kind)] of a comtypes method, from its interface's _methods_ (flags: 1 in,
    2 out, 3 in/out = ByRef; kind float / int / bool / str / list).  None whenever it cannot be read
    (ETABS 23's binding gave argtypes None): never raises."""
    try:
        for _, spec in _specs(obj):
            if _spec_name(spec) != method:
                continue
            argtypes = spec.argtypes if hasattr(spec, "argtypes") else spec[2]
            pflags = spec.paramflags if hasattr(spec, "paramflags") else spec[3]
            if not argtypes or not pflags or len(argtypes) != len(pflags):
                return None
            out = []
            for at, pf in zip(argtypes, pflags):
                tn = getattr(at, "__name__", str(at))
                if tn.startswith("LP_") and hasattr(at, "_type_"):      # ByRef: POINTER(base)
                    tn = getattr(at._type_, "__name__", str(at._type_))
                tn = tn.lower()
                kind = ("list" if "array" in tn else "bool" if "bool" in tn
                        else "float" if "double" in tn or "float" in tn
                        else "str" if "bstr" in tn or "char" in tn else "int")
                out.append((str(pf[1]) if len(pf) > 1 else "", int(pf[0]), kind))
            return out
    except Exception:
        return None
    return None


def com_metadata(obj, methods) -> list:
    """Whatever the binding knows about each method: its _methods_ / _disp_methods_ entry (repr) and
    __doc__, so the log shows the real signatures even when com_params cannot read them."""
    lines = []
    specs = {}
    for cls, spec in _specs(obj):
        specs.setdefault(_spec_name(spec), []).append((cls, spec))
    for m in methods:
        try:
            doc = " ".join(str(getattr(obj, m).__doc__ or "").split())[:300]
        except Exception as exc:
            doc = f"(no __doc__: {exc})"
        try:
            raw = "; ".join(f"{cls}: {spec!r}"[:600] for cls, spec in specs.get(m, [])) or "(no _methods_ entry)"
        except Exception as exc:
            raw = f"(repr failed: {exc})"
        lines.append(f"{m}: doc {doc or '-'} | spec {raw}")
    return lines


def _placeholder(kind):
    return {"float": 0.0, "int": 0, "bool": False, "str": "", "list": []}[kind]


def _td_api_value(pname: str, kind: str, td: dict):
    """The value for a TimeDep argument named ``pname`` of type ``kind``; None keeps ETABS's own value
    (enums such as cement type, curing and creep method are not guessed)."""
    f = _norm(pname)
    vs = float(td["vs"])
    if kind == "str" and ("name" in f or "material" in f):
        return td["material"]
    if kind == "bool":
        if "creep" in f:
            return bool(td["creep"])
        if "shrink" in f:
            return bool(td["shrinkage"])
        if any(w in f for w in ("age", "aging", "strength", "stiff", "modulus")):
            return bool(td["aging"])
        return None
    if kind == "float":
        if "humid" in f or f in ("rh", "relhum"):
            return float(td["rh"])
        if "notional" in f or f in ("h", "h0"):
            return 2.0 * vs
        if "vs" in f or "vol" in f or "surf" in f:
            return vs
        if ("shrink" in f and any(w in f for w in ("start", "age", "time"))) or "drying" in f or f in ("ts", "tsh0"):
            return float(td["shrinkStart"])
    return None


def _call(obj, method, args):
    r = getattr(obj, method)(*args)
    code = r[-1] if isinstance(r, (list, tuple)) else r
    return code, (list(r[:-1]) if isinstance(r, (list, tuple)) else [])


def _get_args(sig, material):
    return [material if i == 0 else _placeholder(k) for i, (_, fl, k) in enumerate(sig) if fl & 1]


def _guesses(method: str, td: dict) -> list:
    """Argument orders tried when the signature cannot be read: the material name first (CSI style),
    then the ACI 209 numbers or the consider flags; ints 1 = type I cement / moist curing."""
    nm, rh, vs, ts = td["material"], float(td["rh"]), float(td["vs"]), float(td["shrinkStart"])
    cr, sh, ag = bool(td["creep"]), bool(td["shrinkage"]), bool(td["aging"])
    low = method.lower()
    if "209" in low or "aci" in low:
        return [(nm, rh, vs, ts), (nm, 1, 1, rh, vs, ts), (nm, 1, 1, rh, vs, ts, 0.0), (nm, ts, rh, vs),
                (nm, rh, vs), (nm, rh / 100.0, vs, ts), (nm, 1, 1, rh, vs, ts, 1.0, 1.0, 1.0, 1.0)]
    return [(nm, ag, sh, cr), (nm, cr, sh, ag), (nm, ag, sh, cr, 0, 0), (nm, cr, sh, ag, 0, 0)]


def _guess_get(tdo, get: str, nm: str) -> str:
    """A Get with an unreadable signature: the name then 0..12 zero placeholders until one answers 0."""
    for n in range(0, 13):
        try:
            r = getattr(tdo, get)(nm, *([0.0] * n))
            code = r[-1] if isinstance(r, (list, tuple)) else r
            if code == 0:
                return f"{get} (guessed {n} placeholders): {list(r) if isinstance(r, (list, tuple)) else r}"
        except Exception:
            continue
    return f"{get}: no placeholder count answered"


def set_time_dependent_api(sm, R, td: dict) -> str:
    """Never raises anything but R.Stop: an unexpected binding error becomes a diagnostic."""
    tdo = getattr(sm.PropMaterial, "TimeDep", None)
    try:
        return _set_time_dependent_api(sm, R, td, tdo)
    except R.Stop:
        raise
    except Exception as exc:
        import traceback
        raise R.Stop(f"PropMaterial.TimeDep: {type(exc).__name__}: {exc} ({traceback.format_exc()[-600:]}) || "
                     f"dir: {R._methods(tdo)}")


def _set_time_dependent_api(sm, R, td: dict, tdo) -> str:
    """Every ACI 209 setter of PropMaterial.TimeDep (and the law-neutral option setters: consider
    creep / shrinkage / age), arguments matched by name; read back with the matching Get."""
    if tdo is None:
        raise R.Stop(f"PropMaterial has no TimeDep (offers {R._methods(sm.PropMaterial)})")
    R.say(f"  PropMaterial.TimeDep dir: {R._methods(tdo)}")
    methods = [n for n in dir(tdo) if not n.startswith("_") and n[:3] in ("Set", "Get")]
    sigs = {m: com_params(tdo, m) for m in methods}
    meta = com_metadata(tdo, methods)
    for m, line in zip(methods, meta):
        sg = sigs[m]
        R.say(f"    {m}(" + (", ".join(f"{n}:{k}{'&' if fl & 2 else ''}" for n, fl, k in sg) if sg is not None
                             else "signature not readable") + ")")
        R.say(f"      {line}")
    low = {m: m.lower() for m in methods}
    aci = [m for m in methods if m.startswith("Set") and ("209" in low[m] or "aci" in low[m])]
    generic = [m for m in methods if m.startswith("Set") and m not in aci
               and not any(w in low[m] for w in _OTHER_LAWS)
               and any(w in low[m] for w in ("option", "consider", "creep", "shrink", "age", "timedep"))]
    tried, done, readback = [], [], []
    nm = td["material"]
    for m in aci + generic:
        sg, get = sigs[m], "Get" + m[3:]
        gsig = sigs.get(get) if get in methods else None
        current = {}
        if gsig is not None:
            try:
                code, vals = _call(tdo, get, _get_args(gsig, nm))
                if code == 0:
                    current = dict(zip([n for n, fl, _ in gsig[1:] if fl & 2], vals))
            except Exception as exc:
                tried.append(f"{get}: {exc}")
        if sg is not None:
            args, kept, ours = [], [], 0
            for i, (pn, fl, kind) in enumerate(sg):
                if not fl & 1:
                    continue
                v = nm if (i == 0 and kind == "str") else _td_api_value(pn, kind, td)
                if v is None:
                    v = current.get(pn, _placeholder(kind))
                    kept.append(f"{pn}={v!r}")
                elif i:
                    ours += 1
                args.append(v)
            if m in generic and not ours:
                continue                                # nothing of ours in it (e.g. a type enum)
            try:
                code, _ = _call(tdo, m, args)
            except Exception as exc:
                code = f"{type(exc).__name__}: {exc}"
            if code != 0:
                tried.append(f"{m}{tuple(args)} returned {code}")
                continue
            done.append((m, f"{m}{tuple(args)}" + (f" [ETABS values kept: {', '.join(kept)}]" if kept else "")))
        else:                                           # signature unreadable: a few argument orders
            for args in _guesses(m, td):
                if not R._try(f"{m}{args}", tried, lambda a=args: getattr(tdo, m)(*a)):
                    continue
                if m in aci and get in methods:
                    # COM coerces numbers silently: keep a guessed order only when the Get returns
                    # RH, V/S and the drying start among its values
                    back = _guess_get(tdo, get, nm)
                    want = (float(td["rh"]), float(td["vs"]), float(td["shrinkStart"]))
                    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", back.split(":", 1)[-1])]
                    if not all(any(abs(w - x) < 1e-9 for x in nums) for w in want):
                        tried.append(f"{m}{args} accepted but {back} does not hold {want}")
                        continue
                done.append((m, f"{m}{args} (signature not readable: guessed order)"))
                break
            else:
                continue
        if gsig is None:
            if get in methods:
                readback.append(_guess_get(tdo, get, nm))
            continue
        try:                                            # read back; what we set must come back
            code, vals = _call(tdo, get, _get_args(gsig, nm))
            byref = [(n, k) for n, fl, k in gsig[1:] if fl & 2]
            readback.append(f"{get}: " + ", ".join(f"{n}={v}" for (n, _), v in zip(byref, vals)) + f" (code {code})")
            for (n, k), v in zip(byref, vals):
                want = _td_api_value(n, k, td)
                if want is not None and not isinstance(want, str) and abs(float(v) - float(want)) > 1e-9:
                    raise R.Stop(f"PropMaterial.TimeDep.{get} reads back {n} = {v}, set {want}")
        except R.Stop:
            raise
        except Exception as exc:
            readback.append(f"{get}: {exc}")
    if not any(m in aci for m, _ in done):
        raise R.Stop("PropMaterial.TimeDep: no ACI 209 setter took the properties. Set: "
                     + ("; ".join(d for _, d in done) or "nothing") + ". Tried: " + " | ".join(tried[-8:])
                     + " || methods: " + "; ".join(
                         f"{m}({', '.join(n for n, _, _ in sigs[m]) if sigs[m] is not None else '?'})" for m in methods)
                     + " || read back: " + " | ".join(readback) + " || metadata: " + " | ".join(meta)[:3000])
    for _, d in done:
        R.say(f"  TimeDep set: {d}")
    for rb in readback:
        R.say(f"  TimeDep read back: {rb}")
    return f"{td['law']} through PropMaterial.TimeDep ({', '.join(m for m, _ in done)})"


def set_time_dependent_tables(sm, R, td: dict) -> str:
    tried = []
    api = _api_names(sm.PropMaterial, "time", "creep", "shrink")
    types = _import_types(sm)
    keys = [k for k in all_keys(sm, R)
            if "material" in k.lower() and any(w in k.lower() for w in ("time", "creep", "shrink"))]
    if not keys:
        raise R.Stop("no material table mentions time, creep or shrinkage (PropMaterial offers "
                     f"{api or 'no time/creep/shrink members'}); material tables offered: "
                     + "; ".join(k for k in all_keys(sm, R) if "material" in k.lower()))
    R.say(f"  time-dependent material tables: {keys}")
    described = describe(sm, R, keys, types)
    # a general table (no code name in its key) first, then the ACI 209 one
    keys.sort(key=lambda k: ("209" in k, k))
    done = []
    for key in keys:
        if any(c in key for c in ("CEB", "GL2000", "2010", "Eurocode", "AS ", "JTG", "User")):
            continue                                   # tables for other code models
        ok_key = False
        for law in LAW_NAMES[td["law"]]:
            for yes, no in (("Yes", "No"), ("True", "False")):
                err, wrote = upsert(sm, key, [td], lambda rows: [_td_rules(td, law, yes, no) for _ in rows],
                                    name_words=("material", "name"))
                if not err:
                    ok_key = True
                    break
                tried.append(f"{key} ({law}, {yes}/{no}): {err} [wrote {wrote}]")
            if ok_key:
                break
        if ok_key:
            done.append(f"{key}: {_row_of(sm, key, td['material'])}")
    if not done:
        raise R.Stop("time-dependent material properties: no table took them. Tried: " + " | ".join(tried[-6:])
                     + " || tables: " + " || ".join(described)
                     + f" || PropMaterial methods: {R._methods(sm.PropMaterial)}")
    for d in done:
        R.say(f"  time-dependent (read back) {d}")
    return f"{td['law']} through {len(done)} table(s)"


def set_td_staged(sm, case: dict, R, point, names: dict, st: dict) -> str:
    """One stage: add every frame and joint at age st['age'] (days), load st['pattern'] if any, hold
    for st['duration'] days; time-dependent material properties on."""
    g = "TDALL"
    t = []
    if not R._try("GroupDef.SetGroup", t, lambda: sm.GroupDef.SetGroup(g)):
        raise R.Stop("group refused: " + "; ".join(t))
    for f in case["frames"]:
        R.ok(sm.FrameObj.SetGroupAssign(names[f["id"]], g, False, 0), f"group {g}: {f['id']}")
        for p in (f["i"], f["j"]):
            R.ok(sm.PointObj.SetGroupAssign(point(p), g, False, 0), f"group {g}: point {p}")
    nm, ss = st["name"], sm.LoadCases.StaticNonlinearStaged
    if not R._try("StaticNonlinearStaged.SetCase", t, lambda: ss.SetCase(nm)):
        raise R.Stop("staged case refused: " + "; ".join(t) + f" | offers: {R._methods(ss)}")
    dur = float(st["duration"])
    if not (R._try("SetStageDefinitions_2", t, lambda: ss.SetStageDefinitions_2(nm, 1, [dur], [True], [""], [""]))
            or R._try("SetStageDefinitions_1", t, lambda: ss.SetStageDefinitions_1(nm, 1, [dur], [True], [""], [""]))
            or R._try("SetStageDefinitions", t, lambda: ss.SetStageDefinitions(nm, 1, [dur], [True], [""]))):
        raise R.Stop("stage definition refused: " + "; ".join(t) + f" | offers: {R._methods(ss)}")
    age = float(st["age"])
    if st.get("pattern"):
        # operation 1 = add structure (with the age of what is added), 3 = load objects if new
        args = (nm, 1, 2, [1, 3], ["Group", "Group"], [g, g], [age, 0.0], ["", "Load"], ["", st["pattern"]],
                [0.0, 1.0])
    else:
        args = (nm, 1, 1, [1], ["Group"], [g], [age], [""], [""], [0.0])
    if not (R._try("SetStageData_2", t, lambda: ss.SetStageData_2(*args))
            or R._try("SetStageData_1", t, lambda: ss.SetStageData_1(*args))
            or R._try("SetStageData", t, lambda: ss.SetStageData(*args))):
        raise R.Stop("stage data refused: " + "; ".join(t[-3:]) + f" | offers: {R._methods(ss)}")
    if hasattr(ss, "SetGeometricNonlinearity"):
        R._try("SetGeometricNonlinearity", t, lambda: ss.SetGeometricNonlinearity(nm, 0))
    # time-dependent material properties on for the case
    how = None
    for label, call in (("SetMaterialNonlinearity(name, True)", lambda: ss.SetMaterialNonlinearity(nm, True)),
                        ("SetMaterialNonlinearity(name, True, True, True)",
                         lambda: ss.SetMaterialNonlinearity(nm, True, True, True)),
                        ("SetMaterialNonlinearity(name, True, True, True, True)",
                         lambda: ss.SetMaterialNonlinearity(nm, True, True, True, True))):
        if hasattr(ss, "SetMaterialNonlinearity") and R._try(label, t, call):
            how = label
            break
    if how is None:
        key = R.find_table(sm, "load case", "staged") or R.find_table(sm, "staged")
        if key:
            try:
                how = R.edit_table_row(sm, key, nm, {"timedep|timedependent|timedepmatprop|materialtime": "Yes"})
            except R.Stop as exc:
                t.append(str(exc))
        if how is None:
            raise R.Stop("staged case: time-dependent material properties could not be switched on: "
                         + "; ".join(t[-4:]) + f" | StaticNonlinearStaged offers: {R._methods(ss)}"
                         + (" | staged tables: " + " || ".join(describe(sm, R, [key])) if key else ""))
    back = ""
    if hasattr(ss, "GetMaterialNonlinearity"):
        for call in (lambda: ss.GetMaterialNonlinearity(nm, False),
                     lambda: ss.GetMaterialNonlinearity(nm, False, False, False)):
            try:
                back = f"; read back {list(call())}"
                break
            except Exception:
                pass
    return (f"one stage of {dur:g} days: add at age {age:g} days" + (f", load {st['pattern']}" if st.get("pattern")
                                                                       else "") + f"; time-dependent on ({how}{back})")


# ── C3: frame hinges through the tables ─────────────────────────────────────
# ETABS 23.2 (second run's log): the user property is a row set in "Hinge Definitions - Moment M3"
# (Name, IsSymmetric, AddBC, AddCD, FDType, Point, Force, Displ, UseYldForce, UseYldDispl,
# FDPosForSF, FDPosDispSF, ..., BeyondE, LengthType, SSAbsLen, SSRelLen, IOPos.., HysType.., GUID:
# the first row carries the property data and point A, the next rows only Name, Point, Force,
# Displ), and the assignment goes in "Frame Assignments - Hinge - User" (UniqueName, AssignHinge,
# SaveFibResp, DistType, RelDist, AbsDist, LenOWType, LenOWRel, LenOWAbs).  Force / Displ are
# divided by the scale factors: SF force = Mp, SF displacement = 1 rad (not the yield values).
# Elastic-perfectly-plastic: (Force/SF, plastic rotation) A (0, 0), B (1, 0), C (1, 0.5),
# D (0.2, 0.55), E (0.2, 1.0); the push needs about 0.05 rad.
EPP_POINTS = [("A", 0.0, 0.0), ("B", 1.0, 0.0), ("C", 1.0, 0.5), ("D", 0.2, 0.55), ("E", 0.2, 1.0)]
FD_TYPES = ("Moment-Rotation", "Moment - Rotation")
# (DistType, SaveFibResp, LenOWType) tried in turn; the first run wrote DistType 0.0 (a number)
ASSIGN_VARIANTS = [("Relative", "No", ""), ("Relative Distance", "No", ""), ("Relative", "No", "None"),
                   ("Relative", "", ""), ("Relative Distance", "No", "None"), ("Rel", "No", ""),
                   ("Relative", "No", "Program Determined"), ("Absolute", "No", "")]


def _table_key(keys, *words, avoid=()) -> str:
    for k in keys:
        lk = k.lower()
        if all(w in lk for w in words) and not any(a in lk for a in avoid):
            return k
    return ""


def _rows_for(fields, rows) -> tuple:
    """Table data for ``rows`` ({normalised field: value}); fields a row does not name are blank."""
    norm = [_norm(f) for f in fields]
    data = []
    for row in rows:
        data += [str(row[n]) if n in row else "" for n in norm]
    return data


def _write_rows(sm, key: str, rows: list) -> tuple:
    """Append ``rows`` to table ``key`` (opened for editing, or started from its importable fields).
    Returns (error or '', what was written)."""
    opened, err = _open(sm, key)
    if opened is None:
        return err, ""
    version, fields, n, data = opened
    norm = {_norm(f) for f in fields}
    missing = sorted({k for r in rows for k in r} - norm)
    data = list(data) + _rows_for(fields, rows)
    wrote = "; ".join("{" + ", ".join(f"{k}={v}" for k, v in r.items() if k in norm) + "}" for r in rows)
    if missing:
        wrote += f" (not in the table: {missing})"
    return _write(sm, key, version, fields, n + len(rows), data), wrote


def _display(sm, key: str, match: str = "") -> str:
    """Rows of a table as ETABS displays it (also for read-only tables such as the hinge summary),
    only those containing ``match``."""
    try:
        r = sm.DatabaseTables.GetTableForDisplayArray(key, [], "", 0, [], 0, [])
        if r[-1] != 0:
            return f"GetTableForDisplayArray returned {r[-1]}"
        fields, n, data = [str(f) for f in r[2]], int(r[3]), [str(v) for v in r[4]]
        nf = len(fields)
        rows = [data[k * nf:(k + 1) * nf] for k in range(n)]
        rows = [rw for rw in rows if not match or match in rw]
        return "; ".join("{" + ", ".join(f"{f}={v}" for f, v in zip(fields, rw) if v != "") + "}"
                         for rw in rows) or "(no row)"
    except Exception as exc:
        return f"GetTableForDisplayArray: {exc}"


def _frame_hinges_api(sm, fname: str) -> str:
    """FrameObj.GetHingeAssigns(_1) exists in ETABS 23 (no setter): read what it says, any signature."""
    out = []
    for m in ("GetHingeAssigns_1", "GetHingeAssigns"):
        if not hasattr(sm.FrameObj, m):
            continue
        for args in ((fname, 0, [], [], [], [], []), (fname, 0, [], [], [], []), (fname, 0, [], [], []),
                     (fname, 0, [], []), (fname, 0, [], [], [], [], [], []), (fname, 0, [], [], [], [], [], [], [])):
            try:
                r = getattr(sm.FrameObj, m)(*args)
                if (r[-1] if isinstance(r, (list, tuple)) else r) == 0:
                    out.append(f"{m}: {list(r)}"[:400])
                    break
            except Exception:
                continue
        else:
            out.append(f"{m}: no argument list answered")
    return "; ".join(out) or "no GetHingeAssigns"


def assign_hinges(sm, case: dict, R, names: dict, po: dict) -> str:
    """Define the user hinge property in "Hinge Definitions - Moment M3" and assign it at both ends of
    every hinged frame in "Frame Assignments - Hinge - User"; R.Stop with what ETABS said otherwise."""
    mps = set(po["hinges"].values())
    if len(mps) != 1:
        raise R.Stop("C3 hinges: one Mp per case is supported")
    mp = float(mps.pop())
    api = {"FrameObj": _api_names(sm.FrameObj, "hinge"), "PropHinge": hasattr(sm, "PropHinge"),
           "PropFrame": _api_names(sm.PropFrame, "hinge")}
    R.say(f"  hinge API: {api}")
    types = _import_types(sm)
    keys = [k for k in all_keys(sm, R) if "hinge" in k.lower()]
    R.say(f"  hinge tables ({len(keys)}): {'; '.join(f'{k} [{types.get(k, chr(63))}]' for k in keys)}")
    def_key = (_table_key(keys, "hinge", "definition", "moment m3", avoid=("force controlled", "generated"))
               or _table_key(keys, "hinge", "prop", "m3", avoid=("force controlled", "generated", "assign")))
    asg_key = _table_key(keys, "frame assignments", "hinge", "user")
    sum_key = _table_key(keys, "frame assignments", "hinge", "summary")
    described = describe(sm, R, [k for k in (def_key, asg_key, sum_key) if k], types)
    tried = []

    def fail(why: str):
        return R.Stop(f"frame hinges: {why}. Tried: " + " | ".join(tried[-6:]) + f" || API: {api} || tables: "
                      + " || ".join(described) + " || every hinge table: " + "; ".join(keys))

    if not def_key:
        raise fail("no hinge definition table for M3 (looked for 'Hinge Definitions - Moment M3')")
    if not asg_key:
        raise fail("no user hinge assignment table (looked for 'Frame Assignments - Hinge - User')")
    # 1. the user hinge property
    defined = False
    for fd in FD_TYPES:
        first = {"name": HINGE, "issymmetric": "Yes", "addbc": "No", "addcd": "No", "fdtype": fd,
                 "useyldforce": "No", "useylddispl": "No", "fdposforsf": mp, "fdposdispsf": 1.0,
                 "fdnegforsf": mp, "fdnegdispsf": 1.0, "hystype": "Isotropic"}
        rows = [dict(first if k == 0 else {"name": HINGE}, point=p, force=f, displ=d)
                for k, (p, f, d) in enumerate(EPP_POINTS)]
        err, wrote = _write_rows(sm, def_key, rows)
        if not err:
            defined = True
            break
        tried.append(f"define in {def_key} (FDType {fd}): {err} [wrote {wrote[:500]}]")
    if not defined:
        raise fail(f"the user hinge property {HINGE} (M3, elastic-perfectly-plastic, Mp {mp:g}) was refused")
    R.say(f"  hinge property (read back) {def_key}: {_row_of(sm, def_key, HINGE)[:900]}")
    # 2. assignments at both ends of each hinged frame (relative distance 0 and 1)
    by_id = {f["id"]: f for f in case["frames"]}
    targets = []
    for fid in po["hinges"]:
        f = by_id[fid]
        L = math.dist(f["i"], f["j"])
        targets += [(names.get(fid, fid), rel, rel * L) for rel in (0.0, 1.0)]
    assigned = False
    for dist_type, fib, low in ASSIGN_VARIANTS:
        rows = []
        for fname, rel, absd in targets:
            row = {"uniquename": fname, "assignhinge": HINGE, "disttype": dist_type, "reldist": rel,
                   "absdist": absd}
            if fib:
                row["savefibresp"] = fib
            if low:
                row["lenowtype"] = low
            rows.append(row)
        err, wrote = _write_rows(sm, asg_key, rows)
        if not err:
            assigned = True
            R.say(f"  hinge assignment: DistType {dist_type!r}, SaveFibResp {fib!r}, LenOWType {low!r}")
            break
        tried.append(f"assign in {asg_key} (DistType {dist_type!r}, SaveFibResp {fib!r}, LenOWType {low!r}): "
                     f"{err} [wrote {wrote[:400]}]")
    if not assigned:
        raise fail(f"{HINGE} defined but its assignment was refused")
    fname0 = targets[0][0]
    R.say(f"  hinges (read back) {asg_key}: {_row_of(sm, asg_key, fname0)[:600]}")
    if sum_key:
        R.say(f"  hinges (summary) {sum_key}: {_display(sm, sum_key, fname0)[:600]}")
    R.say(f"  hinges (API) {_frame_hinges_api(sm, fname0)}")
    return f"{HINGE} (Mp {mp:g}) at both ends of {', '.join(po['hinges'])}"


# ── hooks ───────────────────────────────────────────────────────────────────
def build(sm, case, R, point, names) -> None:
    td = case.get("timeDependent")
    if td:
        mt = next(m for m in case["materials"] if m["name"] == td["material"])
        set_concrete(sm, R, mt)
        R.say(f"  time-dependent material {td['material']}: {set_time_dependent(sm, R, td)}")
    st = case.get("tdStaged")
    if st:
        R.say(f"  staged case {st['name']}: {set_td_staged(sm, case, R, point, names, st)}")
    for po in case.get("extPushover", []):
        R.say(f"  hinges: {assign_hinges(sm, case, R, names, po)}")
        bare = {k: v for k, v in po.items() if k != "hinges"}      # the stock case, hinges already in
        R.say(f"  pushover {po['name']}: {R.set_pushover(sm, case, bare, point)}")


def _uz(res: dict, cname: str, at):
    c = res["cases"].get(cname) or {}
    js = c.get("joints") or []
    if not js:
        return None
    best = min(js, key=lambda j: math.dist(j["xyz"], at))
    return best["u"][2] if math.dist(best["xyz"], at) < 1e-3 else None


def results(sm, case, R, names, res) -> None:
    """The same named scalars as sf_ext_timedep.values."""
    keys = {c.get("key") for c in case["checks"] if c["quantity"] == "value"}
    st = case.get("tdStaged") or {}
    if not st:
        return
    top = max((p for f in case["frames"] for p in (f["i"], f["j"])), key=lambda p: p[2])
    u_td = _uz(res, st["name"], top)
    if "phi" in keys and st.get("pattern"):
        u_el = _uz(res, st["pattern"], top)
        if u_td is not None and u_el:
            res["values"]["phi"] = u_td / u_el - 1.0
    if "eps_sh" in keys and u_td is not None:
        res["values"]["eps_sh"] = -u_td / top[2]
