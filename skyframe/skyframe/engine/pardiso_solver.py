"""Intel MKL PARDISO fast path for the LINEAR analyses (Wave E2).

CONTRACT "PARDISO linear solver (MKL)".  docs/PERFORMANCE.md "PARDISO".

What it does
------------
For the linear flows that run on the engine's reused elastic domain —
linear static cases, centre-of-rigidity unit loads, response-spectrum
modal statics, the massed-DOF condensation columns and mode shapes, the
sparse (shift-invert) eigen solve, Ritz vectors and the buckling
reference / Lanczos solves — the global stiffness is factored ONCE by a
symmetric multithreaded sparse direct solver (MKL PARDISO through
``pypardiso``, METIS nested-dissection ordering) and every right-hand side
is a forward/back substitution, blocks of right-hand sides in one call.

How the matrices are obtained (approach (a): from OpenSees itself)
------------------------------------------------------------------
The OpenSees build of this engine exposes no sparse system matrix
(``printA`` returns a matrix only for the dense ``FullGeneral`` system) and
no element stiffness response.  So:

1. the standard elastic build is RECORDED (every domain-building
   ``ops.*`` call, in order) while the engine builds it;
2. right after the build each DISTINCT element (element type + its
   non-tag arguments + the canonical definition of its geometric
   transformation + node coordinates relative to its first node) is
   instantiated alone in a scratch domain carrying the same material /
   section definitions, and its stiffness and mass matrices are read from
   OpenSees with the ``GimmeMCK`` integrator on a ``FullGeneral`` system
   (``printA``) — the exact OpenSees element matrices.  Elements are
   translation invariant, so a regular building has only a few hundred
   distinct elements (memoised per process);
3. the recorded build is REPLAYED into a fresh domain (the identical
   domain: same calls, same order) so the engine's result recovery runs
   on it unchanged;
4. ``K`` and ``M`` are assembled in numpy/scipy over the physical dofs and
   reduced with the constraint transformation ``T`` of the Transformation
   handler (rigid diaphragm / rigidLink beam|bar / equalDOF matrices, the
   same formulas as OpenSees' MP_Constraint classes): ``K_r = T^T K T``.

How a domain-evaluated solve runs (exact OpenSees semantics)
------------------------------------------------------------
The engine applies the case's loads exactly as before, then
``_setup_linear_reused`` hands over to :func:`prepare_solve`, which works
on a step-only OpenSees analysis (``Diagonal`` system, ``Linear
-factorOnce``): after its priming step every further step applies the
loads at the step's factor and updates / commits every element WITHOUT
changing the displacements and without forming any element tangent
(probed on openseespy 3.7.1):

* right-hand side: the nodal loads of pattern 1 (``getNodeLoadData``);
  when the pattern carries element loads, one such step at factor 1
  applies them and each loaded element's ``eleForce`` at zero
  displacement is its OpenSees fixed-end force (subtracted);
* PARDISO solves ``K_r x = T^T P``; the physical displacements ``T x`` are
  written into the domain (``setNodeDisp -commit``);
* the caller's own ``ops.analyze(1)`` then runs as one more such step
  (factor 1 reached), so reactions, element forces and shell resultants
  are recovered by OpenSees from the solved displacements exactly as after
  its own solve.

Runtime exactness guard: the first solve of every factorisation checks
OpenSees' own residual ``T^T (P - sum_e eleForce_e(u))`` <=
``RESIDUAL_TOL`` relative (pure solves: a pseudo-random field).  Any
failure — or anything outside the explicit allow-list (:data:`ELEMENTS`,
:data:`UNIAXIAL`, :data:`NDMATS`, :data:`SECTIONS`, :data:`MP_COMMANDS`,
linear geometric transformation, no nonlinear members / links / springs,
no ground displacements, no initial strains, no model-wide P-Delta, the
standard elastic build) — falls back to today's OpenSees path and the
reason is reported in results ``extra.solver``.

Pure solves (condensation columns, eigen / Lanczos operators) never touch
the domain: ``u = T K_r^-1 T^T f``.
"""

from __future__ import annotations

import ctypes
import glob
import math
import os
import sys
import time
import weakref
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import openseespy.opensees as ops

# --------------------------------------------------------------------------- #
# policy constants
# --------------------------------------------------------------------------- #
#: estimated equations at/above which ``solver: "auto"`` uses PARDISO when
#: pypardiso imports (measured crossover, docs/PERFORMANCE.md "PARDISO")
PARDISO_EQ_THRESHOLD = 10000
#: max relative residual of the first solve of a factorisation
RESIDUAL_TOL = 1e-8
#: rounding (m) of relative node coordinates in the element-matrix key
COORD_DECIMALS = 10
#: memoised distinct element matrices kept per process
CACHE_CAP = 200000
THREAD_MODES = ("auto",)
#: PARDISO solves with at least this many right-hand sides use every MKL
#: thread; smaller ones run on one thread
BLOCK_THREADS_MIN_RHS = 8
MAX_THREADS = 256

# element type -> number of nodes (allow-list: linear elastic elements)
ELEMENTS = {
    "elasticBeamColumn": 2, "ElasticTimoshenkoBeam": 2, "Truss": 2,
    "zeroLength": 2, "zeroLengthSection": 2, "twoNodeLink": 2,
    "ShellMITC4": 4, "ShellDKGQ": 4, "ASDShellQ4": 4,
    "ShellDKGT": 3, "ASDShellT3": 3,
}
# beam elements carrying a geomTransf tag (last positional argument)
_TRANSF_ELEMENTS = ("elasticBeamColumn", "ElasticTimoshenkoBeam")
UNIAXIAL = ("Elastic", "Parallel", "InitStrainMaterial")
NDMATS = ("ElasticIsotropic", "ElasticOrthotropic", "PlateFiber",
          "PlateFromPlaneStress", "PlaneStress", "PlateRebar")
SECTIONS = ("Elastic", "ElasticMembranePlateSection", "LayeredShell",
            "PlateFiber", "Aggregator")
MP_COMMANDS = ("rigidDiaphragm", "rigidLink", "equalDOF")
#: domain-building commands the recorder accepts (anything else -> fallback)
BUILD_COMMANDS = frozenset(("node", "fix", "mass", "element", "section",
                            "uniaxialMaterial", "nDMaterial", "geomTransf",
                            "beamIntegration") + MP_COMMANDS)
#: side-effect-free queries (never recorded)
_QUERY = frozenset((
    "nodeCoord", "nodeDisp", "nodeVel", "nodeAccel", "nodeReaction",
    "nodeEigenvector", "nodeMass", "nodeDOFs", "nodeBounds", "nodeResponse",
    "nodeUnbalance", "nodePressure", "eleForce", "eleNodes", "eleResponse",
    "eleType", "eleDynamicalForce", "getNodeTags", "getEleTags", "getNDM",
    "getNDF", "getTime", "getLoadFactor", "getPatterns", "getFixedNodes",
    "getFixedDOFs", "getConstrainedNodes", "getConstrainedDOFs",
    "getRetainedNodes", "getRetainedDOFs", "getEleClassTags",
    "getEleLoadClassTags", "getEleLoadData", "getEleLoadTags",
    "getNodeLoadData", "getNodeLoadTags", "getCrdTransfTags",
    "getParamTags", "getParamValue", "getNP", "getPID", "getNumThreads",
    "basicDeformation", "basicForce", "basicStiffness", "sectionForce",
    "sectionDeformation", "sectionStiffness", "sectionFlexibility",
    "sectionLocation", "sectionWeight", "sectionTag", "sectionDisplacement",
    "systemSize", "version", "printModel", "printA", "printB", "printX",
    "numFact", "numIter", "testIter", "testNorm", "testNorms", "totalCPU",
    "solveCPU", "accelCPU", "getStrain", "getStress", "getTangent",
    "pyversion", "classType", "getNodeTemperature", "getDampTangent",
    "cbdiDisplacement", "OpenSeesError"))


# --------------------------------------------------------------------------- #
# MKL / pypardiso loading and threads
# --------------------------------------------------------------------------- #
_LIB: Dict[str, object] = {"tried": False, "mod": None, "err": "",
                           "mkl_rt": "", "lib": None, "threads": None}

_MKL_NAMES = ("mkl_rt.2.dll", "mkl_rt.dll", "mkl_rt.1.dll",
              "libmkl_rt.so.2", "libmkl_rt.so.3", "libmkl_rt.so",
              "libmkl_rt.2.dylib", "libmkl_rt.dylib")


def _mkl_dirs() -> List[str]:
    """Where a pip / conda / frozen MKL keeps ``mkl_rt``."""
    import site
    roots: List[str] = []
    if getattr(sys, "frozen", False):                 # PyInstaller bundle
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        roots += [base, os.path.join(base, "mkl"),
                  os.path.dirname(sys.executable)]
    for pre in (sys.prefix, sys.base_prefix, getattr(site, "USER_BASE", "")):
        if pre:
            roots += [os.path.join(pre, "Library", "bin"),
                      os.path.join(pre, "lib"), os.path.join(pre, "bin"),
                      pre]
    try:
        sps = list(site.getsitepackages()) + [site.getusersitepackages()]
    except Exception:                                   # pragma: no cover
        sps = []
    for sp in sps:                         # <prefix>/lib/pythonX/site-packages
        up = os.path.dirname(os.path.dirname(sp))
        roots += [up, os.path.join(os.path.dirname(up), "Library", "bin"),
                  os.path.join(os.path.dirname(sp), "Library", "bin")]
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def find_mkl_rt() -> str:
    """Path of the MKL runtime library (``""`` when not found)."""
    env = os.environ.get("PYPARDISO_MKL_RT")
    if env and os.path.exists(env):
        return env
    for d in _mkl_dirs():
        for nm in _MKL_NAMES:
            p = os.path.join(d, nm)
            if os.path.exists(p):
                return p
        hits = sorted(glob.glob(os.path.join(d, "*mkl_rt*")), key=len)
        if hits:
            return hits[0]
    return ""


def load():
    """The ``pypardiso`` module (imported once), or None."""
    if _LIB["tried"]:
        return _LIB["mod"]
    _LIB["tried"] = True
    if os.environ.get("SKYFRAME_PARDISO") == "0":    # A/B switch
        _LIB["err"] = "disabled by SKYFRAME_PARDISO=0"
        return None
    try:
        import scipy.sparse  # noqa: F401  (pypardiso needs scipy)
        import scipy.sparse.linalg  # noqa: F401
    except Exception as exc:                            # pragma: no cover
        _LIB["err"] = f"scipy not importable ({exc})"
        return None
    # idle OpenMP workers sleep at once instead of spin-waiting: with the
    # OpenSees process sharing the cores, spinning MKL threads were 20x
    # slower (measured); a user setting wins
    os.environ.setdefault("KMP_BLOCKTIME", "0")
    path = find_mkl_rt()
    if path and not os.environ.get("PYPARDISO_MKL_RT"):
        os.environ["PYPARDISO_MKL_RT"] = path
    if path and sys.platform == "win32":                # pragma: no cover
        # mkl_rt loads mkl_core / mkl_intel_thread / libiomp5md by name:
        # their folder must be on the DLL search path (frozen builds too)
        d = os.path.dirname(os.path.abspath(path))
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        import pypardiso  # noqa: F401
        from pypardiso.pardiso_wrapper import PyPardisoSolver
    except Exception as exc:
        _LIB["err"] = f"pypardiso not importable ({exc})"
        return None
    _LIB["mod"] = pypardiso
    _LIB["cls"] = PyPardisoSolver
    _LIB["mkl_rt"] = os.environ.get("PYPARDISO_MKL_RT") or path
    try:
        probe = PyPardisoSolver()
        _LIB["lib"] = probe.libmkl
    except Exception as exc:                            # pragma: no cover
        _LIB["mod"] = None
        _LIB["err"] = f"MKL runtime not loadable ({exc})"
        return None
    return pypardiso


def available() -> bool:
    return load() is not None


def unavailable_reason() -> str:
    load()
    return "" if _LIB["mod"] is not None else str(_LIB["err"])


def physical_cores() -> int:
    """Physical CPU cores this process may use (hyper-threads excluded)."""
    n_aff = None
    try:
        n_aff = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        pass
    phys = None
    try:
        import psutil                                    # optional
        phys = psutil.cpu_count(logical=False)
    except Exception:
        phys = None
    if not phys and sys.platform.startswith("linux"):
        try:
            cores = set()
            pid = cid = None
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if line.startswith("physical id"):
                        pid = line.split(":")[1].strip()
                    elif line.startswith("core id"):
                        cid = line.split(":")[1].strip()
                    elif not line.strip():
                        if cid is not None:
                            cores.add((pid, cid))
                        pid = cid = None
            if cid is not None:
                cores.add((pid, cid))
            phys = len(cores) or None
        except OSError:                                  # pragma: no cover
            phys = None
    if not phys and sys.platform == "win32":             # pragma: no cover
        phys = _win_physical_cores()
    if not phys:
        phys = os.cpu_count() or 1
    if n_aff:
        phys = min(phys, n_aff)
    return max(int(phys), 1)


def _win_physical_cores() -> Optional[int]:              # pragma: no cover
    """``GetLogicalProcessorInformationEx(RelationProcessorCore)`` count."""
    try:
        k32 = ctypes.windll.kernel32
        size = ctypes.c_ulong(0)
        k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not k32.GetLogicalProcessorInformationEx(0, buf,
                                                    ctypes.byref(size)):
            return None
        n, off = 0, 0
        while off < size.value:
            rec_size = int.from_bytes(buf.raw[off + 4:off + 8], "little")
            if rec_size <= 0:
                break
            n += 1
            off += rec_size
        return n or None
    except Exception:
        return None


def threads_option(model) -> object:
    opts = getattr(model, "analysis_options", None) or {}
    v = opts.get("pardiso_threads", "auto") if isinstance(opts, dict) \
        else "auto"
    return v


def resolve_threads(model) -> int:
    """MKL threads for a run: an explicit ``pardiso_threads`` int; "auto"
    = every physical core, unless ``MKL_NUM_THREADS`` is set in the
    environment (then MKL's own setting is respected)."""
    v = threads_option(model)
    if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
        return min(int(v), MAX_THREADS)
    env = os.environ.get("MKL_NUM_THREADS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return physical_cores()


def set_threads(n: int) -> int:
    """Set MKL's thread count (returns the count MKL reports)."""
    lib = _LIB.get("lib")
    if lib is None:
        return 0
    try:
        lib.MKL_Set_Num_Threads(ctypes.c_int(int(n)))
        _LIB["threads"] = int(n)
        return int(lib.MKL_Get_Max_Threads())
    except Exception:                                   # pragma: no cover
        return int(n)


# --------------------------------------------------------------------------- #
# the PARDISO factor
# --------------------------------------------------------------------------- #
class PardisoFactor:
    """One PARDISO factorisation of a sparse symmetric matrix (upper
    triangle), reused for any number of (blocks of) right-hand sides."""

    def __init__(self, A, threads: int):
        import scipy.sparse as sp
        load()
        cls = _LIB["cls"]
        self.n = A.shape[0]
        self.threads = threads
        set_threads(threads)
        self._s = cls()
        self.mtype = 2                       # real symmetric positive def.
        self.Au = sp.triu(A, format="csr")
        self.Au.sort_indices()
        self.Au.data = np.ascontiguousarray(self.Au.data, dtype=np.float64)
        self._ia = (self.Au.indptr.astype(np.int32) + 1)
        self._ja = (self.Au.indices.astype(np.int32) + 1)
        self.solves = 0
        self.rhs = 0
        t = time.perf_counter()
        try:
            self._call(12, np.zeros((self.n, 1)))
        except Exception:
            # not numerically positive definite -> Bunch-Kaufman LDL^T
            self._release()
            self._s = cls()
            self.mtype = -2
            self._call(12, np.zeros((self.n, 1)))
        self.factor_time = time.perf_counter() - t

    def _iparm(self) -> None:
        ip = self._s.iparm
        ip[:] = 0
        ip[0] = 1          # explicit iparm
        ip[1] = 2          # METIS nested-dissection ordering
        ip[9] = 8 if self.mtype == -2 else 13   # pivot perturbation 1e-8
        ip[10] = 0         # no scaling (symmetric)
        ip[12] = 0         # no weighted matching (symmetric)
        ip[17] = -1        # report nnz(L)
        ip[34] = 0         # one-based indexing

    def _call(self, phase: int, b: np.ndarray) -> np.ndarray:
        s = self._s
        if phase in (12, 11, 13):
            self._iparm()
        b = np.asfortranarray(b, dtype=np.float64)
        x = np.zeros_like(b)
        err = ctypes.c_int32(0)
        i32p = ctypes.POINTER(ctypes.c_int32)
        f64p = ctypes.POINTER(ctypes.c_double)
        nrhs = 1 if b.ndim == 1 else b.shape[1]
        s._mkl_pardiso(
            s.pt.ctypes.data_as(ctypes.POINTER(s._pt_type[0])),
            ctypes.byref(ctypes.c_int32(1)), ctypes.byref(ctypes.c_int32(1)),
            ctypes.byref(ctypes.c_int32(self.mtype)),
            ctypes.byref(ctypes.c_int32(phase)),
            ctypes.byref(ctypes.c_int32(self.n)),
            self.Au.data.ctypes.data_as(f64p),
            self._ia.ctypes.data_as(i32p), self._ja.ctypes.data_as(i32p),
            s.perm.ctypes.data_as(i32p), ctypes.byref(ctypes.c_int32(nrhs)),
            s.iparm.ctypes.data_as(i32p), ctypes.byref(ctypes.c_int32(0)),
            b.ctypes.data_as(f64p), x.ctypes.data_as(f64p),
            ctypes.byref(err))
        if err.value != 0:
            raise RuntimeError(f"PARDISO error {err.value} (phase {phase})")
        return x

    def solve(self, B: np.ndarray) -> np.ndarray:
        """``A^-1 B`` for a vector or a (n x k) block."""
        one = B.ndim == 1
        Bm = B.reshape(self.n, -1)
        # the substitution of a few right-hand sides is memory bound: one
        # thread (measured faster, and immune to oversubscription); blocks
        # use every thread
        set_threads(self.threads if Bm.shape[1] >= BLOCK_THREADS_MIN_RHS
                    else 1)
        X = self._call(33, Bm)
        self.solves += 1
        self.rhs += X.shape[1]
        return X[:, 0].copy() if one else np.ascontiguousarray(X)

    @property
    def nnz_factor(self) -> int:
        return int(self._s.iparm[17])

    def _release(self) -> None:
        try:
            self._call(-1, np.zeros((self.n, 1)))
        except Exception:
            pass

    def __del__(self):                                  # pragma: no cover
        try:
            self._release()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# recording the standard elastic build
# --------------------------------------------------------------------------- #
class _Recorder:
    def __init__(self):
        self.calls: List[Tuple[str, tuple, dict]] = []
        self.orig: Dict[str, object] = {}

    def install(self) -> None:
        for name in dir(ops):
            if name.startswith("_") or name in _QUERY:
                continue
            f = getattr(ops, name)
            if not callable(f) or isinstance(f, type):
                continue
            self.orig[name] = f
            setattr(ops, name, self._wrap(name, f))

    def _wrap(self, name, f):
        calls = self.calls

        def rec(*a, **k):
            r = f(*a, **k)
            calls.append((name, a, k))
            return r
        rec.__wrapped__ = f
        return rec

    def uninstall(self) -> None:
        for name, f in self.orig.items():
            setattr(ops, name, f)
        self.orig = {}


_REC: Dict[str, object] = {"rec": None, "eng": None, "why": ""}


def _stop_recording() -> Optional[_Recorder]:
    rec = _REC.get("rec")
    _REC["rec"] = None
    if rec is not None:
        rec.uninstall()
    return rec


def solver_option(model) -> str:
    opts = getattr(model, "analysis_options", None) or {}
    v = opts.get("solver", "auto") if isinstance(opts, dict) else "auto"
    return v if isinstance(v, str) else "auto"


def wanted(model) -> bool:
    """True when the solver policy may pick PARDISO for this model."""
    opt = solver_option(model)
    if opt == "pardiso":
        return True
    return opt == "auto"


def record_begin(eng, transf_name: str, hinge_case, pdx) -> None:
    """``_build`` hook (right after ``ops.model``): start recording the
    domain-building calls of a STANDARD elastic build when PARDISO may be
    used.  Cheap no-op otherwise."""
    _stop_recording()
    _REC["eng"] = None
    _REC["why"] = ""
    model = eng.model
    if not wanted(model):
        return
    forced = solver_option(model) == "pardiso"
    _REC["eng"] = weakref.ref(eng)       # the report needs the engine
    if os.environ.get("SKYFRAME_SLOW_PATH") == "1":
        _REC["why"] = "SKYFRAME_SLOW_PATH=1"
        return
    if hinge_case is not None or transf_name != "Linear" or pdx:
        _REC["why"] = ("not the standard elastic build (hinges / P-Delta "
                       "or corotational transformation / model-wide "
                       "P-Delta)")
        return
    if not forced:
        mesh = getattr(eng, "_mesh", None)
        npts = len(getattr(mesh, "points", ()) or ()) if mesh else 0
        if 6 * npts < PARDISO_EQ_THRESHOLD:     # upper bound of the size
            return
    if not available():
        _REC["why"] = f"pypardiso unavailable: {unavailable_reason()}"
        return
    rec = _Recorder()
    rec.install()
    _REC["rec"] = rec
    _REC["eng"] = weakref.ref(eng)


# --------------------------------------------------------------------------- #
# recipe analysis (allow-list) and element-matrix extraction
# --------------------------------------------------------------------------- #
_ELEM_CACHE: Dict[tuple, Tuple[np.ndarray, Optional[np.ndarray]]] = {}


class Unsupported(Exception):
    pass


def _linear_defs(calls) -> Tuple[list, Dict[int, tuple], str]:
    """``(definition calls, transf tag -> args, defs fingerprint)``;
    raises :class:`Unsupported` for anything not provably linear."""
    defs = []
    transf: Dict[int, tuple] = {}
    uni: Dict[int, bool] = {}
    nd: Dict[int, bool] = {}
    for name, a, k in calls:
        if name not in BUILD_COMMANDS:
            raise Unsupported(f"build command {name!r}")
        if k:
            raise Unsupported(f"keyword arguments in {name!r}")
        if name == "geomTransf":
            if a[0] != "Linear":
                raise Unsupported(f"geomTransf {a[0]!r}")
            transf[int(a[1])] = tuple(a[2:])
        elif name == "uniaxialMaterial":
            typ, tag = a[0], int(a[1])
            if typ == "Elastic":
                ok = len(a) < 5 or float(a[4]) == float(a[2])
            elif typ == "Parallel":
                ok = all(uni.get(int(t), False) for t in a[2:]
                         if not isinstance(t, str)) and "-factors" not in a
            elif typ == "InitStrainMaterial":
                ok = uni.get(int(a[2]), False)
            else:
                ok = False
            if not ok:
                raise Unsupported(f"uniaxialMaterial {typ!r} (nonlinear)")
            uni[tag] = True
            defs.append((name, a))
        elif name == "nDMaterial":
            typ, tag = a[0], int(a[1])
            if typ in ("ElasticIsotropic", "ElasticOrthotropic"):
                ok = True
            elif typ in ("PlateFiber", "PlaneStress"):
                ok = nd.get(int(a[2]), False)
            elif typ == "PlateFromPlaneStress":
                ok = nd.get(int(a[2]), False)
            elif typ == "PlateRebar":
                ok = uni.get(int(a[2]), False)
            else:
                ok = False
            if not ok:
                raise Unsupported(f"nDMaterial {typ!r}")
            nd[tag] = True
            defs.append((name, a))
        elif name == "section":
            typ = a[0]
            if typ in ("Elastic", "ElasticMembranePlateSection"):
                ok = True
            elif typ == "LayeredShell":
                n = int(a[2])
                ok = all(nd.get(int(a[3 + 2 * i]), False) for i in range(n))
            elif typ == "PlateFiber":
                ok = nd.get(int(a[2]), False)
            elif typ == "Aggregator":
                ok = True
                i = 2
                while i < len(a):
                    if a[i] == "-section":
                        ok = False         # nested sections: not checked
                        break
                    ok = ok and uni.get(int(a[i]), False)
                    i += 2
            else:
                ok = False
            if not ok:
                raise Unsupported(f"section {typ!r}")
            defs.append((name, a))
        elif name == "beamIntegration":
            defs.append((name, a))
    fp = repr(defs)
    return defs, transf, fp


def _element_template(a: tuple, transf: Dict[int, tuple]):
    """``(etype, nn, nodes, post-args, transf tag or None, key-args)``."""
    etype = a[0]
    nn = ELEMENTS.get(etype)
    if nn is None:
        raise Unsupported(f"element {etype!r}")
    nodes = tuple(int(t) for t in a[2:2 + nn])
    post = list(a[2 + nn:])
    tf = None
    keyargs = list(post)
    if etype in _TRANSF_ELEMENTS:
        # the transformation tag is the last positional before options
        i = 0
        while i < len(post) and not isinstance(post[i], str):
            i += 1
        if i == 0:
            raise Unsupported(f"{etype}: no transformation tag")
        tf = int(post[i - 1])
        if tf not in transf:
            raise Unsupported(f"{etype}: undefined transformation {tf}")
        keyargs[i - 1] = ("transf",) + transf[tf]
    return etype, nn, nodes, tuple(post), tf, tuple(keyargs)


def _quiet():
    from skyframe.engine.opensees_engine import _quiet_native_output
    return _quiet_native_output()


def _extract(defs, transf, todo) -> Dict[tuple, Tuple[np.ndarray, Optional[
        np.ndarray]]]:
    """Element stiffness / mass matrices of the ``todo`` items
    ``(key, etype, coords, post, tf)`` from OpenSees itself (one element
    at a time in a scratch domain; ``GimmeMCK`` + ``FullGeneral``)."""
    out = {}
    if not todo:
        return out
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    for name, a in defs:
        getattr(ops, name)(*a)
    done_tf = set()
    ntag = 0
    live: List[int] = []
    first = True
    with _quiet():
        for key, etype, coords, post, tf in todo:
            if live:
                ops.remove("element", 1)
                for t in live:
                    ops.remove("node", t)
            if tf is not None and tf not in done_tf:
                ops.geomTransf("Linear", tf, *transf[tf])
                done_tf.add(tf)
            live = []
            for c in coords:
                ntag += 1
                ops.node(ntag, *c)
                live.append(ntag)
            ops.element(etype, 1, *live, *post)
            if first:
                ops.constraints("Plain")
                ops.numberer("Plain")
                ops.system("FullGeneral")
                ops.algorithm("Linear")
                ops.integrator("GimmeMCK", 0.0, 0.0, 1.0)
                ops.analysis("Transient")
                first = False
            else:
                ops.integrator("GimmeMCK", 0.0, 0.0, 1.0)
            ops.analyze(1, 0.0)
            Kf = np.array(ops.printA("-ret"), dtype=float)
            ops.integrator("GimmeMCK", 1.0, 0.0, 0.0)
            ops.analyze(1, 0.0)
            Mf = np.array(ops.printA("-ret"), dtype=float)
            n = int(round(math.sqrt(Kf.size)))
            perm = np.array([e for t in live for e in ops.nodeDOFs(t)],
                            dtype=np.int64)
            if n != 6 * len(live) or perm.min() < 0:
                raise Unsupported(f"element {etype!r}: unexpected dofs")
            K = Kf.reshape(n, n)[np.ix_(perm, perm)]
            M = Mf.reshape(n, n)[np.ix_(perm, perm)]
            out[key] = (K, M if np.any(M) else None)
    ops.wipe()
    return out


def _replay(calls) -> None:
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    for name, a, k in calls:
        getattr(ops, name)(*a, **k)


# --------------------------------------------------------------------------- #
# the linear system of one elastic build
# --------------------------------------------------------------------------- #
class _Token:
    """``_REUSE["factored"]`` marker: the PARDISO Diagonal analysis is
    live on this assembly (callable like a weakref -> never 'is asm', so
    no OpenSees factor-once consumer mistakes it for its own)."""

    def __init__(self, asm):
        self.asm = weakref.ref(asm)

    def __call__(self):
        return None


class PardisoSystem:
    def __init__(self, eng, asm, calls, transformation: bool, model):
        self.ok = False
        self.reason = ""
        self.info: Dict[str, object] = {}
        self.timing: Dict[str, float] = {}
        self.factor: Optional[PardisoFactor] = None
        self.threads = resolve_threads(model)
        self.transformation = transformation
        self.calls = calls
        self.checked = False
        self.n_domain_solves = 0
        self.n_block_rhs = 0
        self._space_maps: Dict[int, np.ndarray] = {}
        self.initial_forces = False
        self.elem_key: Dict[int, tuple] = {}
        self.elem_nodes: Dict[int, Tuple[int, ...]] = {}
        self._asm_ref = weakref.ref(asm)
        self._build(asm)

    # ---- assembly ---------------------------------------------------------
    def _build(self, asm) -> None:
        import scipy.sparse as sp
        t0 = time.perf_counter()
        calls = self.calls
        defs, transf, fp = _linear_defs(calls)
        coords: Dict[int, Tuple[float, float, float]] = {}
        fixed: Dict[int, Tuple[int, ...]] = {}
        nmass: Dict[int, List[float]] = {}
        elems = []
        mps = []
        for name, a, _k in calls:
            if name == "node":
                tag = int(a[0])
                coords[tag] = (float(a[1]), float(a[2]), float(a[3]))
                rest = list(a[4:])
                if rest:
                    if rest[0] == "-mass" and len(rest) >= 7:
                        nmass[tag] = [float(v) for v in rest[1:7]]
                        rest = rest[7:]
                    if rest:
                        raise Unsupported(f"node options {rest!r}")
            elif name == "fix":
                tag = int(a[0])
                prev = fixed.get(tag, (0,) * 6)
                fixed[tag] = tuple(max(int(p), int(v)) for p, v in
                                   zip(prev, a[1:7]))
            elif name == "mass":
                nmass[int(a[0])] = [float(v) for v in a[1:7]]
            elif name == "element":
                elems.append(a)
                self.elem_nodes[int(a[1])] = tuple(
                    int(t) for t in a[2:2 + ELEMENTS.get(a[0], 0)])
            elif name in MP_COMMANDS:
                mps.append((name, a))
        tags = sorted(coords)
        self.tags = tags
        idx = {t: i for i, t in enumerate(tags)}
        self.idx = idx
        nphys = 6 * len(tags)
        self.nphys = nphys
        # ---- element matrices (memoised by translation-invariant key)
        groups: Dict[tuple, List[Tuple[int, ...]]] = {}
        todo = []
        seen = set()
        for a in elems:
            etype, nn, nodes, post, tf, keyargs = _element_template(a, transf)
            if any(t not in coords for t in nodes):
                raise Unsupported(f"element {a[1]}: unknown node")
            x0 = coords[nodes[0]]
            rel = tuple(round(c[i] - x0[i], COORD_DECIMALS)
                        for t in nodes for c in (coords[t],)
                        for i in range(3))
            uses_defs = not (etype in _TRANSF_ELEMENTS
                             and sum(1 for v in post
                                     if not isinstance(v, str)) >= 7)
            key = (fp if uses_defs else None, etype, keyargs, rel)
            groups.setdefault(key, []).append(nodes)
            self.elem_key[int(a[1])] = key
            if key not in _ELEM_CACHE and key not in seen:
                seen.add(key)
                todo.append((key, etype, [coords[t] for t in nodes],
                             post, tf))
        t1 = time.perf_counter()
        new = _extract(defs, transf, todo)
        t2 = time.perf_counter()
        if len(_ELEM_CACHE) + len(new) > CACHE_CAP:
            _ELEM_CACHE.clear()
        _ELEM_CACHE.update(new)
        # this build's element stiffness matrices (fixed-end forces)
        self.kmat = {k: _ELEM_CACHE[k][0] for k in groups}
        _replay(calls)                     # the identical engine domain
        t3 = time.perf_counter()
        # ---- assemble K, M over the physical dofs
        rows, cols, vals = [], [], []
        mrows, mcols, mvals = [], [], []
        asym = 0.0
        kmax = 0.0
        for key, inst in groups.items():
            Ke, Me = _ELEM_CACHE[key] if key in _ELEM_CACHE else new[key]
            kmax = max(kmax, float(np.abs(Ke).max()) if Ke.size else 0.0)
            asym = max(asym, float(np.abs(Ke - Ke.T).max()) if Ke.size
                       else 0.0)
            ni = np.array([[idx[t] for t in nodes] for nodes in inst],
                          dtype=np.int64)
            dof = (6 * ni[:, :, None] + np.arange(6)).reshape(len(inst), -1)
            r_, c_ = np.nonzero(Ke)
            if r_.size:
                rows.append(dof[:, r_].ravel())
                cols.append(dof[:, c_].ravel())
                vals.append(np.tile(Ke[r_, c_], len(inst)))
            if Me is not None:
                r_, c_ = np.nonzero(Me)
                mrows.append(dof[:, r_].ravel())
                mcols.append(dof[:, c_].ravel())
                mvals.append(np.tile(Me[r_, c_], len(inst)))
        if kmax > 0 and asym > 1e-10 * kmax:
            raise Unsupported("non-symmetric element stiffness")
        cat = (lambda xs, dt: np.concatenate(xs).astype(dt, copy=False) if xs
               else np.zeros(0, dt))
        K = sp.csr_matrix((cat(vals, float), (cat(rows, np.int64),
                                               cat(cols, np.int64))),
                          shape=(nphys, nphys))
        del rows, cols, vals
        for t, m in nmass.items():
            b = 6 * idx[t]
            mrows.append(np.arange(b, b + 6))
            mcols.append(np.arange(b, b + 6))
            mvals.append(np.array(m, dtype=float))
        M = sp.csr_matrix((cat(mvals, float), (cat(mrows, np.int64),
                                                cat(mcols, np.int64))),
                          shape=(nphys, nphys))
        # ---- constraint transformation T (phys -> reduced)
        T, red, slaves = self._transformation(tags, idx, coords, fixed, mps)
        self.T = T
        self.red = red                         # reduced -> phys index
        self.slaves = slaves
        self.fixed = fixed
        fm = np.zeros(nphys, dtype=bool)
        for t, f in fixed.items():
            b = 6 * idx[t]
            for d in range(6):
                if f[d]:
                    fm[b + d] = True
        self.fixed_mask = fm
        self.initial_forces = any(
            name == "uniaxialMaterial" and a[0] == "InitStrainMaterial"
            for name, a in defs)
        if self.initial_forces:
            # initial strains load the zero state (the priming step would
            # move it): kept on the OpenSees path
            raise Unsupported("initial-strain materials (InitStrainMaterial)")
        Kr = (T.T @ K @ T).tocsr()
        Kr = 0.5 * (Kr + Kr.T)
        Mr = (T.T @ M @ T).tocsr()
        Mr = 0.5 * (Mr + Mr.T)
        del K, M                          # the reduced pair is kept
        self.Kr, self.Mr = Kr.tocsr(), Mr.tocsr()
        self.Kr.sort_indices()
        t4 = time.perf_counter()
        self.timing.update(extract=t2 - t1, replay=t3 - t2,
                           assemble=(t1 - t0) + (t4 - t3))
        self.info.update(equations=int(Kr.shape[0]), nnz=int(self.Kr.nnz),
                         elements=len(elems), distinct_elements=len(groups),
                         extracted=len(new), nodes=len(tags))
        self.ok = True

    @staticmethod
    def _transformation(tags, idx, coords, fixed, mps):
        """``(T, red, slaves)`` — T maps reduced to physical dofs exactly as
        OpenSees' Transformation handler (C matrices of RigidDiaphragm,
        RigidBeam / RigidRod and equalDOF)."""
        import scipy.sparse as sp
        cons: Dict[int, Tuple[int, List[int], List[int], np.ndarray]] = {}

        def add(c, r, cdofs, rdofs, C):
            if c in cons:
                raise Unsupported(f"node {c} carries two MP constraints")
            cons[c] = (r, cdofs, rdofs, C)
        for name, a in mps:
            if name == "rigidDiaphragm":
                perp, r = int(a[0]), int(a[1])
                for c in a[2:]:
                    c = int(c)
                    dx = [coords[c][i] - coords[r][i] for i in range(3)]
                    C = np.eye(3)
                    if perp == 1:
                        dofs = [1, 2, 3]
                        C[0, 2], C[1, 2] = -dx[2], dx[1]
                    elif perp == 2:
                        dofs = [0, 2, 4]
                        C[0, 2], C[1, 2] = dx[2], -dx[0]
                    elif perp == 3:
                        dofs = [0, 1, 5]
                        C[0, 2], C[1, 2] = -dx[1], dx[0]
                    else:
                        raise Unsupported(f"rigidDiaphragm perp {perp}")
                    add(c, r, dofs, dofs, C)
            elif name == "rigidLink":
                typ, r, c = a[0], int(a[1]), int(a[2])
                dx = [coords[c][i] - coords[r][i] for i in range(3)]
                if typ == "beam":
                    C = np.eye(6)
                    C[0, 4], C[0, 5] = dx[2], -dx[1]
                    C[1, 3], C[1, 5] = -dx[2], dx[0]
                    C[2, 3], C[2, 4] = dx[1], -dx[0]
                    add(c, r, list(range(6)), list(range(6)), C)
                elif typ == "bar":
                    add(c, r, [0, 1, 2], [0, 1, 2], np.eye(3))
                else:
                    raise Unsupported(f"rigidLink {typ!r}")
            elif name == "equalDOF":
                r, c = int(a[0]), int(a[1])
                dofs = [int(d) - 1 for d in a[2:]]
                add(c, r, dofs, dofs, np.eye(len(dofs)))
        for c, (r, cd, rd, C) in cons.items():
            if r in cons:
                raise Unsupported("chained MP constraints")
            fc = fixed.get(c, (0,) * 6)
            if any(fc[d] for d in cd):
                raise Unsupported(f"fixed constrained dof at node {c}")
        # reduced numbering: free & unconstrained physical dofs, node order
        red_of = -np.ones(6 * len(tags), dtype=np.int64)
        red: List[int] = []
        for t in tags:
            b = 6 * idx[t]
            fx = fixed.get(t, (0,) * 6)
            cd = set(cons[t][1]) if t in cons else set()
            for d in range(6):
                if not fx[d] and d not in cd:
                    red_of[b + d] = len(red)
                    red.append(b + d)
        rows, cols, vals = [], [], []
        for p in red:
            rows.append(p)
            cols.append(red_of[p])
            vals.append(1.0)
        for c, (r, cd, rd, C) in cons.items():
            bc, br = 6 * idx[c], 6 * idx[r]
            for i, d in enumerate(cd):
                for j, e in enumerate(rd):
                    q = red_of[br + e]
                    if C[i, j] != 0.0 and q >= 0:
                        rows.append(bc + d)
                        cols.append(q)
                        vals.append(float(C[i, j]))
        T = sp.csr_matrix((vals, (rows, cols)),
                          shape=(6 * len(tags), len(red)))
        return T, np.array(red, dtype=np.int64), cons

    # ---- factor / solves --------------------------------------------------
    def ensure_factor(self) -> PardisoFactor:
        if self.factor is None:
            t = time.perf_counter()
            self.factor = PardisoFactor(self.Kr, self.threads)
            self.timing["factor"] = time.perf_counter() - t
            self.info.update(mtype=self.factor.mtype,
                             nnz_factor=self.factor.nnz_factor,
                             threads=self.threads)
        return self.factor

    def solve_red(self, B: np.ndarray) -> np.ndarray:
        f = self.ensure_factor()
        t = time.perf_counter()
        X = f.solve(B)
        self.timing["solve"] = self.timing.get("solve", 0.0) + (
            time.perf_counter() - t)
        return X

    def solve_phys(self, F: np.ndarray) -> np.ndarray:
        """``T K_r^-1 T^T F`` for physical load vector(s) (n_phys[, k])."""
        Fr = self.T.T @ F
        X = self.solve_red(np.asarray(Fr))
        if F.ndim == 2:
            self.n_block_rhs += F.shape[1]
        return np.asarray(self.T @ X)

    # ---- right-hand sides and exactness checks ----------------------------
    def nodal_load_vector(self) -> np.ndarray:
        """Physical vector of the nodal loads of pattern 1 (the engine's
        convention: ``timeSeries Linear 1`` + ``pattern Plain 1 1``, so
        factor 1 at the solved step)."""
        F = np.zeros(self.nphys)
        if 1 not in list(ops.getPatterns()):
            return F
        tags = ops.getNodeLoadTags(1)
        data = ops.getNodeLoadData(1)
        if len(data) != 6 * len(tags):                  # pragma: no cover
            raise Unsupported("unexpected nodal load data")
        idx = self.idx
        for k, t in enumerate(tags):
            b = 6 * idx[int(t)]
            F[b:b + 6] += data[6 * k:6 * k + 6]
        return F

    def element_loads(self) -> List[int]:
        """Elements whose force is nonzero at zero displacement: those
        carrying element loads in pattern 1 (and every element when the
        build has initial-strain materials)."""
        if self.initial_forces:
            return list(self.elem_nodes)
        if 1 not in list(ops.getPatterns()):
            return []
        return list(dict.fromkeys(int(e) for e in ops.getEleLoadTags(1)))

    def fixed_end_forces(self, P: np.ndarray, etags) -> None:
        """``P -= sum_e F0_e`` over the loaded elements, with OpenSees' own
        fixed-end force ``F0_e = eleForce_e(u) - K_e u_e`` at the current
        (stepped) state ``u`` — exact whatever increment the step took."""
        idx = self.idx
        for e in etags:
            nodes = self.elem_nodes[e]
            f = np.asarray(ops.eleForce(e), dtype=float)
            if f.size != 6 * len(nodes):
                raise Unsupported(f"element {e}: {f.size} force values")
            ue = np.concatenate([np.asarray(ops.nodeDisp(t)[:6], dtype=float)
                                 for t in nodes])
            if np.any(ue):
                f = f - self.kmat[self.elem_key[e]] @ ue
            for j, t in enumerate(nodes):
                b = 6 * idx[t]
                P[b:b + 6] -= f[6 * j:6 * j + 6]

    def element_force_sum(self) -> np.ndarray:
        """``sum_e eleForce_e`` of every element (global, physical)."""
        F = np.zeros(self.nphys)
        idx = self.idx
        for e, nodes in self.elem_nodes.items():
            f = ops.eleForce(e)
            if len(f) != 6 * len(nodes):
                raise Unsupported(f"element {e}: {len(f)} force values")
            for j, t in enumerate(nodes):
                b = 6 * idx[t]
                F[b:b + 6] += f[6 * j:6 * j + 6]
        return F

    def check(self, R_red: np.ndarray, Kx: np.ndarray) -> None:
        """The one-time exactness check: OpenSees' own residual (from its
        element forces) at the PARDISO solution must be round-off relative
        to ``K_r x``."""
        scale = max(float(np.abs(Kx).max()) if Kx.size else 0.0, 1e-300)
        res = float(np.abs(R_red).max()) / scale if R_red.size else 0.0
        self.info["residual_check"] = res
        if res > RESIDUAL_TOL:
            raise Unsupported(f"OpenSees residual {res:.2e} after the "
                              "PARDISO solve (stiffness mismatch)")
        self.checked = True

    def read_phys(self) -> np.ndarray:
        U = np.empty((len(self.tags), 6))
        for i, t in enumerate(self.tags):
            U[i, :] = ops.nodeDisp(t)[:6]
        return U.ravel()

    # ---- domain I/O ---------------------------------------------------------
    def push(self, u: np.ndarray) -> None:
        U = u.reshape(-1, 6)
        sd = ops.setNodeDisp
        for i, t in enumerate(self.tags):
            row = U[i]
            for d in range(6):
                sd(t, d + 1, float(row[d]), "-commit")

    def space_map(self, space) -> np.ndarray:
        """Index map: engine Space dof -> this system's physical dof."""
        key = id(space)
        m = self._space_maps.get(key)
        if m is None:
            m = np.empty(space.n, dtype=np.int64)
            for i, t in enumerate(space.tags):
                b = 6 * self.idx[t]
                m[6 * i:6 * i + 6] = np.arange(b, b + 6)
            self._space_maps[key] = m
        return m

    def describe(self) -> dict:
        d = dict(self.info)
        d["solves"] = int(self.factor.solves) if self.factor else 0
        d["rhs"] = int(self.factor.rhs) if self.factor else 0
        d["domain_solves"] = self.n_domain_solves
        d["timing"] = {k: round(v, 4) for k, v in self.timing.items()}
        return d


# --------------------------------------------------------------------------- #
# engine glue
# --------------------------------------------------------------------------- #
def adopt(model, asm, neq: int) -> None:
    """``solver_policy.choose`` hook (end of ``_build``): stop recording
    and, when the policy picks PARDISO and the build is eligible, build
    the system (scratch-domain extraction + replay of the build)."""
    rec = _stop_recording()
    ref = _REC.get("eng")
    eng = ref() if ref is not None else None
    _REC["eng"] = None
    why = _REC.get("why", "")
    _REC["why"] = ""
    opt = solver_option(model)
    if opt not in ("pardiso", "auto"):
        return
    if opt == "auto" and neq < PARDISO_EQ_THRESHOLD:
        return
    state = _state(eng, opt)
    if rec is None or eng is None:
        if not available():
            if opt == "auto":           # auto + not installed: today's path
                if eng is not None and not state["systems"]:
                    eng.__dict__.pop("_pds_state", None)
                return
            state["reason"] = ("pypardiso unavailable: "
                               + unavailable_reason())
        elif why.startswith("not the standard"):
            # P-Delta / hinge / corotational builds are OpenSees analyses
            # by design (not a fallback of a linear flow)
            n = state.setdefault("opensees_builds", 0)
            state["opensees_builds"] = n + 1
        else:
            state["reason"] = why or "build not recorded"
        return
    try:
        if (eng._axial_only_present() or eng._nonlinear_static_links_present()
                or eng._compression_only_springs_present()):
            raise Unsupported("nonlinear members / links / springs")
        if getattr(asm, "gd_present", False):
            raise Unsupported("ground displacements (non-homogeneous SP)")
        S = PardisoSystem(eng, asm, rec.calls, asm.use_transformation, model)
    except Unsupported as exc:
        state["reason"] = str(exc)
        if not _domain_intact(rec):
            _replay(rec.calls)
        return
    except Exception as exc:                            # pragma: no cover
        state["reason"] = f"{type(exc).__name__}: {exc}"
        _replay(rec.calls)
        return
    asm.__dict__["_pds"] = S
    state["systems"].append(S)


def _state(eng, opt: str) -> dict:
    """The run's PARDISO report state (one per engine; every build of the
    run appends its system)."""
    st = {"requested": opt, "reason": "", "systems": []}
    if eng is None:
        return st
    prev = eng.__dict__.get("_pds_state")
    if prev:
        return prev
    eng.__dict__["_pds_state"] = st
    return st


def _domain_intact(rec) -> bool:
    """True when the engine's domain still lives (no scratch extraction
    ran yet): the node count of the recording matches the live domain."""
    try:
        n = sum(1 for name, _a, _k in rec.calls if name == "node")
        return len(ops.getNodeTags()) == n
    except Exception:                                   # pragma: no cover
        return False


def system(asm) -> Optional[PardisoSystem]:
    S = getattr(asm, "_pds", None) if asm is not None else None
    return S if (S is not None and S.ok) else None


def active(eng) -> bool:
    from skyframe.engine.opensees_engine import _reuse_owner, _slow_path
    asm = getattr(eng, "_asm", None)
    return (system(asm) is not None and not _slow_path()
            and _reuse_owner() is eng)


def disable(eng, asm, reason: str) -> None:
    """Fall back to the OpenSees path for this assembly (reason kept for
    results ``extra.solver``)."""
    S = getattr(asm, "_pds", None)
    if S is not None:
        S.ok = False
        S.reason = reason
    st = getattr(eng, "_pds_state", None)
    if st is not None:
        st["reason"] = reason


def _ensure_diag_analysis(asm) -> None:
    """The step-only analysis of the PARDISO path: ``Diagonal`` system +
    ``Linear -factorOnce`` (Transformation / Plain handler as the engine's,
    ``LoadControl 1``).  A step applies the loads at factor 1 (time steered
    with ``setTime``), updates and commits every element and adds
    ``R / diag`` — round-off when the written displacements solve the
    case, and zero after a domain change (the factor-once diagonal is then
    empty: probed).  No element tangent is formed after the first step.
    The integrator is never re-issued: that re-sets the whole analysis up
    (measured +0.45 s per step at 28k equations).  Kept live across solves
    on the reused domain (any other analysis setup clears the token)."""
    from skyframe.engine.opensees_engine import _REUSE
    tok = _REUSE.get("factored")
    if isinstance(tok, _Token) and tok.asm() is asm:
        return
    _REUSE["factored"] = None
    ops.wipeAnalysis()
    ops.constraints("Transformation" if asm.use_transformation else "Plain")
    ops.numberer("Plain")
    ops.system("Diagonal")
    ops.algorithm("Linear", "-factorOnce")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    _REUSE["factored"] = _Token(asm)


def _step() -> None:
    """One step from time 0 to load factor 1."""
    ops.setTime(0.0)
    with _quiet():
        ok = ops.analyze(1)
    if ok != 0:
        raise RuntimeError("Diagonal step failed")


def prepare_solve(eng, asm) -> bool:
    """``_setup_linear_reused`` hook: solve the applied case with PARDISO,
    write the displacements into the domain and leave the caller's
    ``ops.analyze(1)`` as the step that applies the loads at factor 1 and
    updates every element.  False = not handled (the caller continues
    with the OpenSees path on an unchanged domain)."""
    from skyframe.engine.opensees_engine import _REUSE, _reuse_owner, \
        _slow_path
    S = system(asm)
    if S is None or _slow_path() or _reuse_owner() is not eng:
        return False
    if getattr(asm, "gd_present", False):
        disable(eng, asm, "ground displacements (non-homogeneous SP)")
        return False
    stage = "setup"
    try:
        S.ensure_factor()
        if ops.getTime() != 0.0:
            raise Unsupported("domain not at its initial state")
        _ensure_diag_analysis(asm)
        stage = "right-hand side"
        Pn = S.nodal_load_vector()
        P = Pn.copy()
        loaded = S.element_loads()
        if loaded:
            _step()                       # applies the element loads
            S.fixed_end_forces(P, loaded)
        stage = "solve"
        x = S.solve_red(np.asarray(S.T.T @ P).ravel())
        u = np.asarray(S.T @ x).ravel()
        S.push(u)
        if not S.checked:
            stage = "residual check"
            _step()
            us = S.read_phys()
            R = Pn - S.element_force_sum()
            S.check(np.asarray(S.T.T @ R).ravel(),
                    np.asarray(S.Kr @ x).ravel())
            if np.abs(us - u).max() > 1e-9 * max(np.abs(u).max(), 1e-300):
                raise Unsupported("the update step moved the solution")
            S.push(u)
        ops.setTime(0.0)                  # the caller's step: factor 0 -> 1
        S.n_domain_solves += 1
        return True
    except Exception as exc:
        disable(eng, asm, f"{stage}: {exc}")
        # back to the pristine applied state (time 0, zero displacements,
        # the case's pattern still present) for the OpenSees path
        _REUSE["factored"] = None
        ops.reset()
        return False


def _pure(eng, asm, what: str) -> Optional[PardisoSystem]:
    """The system for a pure (domain-free) solve, validated against
    OpenSees on its first use: on the reset domain (no loads) a fixed
    pseudo-random field is written and one step updates the elements;
    OpenSees' element forces at the resulting state ``u'`` must equal
    ``K_r u'``."""
    S = system(asm)
    if S is None or not active(eng):
        return None
    if S.checked:
        return S
    from skyframe.engine.opensees_engine import _REUSE
    try:
        S.ensure_factor()
        eng._elastic_domain()            # removes any pattern, resets
        _ensure_diag_analysis(asm)
        rng = np.random.default_rng(20261012)
        x = rng.standard_normal(len(S.red))
        S.push(np.asarray(S.T @ x).ravel())
        _step()
        us = S.read_phys()
        xs = us[S.red]
        Kx = np.asarray(S.Kr @ xs).ravel()
        Fos = np.asarray(S.T.T @ S.element_force_sum()).ravel()
        S.check(Fos - Kx, Kx)
        ops.reset()
    except Exception as exc:
        disable(eng, asm, f"{what}: {exc}")
        _REUSE["factored"] = None
        ops.reset()
        return None
    return S


def g_columns(eng, asm, dofs, masters):
    """Condensation columns ``G[:, j]`` (+ the CR rz memo) as ONE block
    PARDISO solve (no domain evaluation); None = not handled."""
    S = _pure(eng, asm, "condensation columns")
    if S is None:
        return None
    nm = len(dofs)
    rows = np.array([6 * S.idx[t] + d - 1 for (t, d) in dofs],
                    dtype=np.int64)
    F = np.zeros((S.nphys, nm))
    F[rows, np.arange(nm)] = 1.0
    try:
        U = S.solve_phys(F)
    except Exception as exc:
        disable(eng, asm, f"condensation columns: {exc}")
        return None
    out = []
    for j, (t, d) in enumerate(dofs):
        col = [float(v) for v in U[rows, j]]
        rz = (float(U[6 * S.idx[t] + 5, j]) if t in masters else None)
        out.append((col, rz))
    return out


def engine_solve(eng, space, f: np.ndarray) -> Optional[np.ndarray]:
    """``sparse_solve.EngineSolver`` hook: ``K^-1 f`` in the engine's
    physical Space (Lanczos / buckling operators) without the domain."""
    asm = getattr(eng, "_asm", None)
    S = _pure(eng, asm, "linear solve")
    if S is None:
        return None
    m = S.space_map(space)
    F = np.zeros(S.nphys)
    F[m] = f
    U = S.solve_phys(F)
    return U[m]


def reuse_for_modal(eng, num_modes):
    """``run_modal`` hook when PARDISO is active (the process-parallel
    variant of :func:`skyframe.engine.parallel.reuse_for_modal` is off
    with PARDISO): ``(cached ModalResults | None, live assembly | None)``.
    The live reused elastic domain is reused for the condensed modal path
    and for the sparse eigen path (both need no rebuild)."""
    from skyframe.engine import modal_condensed as _mcd
    from skyframe.engine import sparse_modal as _spm
    asm = eng._asm
    model = eng.model
    if asm is None or not active(eng) or not _mcd.enabled(model):
        return None, None
    n = min(num_modes or model.num_modes, asm.free_massed_dofs())
    if n <= 0:
        return None, None
    hit = eng._modal_cache.get(n)
    if hit is not None:
        return hit, None
    if not (_mcd.applicable(eng, asm, n) or _spm.applicable(eng, asm, n)):
        return None, None
    return None, eng._elastic_domain()


def eigsh_modes(eng, asm, n: int):
    """``sparse_modal.solve`` hook: the ``n`` lowest modes by scipy
    ``eigsh`` shift-invert (sigma = 0) whose operator is the PARDISO
    factor; post-processed exactly like the Lanczos path (mass
    normalisation, sign, canonical degenerate basis).  None = not
    handled."""
    S = _pure(eng, asm, "eigen")
    if S is None:
        return None
    from scipy.sparse.linalg import LinearOperator, eigsh
    from skyframe.engine import modal_condensed as _mcd
    from skyframe.engine import eigen_refine as _eref
    from skyframe.engine import geom_stiffness as _gk
    space = _gk.Space(asm.node_coords)
    dofs = _mcd.massed_dofs(asm)
    rows = np.array([6 * space.idx[t] + d - 1 for (t, d) in dofs],
                    dtype=np.int64)
    m = np.array([asm.mass_map[k] for k in dofs], dtype=float)
    mvec = np.zeros(space.n)
    mvec[rows] = m
    nr = S.Kr.shape[0]
    f = S.ensure_factor()
    count = {"n": 0}

    def opinv(v):
        count["n"] += 1
        return f.solve(np.asarray(v, dtype=float).ravel())
    OP = LinearOperator((nr, nr), matvec=opinv, dtype=float)
    rng = np.random.default_rng(20261012)
    v0 = f.solve(S.Mr @ rng.standard_normal(nr))     # purified start
    t = time.perf_counter()
    try:
        vals, vecs = eigsh(S.Kr, k=n, M=S.Mr, sigma=0.0, which="LM",
                           OPinv=OP, v0=v0, tol=0.0,
                           ncv=min(nr, max(2 * n + 1, 20)))
    except Exception as exc:
        disable(eng, asm, f"eigsh: {exc}")
        return None
    S.timing["eigsh"] = S.timing.get("eigsh", 0.0) + time.perf_counter() - t
    order = np.argsort(vals)
    vals, vecs = vals[order], vecs[:, order]
    if np.any(vals <= 0.0):
        disable(eng, asm, "eigsh: non-positive eigenvalue")
        return None
    smap = S.space_map(space)
    X = []
    s = np.sqrt(m)
    for k in range(n):
        x = np.asarray(S.T @ vecs[:, k]).ravel()[smap]
        nrm = float(np.sqrt(x @ (mvec * x)))
        x = x / nrm
        y = s * x[rows]
        if y[int(np.argmax(np.abs(y)))] < 0.0:
            x = -x
        X.append(x)
    lams = [float(v) for v in vals]
    Phi = np.column_stack([x[rows] for x in X])
    for g in _eref.clusters(lams):
        try:
            R = _eref.align_basis(Phi[:, g], m, dofs)
        except np.linalg.LinAlgError:                   # pragma: no cover
            continue
        block = np.column_stack([X[k] for k in g]) @ R
        for j, k in enumerate(g):
            X[k] = block[:, j]
    shapes: Dict[int, Dict[int, List[float]]] = {}
    for k, x in enumerate(X, start=1):
        Xm = x.reshape(-1, 6)
        shapes[k] = {t: [float(v) for v in Xm[i]]
                     for i, t in enumerate(space.tags)}
    info = {"solver": "eigsh-pardiso", "system": "PARDISO",
            "solves": int(count["n"]), "converged": True,
            "max_residual": 0.0}
    eng._spm_info = info
    return lams, shapes, info


def attach(eng, res) -> None:
    """Results ``extra.solver`` (only when PARDISO was requested / chosen
    by the policy — default small models stay byte-identical).

    ``used``: PARDISO solved at least one right-hand side of the run;
    ``reason``: why (part of) the run fell back to OpenSees ("" = none);
    the counters and timings add up every build of the run."""
    st = getattr(eng, "_pds_state", None)
    if not st:
        return
    systems = st.get("systems", [])
    agg: Dict[str, object] = {}
    timing: Dict[str, float] = {}
    solves = rhs = dsolves = extracted = 0
    for S in systems:
        d = S.describe()
        solves += d["solves"]
        rhs += d["rhs"]
        dsolves += d["domain_solves"]
        extracted += int(d.get("extracted", 0))
        for k, v in d["timing"].items():
            timing[k] = round(timing.get(k, 0.0) + v, 4)
        for k in ("equations", "nnz", "nodes", "elements",
                  "distinct_elements", "mtype", "nnz_factor", "threads",
                  "diag_check", "residual_check"):
            if k in d:
                agg[k] = d[k]
    used = rhs > 0
    out = {"requested": st.get("requested"), "used": used,
           "engine": "pardiso" if used else "opensees",
           "reason": st.get("reason", ""), "builds": len(systems),
           "solves": solves, "rhs": rhs, "domain_solves": dsolves,
           "extracted": extracted,
           "opensees_builds": int(st.get("opensees_builds", 0))}
    out.update(agg)
    out["timing"] = timing
    extra = dict(getattr(res, "extra", None) or {})
    extra["solver"] = out
    res.extra = extra


def selftest() -> dict:
    """Frozen-build smoke check (``SkyFrameCLI --selftest-pardiso``): MKL
    loads, a 2x2 SPD system and a 3-story frame solve through the engine
    with PARDISO equal OpenSees to 1e-9."""
    out = {"pypardiso": available(), "mkl_rt": _LIB.get("mkl_rt", ""),
           "reason": unavailable_reason()}
    if not out["pypardiso"]:
        out["ok"] = False
        return out
    import scipy.sparse as sp
    A = sp.csr_matrix(np.array([[4.0, 1.0], [1.0, 3.0]]))
    x = PardisoFactor(A, 1).solve(np.array([1.0, 2.0]))
    ok = bool(np.allclose(x, [1.0 / 11.0, 7.0 / 11.0], rtol=1e-12))
    from skyframe.core.builder import quick_building
    from skyframe.engine.opensees_engine import OpenSeesEngine
    m1 = quick_building(stories=3, bays_x=2, bays_y=2)
    m2 = quick_building(stories=3, bays_x=2, bays_y=2)
    m2.analysis_options = {"solver": "pardiso"}
    r1 = OpenSeesEngine(m1).run_static("DEAD")
    e2 = OpenSeesEngine(m2)
    r2 = e2.run_static("DEAD")
    scale = max(abs(a) for v in r1.node_disp.values() for a in v) or 1.0
    worst = max(abs(a - b) for t, v in r1.node_disp.items()
                for a, b in zip(v, r2.node_disp[t])) / scale
    used = system(e2._asm) is not None
    out.update(ok=bool(ok and used and worst < 1e-9), used=used,
               worst_rel=worst, threads=_LIB.get("threads"))
    return out
