"""``SapModel.Results`` / ``SapModel.Results.Setup`` of the OAPI facade.

Results come from the SkyFrame :class:`AnalysisResults` of the last
``Analyze.RunAnalysis`` and are returned in the CSI array layout, in the
present units:

* ``JointDispl`` / ``JointDisplAbs`` / ``JointReact`` -- global axes;
* ``FrameForce`` -- P V2 V3 T M2 M3 at the member output stations
  (CSI sign convention: tension +, M3 sagging + for gravity beams);
* ``BaseReact`` -- about the global origin (gx = gy = gz = 0);
* ``ModalPeriod`` / ``ModalParticipatingMassRatios`` / ``StoryDrifts``.

Envelope combinations return two rows per item (StepType ``Max`` /
``Min``); modal results use StepType ``Mode`` and StepNum = mode number.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

FAIL = 1


class cAnalysisResultsSetup:
    """Case / combo output selection.  Unlike CSI (nothing selected),
    every case and combo is selected until the first deselect call."""

    def __init__(self, sm):
        self._sm = sm
        self._reset()

    def _reset(self) -> None:
        self._all = True
        self._cases: set = set()
        self._combos: set = set()

    def DeselectAllCasesAndCombosForOutput(self):
        self._all = False
        self._cases.clear()
        self._combos.clear()
        return 0

    def SetCaseSelectedForOutput(self, Name, Selected=True):
        if Name not in self._sm._st["cases"]:
            return FAIL
        self._materialize()
        (self._cases.add if Selected else self._cases.discard)(Name)
        return 0

    def SetComboSelectedForOutput(self, Name, Selected=True):
        if Name not in self._sm._st["combos"]:
            return FAIL
        self._materialize()
        (self._combos.add if Selected else self._combos.discard)(Name)
        return 0

    def GetCaseSelectedForOutput(self, Name, Selected=False):
        if Name not in self._sm._st["cases"]:
            return [False, FAIL]
        return [self._all or Name in self._cases, 0]

    def GetComboSelectedForOutput(self, Name, Selected=False):
        if Name not in self._sm._st["combos"]:
            return [False, FAIL]
        return [self._all or Name in self._combos, 0]

    def _materialize(self) -> None:
        if self._all:
            self._all = False
            self._cases = set(self._sm._st["cases"])
            self._combos = set(self._sm._st["combos"])

    def selected(self) -> List[Tuple[str, str]]:
        """[(name, "case" | "modal" | "combo")] in definition order."""
        st = self._sm._st
        out = []
        for n, c in st["cases"].items():
            if (self._all or n in self._cases) and n not in st["not_run"]:
                out.append((n, "modal" if c["type"] == "modal" else "case"))
        for n in st["combos"]:
            if self._all or n in self._combos:
                out.append((n, "combo"))
        return out

    # CSI options that do not change SkyFrame output
    def SetOptionModalHist(self, Value):
        return 0

    def SetOptionDirectHist(self, Value):
        return 0

    def SetOptionMultiStepStatic(self, Value):
        return 0


class cAnalysisResults:
    def __init__(self, sm):
        self._sm = sm
        self.Setup = cAnalysisResultsSetup(sm)

    # ---------------------------------------------------------- helpers
    @property
    def _res(self):
        return self._sm._results

    def _tag_of(self) -> Dict[str, int]:
        res = self._res
        idx = {}
        for t, xyz in res.nodes.items():
            idx.setdefault(tuple(round(c, 6) for c in xyz), int(t))
        out = {}
        for nm, p in self._sm._st["points"].items():
            t = idx.get(tuple(round(c, 6) for c in p))
            if t is not None:
                out[nm] = t
        return out

    def _points(self, Name, item) -> Optional[List[str]]:
        st = self._sm._st
        it = int(item or 0)
        if it in (0, 1):
            return [Name] if Name in st["points"] else None
        if it == 2:
            g = st["groups"].get(Name)
            if g is None:
                return None
            pts = list(g["points"])
            for f in g["frames"]:
                if f in st["frames"]:
                    pts += [st["frames"][f]["i"], st["frames"][f]["j"]]
            for a in g["areas"]:
                if a in st["areas"]:
                    pts += st["areas"][a]["pts"]
            return list(dict.fromkeys(pts))
        return []

    def _frames(self, Name, item) -> Optional[List[str]]:
        st = self._sm._st
        it = int(item or 0)
        if it in (0, 1):
            return [Name] if Name in st["frames"] else None
        if it == 2:
            g = st["groups"].get(Name)
            return None if g is None else [f for f in g["frames"]
                                           if f in st["frames"]]
        return []

    def _states(self, allow_modal: bool = False):
        """Yield (name, step_type, step_num, CaseResults|modal-shape)."""
        res = self._res
        for name, kind in self.Setup.selected():
            if kind == "case":
                cr = res.cases.get(name)
                if cr is not None:
                    yield name, "", 0.0, cr
            elif kind == "combo":
                cr = res.combos.get(name)
                if cr is None:
                    continue
                if getattr(cr, "minima", None) is not None:
                    yield name, "Max", 0.0, cr
                    yield name, "Min", 0.0, cr.minima
                else:
                    yield name, "", 0.0, cr
            elif allow_modal and res.modal is not None:
                for mode in sorted(res.modal.shapes, key=int):
                    yield name, "Mode", float(mode), \
                        {"node_disp": res.modal.shapes[mode]}

    @staticmethod
    def _disp(state) -> dict:
        return state["node_disp"] if isinstance(state, dict) \
            else state.node_disp

    # ---------------------------------------------------------- joints
    def JointDispl(self, Name, ItemTypeElm, NumberResults=0, Obj=None,
                   Elm=None, LoadCase=None, StepType=None, StepNum=None,
                   U1=None, U2=None, U3=None, R1=None, R2=None, R3=None):
        empty = [0, [], [], [], [], [], [], [], [], [], [], []]
        if self._res is None:
            return empty + [FAIL]
        names = self._points(Name, ItemTypeElm)
        if names is None:
            return empty + [FAIL]
        tags = self._tag_of()
        u = self._sm._units
        cols = [[] for _ in range(11)]
        for case, stype, snum, state in self._states(allow_modal=True):
            disp = self._disp(state)
            for p in names:
                t = tags.get(p)
                v = disp.get(t, disp.get(str(t))) if t is not None else None
                if v is None:
                    continue
                for k, x in enumerate((p, p, case, stype, snum,
                                       u.out(v[0], "length"),
                                       u.out(v[1], "length"),
                                       u.out(v[2], "length"),
                                       float(v[3]), float(v[4]),
                                       float(v[5]))):
                    cols[k].append(x)
        return [len(cols[0])] + cols + [0]

    JointDisplAbs = JointDispl

    def JointReact(self, Name, ItemTypeElm, NumberResults=0, Obj=None,
                   Elm=None, LoadCase=None, StepType=None, StepNum=None,
                   F1=None, F2=None, F3=None, M1=None, M2=None, M3=None):
        empty = [0, [], [], [], [], [], [], [], [], [], [], []]
        if self._res is None:
            return empty + [FAIL]
        names = self._points(Name, ItemTypeElm)
        if names is None:
            return empty + [FAIL]
        tags = self._tag_of()
        u = self._sm._units
        cols = [[] for _ in range(11)]
        for case, stype, snum, state in self._states():
            reac = state.reactions
            for p in names:
                t = tags.get(p)
                v = reac.get(t, reac.get(str(t))) if t is not None else None
                if v is None:
                    continue
                for k, x in enumerate((p, p, case, stype, snum,
                                       u.out(v[0], "force"),
                                       u.out(v[1], "force"),
                                       u.out(v[2], "force"),
                                       u.out(v[3], "moment"),
                                       u.out(v[4], "moment"),
                                       u.out(v[5], "moment"))):
                    cols[k].append(x)
        return [len(cols[0])] + cols + [0]

    # ---------------------------------------------------------- frames
    def FrameForce(self, Name, ItemTypeElm, NumberResults=0, Obj=None,
                   ObjSta=None, Elm=None, ElmSta=None, LoadCase=None,
                   StepType=None, StepNum=None, P=None, V2=None, V3=None,
                   T=None, M2=None, M3=None):
        empty = [0] + [[] for _ in range(13)]
        if self._res is None:
            return empty + [FAIL]
        names = self._frames(Name, ItemTypeElm)
        if names is None:
            return empty + [FAIL]
        u = self._sm._units
        cols = [[] for _ in range(13)]
        for case, stype, snum, state in self._states():
            for f in names:
                sta = state.member_stations.get(f)
                if not sta:
                    continue
                for i, x in enumerate(sta["x"]):
                    xs = u.out(x, "length")
                    vals = (f, xs, f, xs, case, stype, snum,
                            u.out(sta["N"][i], "force"),
                            u.out(sta["V2"][i], "force"),
                            u.out(sta["V3"][i], "force"),
                            u.out(sta["T"][i], "moment"),
                            u.out(sta["M2"][i], "moment"),
                            u.out(sta["M3"][i], "moment"))
                    for k, v in enumerate(vals):
                        cols[k].append(v)
        return [len(cols[0])] + cols + [0]

    # ---------------------------------------------------------- global
    def BaseReact(self, NumberResults=0, LoadCase=None, StepType=None,
                  StepNum=None, Fx=None, Fy=None, Fz=None, Mx=None,
                  ParamMy=None, Mz=None, gx=0.0, gy=0.0, gz=0.0):
        empty = [0] + [[] for _ in range(9)] + [0.0, 0.0, 0.0]
        if self._res is None:
            return empty + [FAIL]
        u = self._sm._units
        cols = [[] for _ in range(9)]
        for case, stype, snum, state in self._states():
            b = state.base
            vals = (case, stype, snum, u.out(b["FX"], "force"),
                    u.out(b["FY"], "force"), u.out(b["FZ"], "force"),
                    u.out(b["MX"], "moment"), u.out(b["MY"], "moment"),
                    u.out(b["MZ"], "moment"))
            for k, v in enumerate(vals):
                cols[k].append(v)
        return [len(cols[0])] + cols + [0.0, 0.0, 0.0, 0]

    def ModalPeriod(self, NumberResults=0, LoadCase=None, StepType=None,
                    StepNum=None, Period=None, Frequency=None,
                    CircFreq=None, EigenValue=None):
        if self._res is None:
            return [0] + [[] for _ in range(7)] + [FAIL]
        cols = [[] for _ in range(7)]
        modal = self._res.modal
        for name, kind in self.Setup.selected():
            if kind != "modal" or modal is None:
                continue
            for i, T in enumerate(modal.periods):
                f = 1.0 / T if T > 0 else 0.0
                w = 2.0 * math.pi * f
                for k, v in enumerate((name, "Mode", float(i + 1), T, f, w,
                                       w * w)):
                    cols[k].append(v)
        return [len(cols[0])] + cols + [0]

    def ModalParticipatingMassRatios(self, NumberResults=0, LoadCase=None,
                                     StepType=None, StepNum=None,
                                     Period=None, *args):
        if self._res is None:
            return [0] + [[] for _ in range(16)] + [FAIL]
        cols = [[] for _ in range(16)]
        modal = self._res.modal
        for name, kind in self.Setup.selected():
            if kind != "modal" or modal is None:
                continue
            sums = [0.0] * 6
            for i, p in enumerate(modal.participation):
                r = [float(p.get(k, 0.0)) for k in ("ux", "uy", "uz",
                                                    "rx", "ry", "rz")]
                sums = [a + b for a, b in zip(sums, r)]
                row = (name, "Mode", float(i + 1),
                       float(modal.periods[i]) if i < len(modal.periods)
                       else 0.0, r[0], r[1], r[2], sums[0], sums[1],
                       sums[2], r[3], r[4], r[5], sums[3], sums[4],
                       sums[5])
                for k, v in enumerate(row):
                    cols[k].append(v)
        return [len(cols[0])] + cols + [0]

    def StoryDrifts(self, NumberResults=0, Story=None, LoadCase=None,
                    StepType=None, StepNum=None, Direction=None,
                    Drift=None, Label=None, X=None, Y=None, Z=None):
        """Diaphragm/story drift ratio per story and direction (ETABS)."""
        if self._res is None:
            return [0] + [[] for _ in range(10)] + [FAIL]
        u = self._sm._units
        elev = dict(self._res.story_elev)
        cols = [[] for _ in range(10)]
        for case, stype, snum, state in self._states():
            for s in self._res.story_order:
                row = state.story.get(s)
                if not row:
                    continue
                for d, key in (("X", "drift_x"), ("Y", "drift_y")):
                    vals = (s, case, stype, snum, d, float(row.get(key, 0.0)),
                            "", 0.0, 0.0, u.out(elev.get(s, 0.0), "length"))
                    for k, v in enumerate(vals):
                        cols[k].append(v)
        return [len(cols[0])] + cols + [0]
