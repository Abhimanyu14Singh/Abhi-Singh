"""SDNF v3.0 (Steel Detailing Neutral File) reader and writer -- the
documented subset (docs/INTEROP.md, CONTRACT "SAP2000 / SAFE / steel
interop").

``export_sdnf(model, units="m")`` -> ``(text, report)``;
``read_sdnf(text, sections_from=None)`` -> ``(model, warnings)``.

Packets written / read::

    Packet 00                                  (title)
    "<company>" "<structure>" "<code>" "<date>" "<time>" <rev> "<length unit>" "<force unit>"

    Packet 10                                  (linear members)
    <member count>
    <id> "<type>" "<piece mark>" "<section>" "<grade>" <rotation> <mirror x> <mirror y> <cardinal point>
    <x1> <y1> <z1> <x2> <y2> <z2>              (member end points)
    <dx1> <dy1> <dz1> <dx2> <dy2> <dz2>        (end offsets, global)
    "<release start>" "<release end>"          (6 x 0/1 for U1 U2 U3 R1 R2 R3, 1 = free)

* ``type`` is Column / Beam / Vertical Brace / Horizontal Brace / Brace;
  ``section`` is the database shape name for database sections
  (``W14X90``, ``IPE300`` ...), else the SkyFrame section name; ``grade`` is
  the material name; ``rotation`` is the local-axis angle in degrees;
  ``cardinal point`` 1..11 (10 = centroid).
* Other packets (20 plates, 30 holes, 40 connections, ...) are skipped by
  the reader with a warning; comment lines start with ``#`` or ``!``.
* Reader: a ``section`` found in ``sections_from`` (the current model) is
  copied; else a steel-database shape (aisc, eurocode, bs, as_nzs, is808,
  in that order); else a 0.3 x 0.3 m placeholder with a warning.  Each
  ``grade`` becomes a steel material (E = 200 GPa, nu = 0.3, 77 kN/m3)
  unless ``sections_from`` has it.  Stories come from the distinct end
  elevations; supports are fixed at the lowest level.
"""

from __future__ import annotations

import datetime
import math
import re
import shlex
from typing import Dict, List, Optional, Tuple

__all__ = ["export_sdnf", "read_sdnf", "LENGTH_UNITS"]

LENGTH_UNITS = {"m": ("Meters", 1.0), "mm": ("Millimeters", 1e-3),
                "in": ("Inches", 0.0254), "ft": ("Feet", 0.3048)}
_UNIT_BACK = {"METERS": 1.0, "METRES": 1.0, "M": 1.0, "MILLIMETERS": 1e-3,
              "MILLIMETRES": 1e-3, "MM": 1e-3, "INCHES": 0.0254,
              "IN": 0.0254, "FEET": 0.3048, "FT": 0.3048}
_DOFS = ("P", "V2", "V3", "T", "M2", "M3")
_STDS = ("aisc", "eurocode", "bs", "as_nzs", "is808")


def _q(s) -> str:
    return '"' + str(s).replace('"', "'") + '"'


def _n(v) -> str:
    r = repr(float(v))
    return r[:-2] if r.endswith(".0") else ("0" if r == "-0.0" else r)


def _mtype(mem) -> str:
    if mem.kind == "column":
        return "Column"
    if mem.kind == "beam":
        return "Beam"
    dz = abs(mem.pj[2] - mem.pi[2])
    return "Horizontal Brace" if dz < 1e-9 else "Vertical Brace"


def export_sdnf(model, units: str = "m", company: str = "SkyFrame",
                code: str = "") -> Tuple[str, dict]:
    import copy
    from skyframe.core import frame_axes as _fa
    from skyframe.core import releases as _rel
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    if _fa.model_convention(model) != "etabs":
        model = copy.deepcopy(model)
        _fa.convert_model_to_etabs(model)
    if units not in LENGTH_UNITS:
        raise ValueError(f"units must be one of {sorted(LENGTH_UNITS)}")
    lname, lf = LENGTH_UNITS[units]
    L = lambda v: _n(float(v) / lf)                    # noqa: E731
    now = datetime.datetime(2000, 1, 1)                # deterministic text
    out = ["Packet 00",
           " ".join([_q(company), _q(model.name), _q(code or "None"),
                     _q(now.strftime("%m/%d/%Y")),
                     _q(now.strftime("%H:%M:%S")), "1", _q(lname),
                     _q("Kilonewtons")]),
           "Packet 10", str(len(model.members))]
    skipped: List[str] = []
    ids: Dict[str, int] = {}
    for k, mem in enumerate(model.members, start=1):
        sec = model.sections[mem.section]
        name = sec.db_ref["name"] if sec.db_ref else sec.name
        ids[mem.uid] = k
        out.append(" ".join([str(k), _q(_mtype(mem)), _q(mem.uid), _q(name),
                             _q(sec.material), _n(mem.angle), "0", "0",
                             str(int(mem.cardinal_point))]))
        out.append(" ".join(L(v) for v in (*mem.pi, *mem.pj)))
        jo = mem.joint_offsets or {}
        if jo and jo.get("system", "global") != "global":
            skipped.append(f"frame {mem.uid}: local joint offsets")
            jo = {}
        oi = list(jo.get("i", [0.0, 0.0, 0.0]))
        oj = list(jo.get("j", [0.0, 0.0, 0.0]))
        out.append(" ".join(L(v) for v in (*oi, *oj)))
        eff = _rel.effective(mem)
        if any(v > 0 for e in eff.values() for v in e.values()):
            skipped.append(f"frame {mem.uid}: partial-fixity springs "
                           "(written as free)")
        rs = ["".join("1" if d in eff.get(end, {}) else "0" for d in _DOFS)
              for end in ("i", "j")]
        out.append(f"{_q(rs[0])} {_q(rs[1])}")
    if model.shells:
        skipped.append(f"{len(model.shells)} wall / slab region(s) "
                       "(SDNF carries steel members)")
    if model.links:
        skipped.append(f"{len(model.links)} link(s)")
    text = "\n".join(out) + "\n"
    return text, {"format": "sdnf", "version": "3.0", "units": lname,
                  "written": {"members": len(model.members)},
                  "skipped": list(dict.fromkeys(skipped))}


def _lines(text: str) -> List[str]:
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if s and not s.startswith(("#", "!")):
            out.append(s)
    return out


def _toks(line: str) -> List[str]:
    try:
        return shlex.split(line)
    except ValueError:
        return line.replace('"', " ").split()


def read_sdnf(text: str, sections_from=None) -> Tuple[object, List[str]]:
    from skyframe.core import frame_axes as _fa
    from skyframe.core import releases as _rel
    from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                     Story)
    warns: List[str] = []
    lines = _lines(text)
    lf = 1.0
    model = BuildingModel(name="SDNF import")
    _fa.as_new_model(model)
    members = []
    i = 0
    while i < len(lines):
        m = re.match(r"^packet\s+(\d+)", lines[i], re.I)
        if not m:
            i += 1
            continue
        pk = int(m.group(1))
        i += 1
        if pk == 0 and i < len(lines):
            t = _toks(lines[i])
            if len(t) > 1:
                model.name = t[1] or model.name
            for tok in t[6:]:
                if tok.upper() in _UNIT_BACK:
                    lf = _UNIT_BACK[tok.upper()]
                    break
            i += 1
        elif pk == 10 and i < len(lines):
            try:
                n = int(float(_toks(lines[i])[0]))
            except (ValueError, IndexError):
                warns.append("Packet 10: bad member count")
                break
            i += 1
            for _k in range(n):
                if i + 2 >= len(lines):
                    warns.append("Packet 10: truncated member records")
                    break
                head = _toks(lines[i])
                try:
                    xyz = [float(v) for v in _toks(lines[i + 1])[:6]]
                    off = [float(v) for v in _toks(lines[i + 2])[:6]]
                except ValueError:
                    warns.append(f"Packet 10: bad record near line "
                                 f"{lines[i]!r}")
                    i += 4
                    continue
                rel = _toks(lines[i + 3]) if i + 3 < len(lines) else []
                members.append((head, xyz, off, rel))
                i += 4
        else:
            warns.append(f"Packet {pk:02d} skipped (not in the supported "
                         "subset)")
    if not members:
        return model, warns + ["no linear members (Packet 10)"]

    # materials / sections
    src = sections_from
    for head, *_ in members:
        grade = head[4] if len(head) > 4 else "STEEL"
        if grade in model.materials:
            continue
        if src is not None and grade in src.materials:
            import copy
            model.add_material(copy.deepcopy(src.materials[grade]))
        else:
            model.add_material(Material(grade, 2.0e8, 0.3, 77.0,
                                        material_type="steel"))
    for head, *_ in members:
        size = head[3] if len(head) > 3 else "SEC"
        grade = head[4] if len(head) > 4 else "STEEL"
        if size in model.sections:
            continue
        sec = None
        if src is not None and size in src.sections:
            import copy
            sec = copy.deepcopy(src.sections[size])
            if sec.material not in model.materials:
                model.add_material(copy.deepcopy(
                    src.materials[sec.material]))
        if sec is None:
            from skyframe.core.steel_db import section_from_db
            for std in _STDS:
                try:
                    sec = section_from_db(std, size, grade, size)
                    break
                except (KeyError, ValueError):
                    continue
        if sec is None:
            sec = FrameSection.rectangular(size, grade, 0.3, 0.3)
            warns.append(f"section {size!r}: not in a steel database or "
                         "the current model -- 0.3 x 0.3 m placeholder")
        model.add_section(sec)

    zs = sorted({round(c * lf, 9) for _h, xyz, _o, _r in members
                 for c in (xyz[2], xyz[5])})
    if len(zs) > 1:
        model.stories = []
        for k in range(1, len(zs)):
            model.stories.append(Story(f"Story{k}", zs[k] - zs[k - 1],
                                       zs[k]))

    def story_of(z):
        for s in model.stories:
            if abs(s.elevation - z) < 1e-6:
                return s.name
        above = [s for s in model.stories if s.elevation > z]
        return min(above, key=lambda s: s.elevation).name if above else ""

    used = set()
    for head, xyz, off, rel in members:
        uid = head[2] if len(head) > 2 and head[2] else head[0]
        while uid in used:
            uid += "_"
        used.add(uid)
        typ = head[1].lower() if len(head) > 1 else ""
        pi = tuple(v * lf for v in xyz[:3])
        pj = tuple(v * lf for v in xyz[3:6])
        kind = "column" if "column" in typ else (
            "beam" if "beam" in typ else "brace")
        if not typ:
            dx, dy, dz = (pj[k] - pi[k] for k in range(3))
            kind = "column" if math.hypot(dx, dy) < 1e-9 else (
                "beam" if abs(dz) < 1e-9 else "brace")
        size = head[3] if len(head) > 3 else "SEC"
        try:
            ang = float(head[5]) if len(head) > 5 else 0.0
            cp = int(float(head[8])) if len(head) > 8 else 10
        except ValueError:
            ang, cp = 0.0, 10
        jo = None
        if any(abs(v) > 0 for v in off):
            jo = {"i": [v * lf for v in off[:3]],
                  "j": [v * lf for v in off[3:6]], "system": "global"}
        try:
            mem = model.add_member(kind, size, pi, pj,
                                   story=story_of(max(pi[2], pj[2])),
                                   uid=uid, angle=ang,
                                   cardinal_point=cp if 1 <= cp <= 11
                                   else 10, joint_offsets=jo)
        except ValueError as exc:
            warns.append(f"member {uid}: {exc}")
            continue
        if len(rel) >= 2:
            eff = {}
            for end, flags in zip(("i", "j"), rel[:2]):
                flags = flags.strip()
                if len(flags) == 6 and set(flags) <= {"0", "1"}:
                    e = {d: 0.0 for d, f in zip(_DOFS, flags) if f == "1"}
                    if e:
                        eff[end] = e
            if eff:
                _rel.set_effective(mem, eff)
    _fa.sync_members(model)
    model.validate()
    return model, warns
