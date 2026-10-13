"""Physical performance-core count (the "auto" parallel lane count).

CONTRACT "Windows-capable parallel solves (spawn worker pool)".

A solve lane is a whole OpenSees process doing memory-bound element loops,
so lanes are sized by PHYSICAL cores, and on hybrid CPUs by PERFORMANCE
cores only (an E-core lane would finish last and hold the family up).

* Windows: ``GetLogicalProcessorInformationEx(RelationProcessorCore)``
  gives one record per physical core with its ``EfficiencyClass``;
  performance cores are those with the highest class (all of them on a
  non-hybrid CPU).  Intel Core Ultra 9 285K (8P + 16E): 8.
* Linux: the CPUs this process may run on (affinity), restricted to
  ``/sys/devices/cpu_core/cpus`` when the kernel exposes Intel's hybrid
  PMU split, counted once per ``(package, core_id)``.
* Anything else / any error: ``os.cpu_count()``.

``SKYFRAME_PERF_CORES`` (an int) overrides the detection.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, List, Optional, Set

_CACHE: dict = {}


def _parse_cpu_list(text: str) -> List[int]:
    out: List[int] = []
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def linux_performance_cores(allowed: Iterable[int],
                            root: str = "/sys/devices") -> Optional[int]:
    """Physical performance cores among the ``allowed`` logical CPUs, from
    sysfs under ``root`` (a parameter for tests); None when sysfs has no
    topology."""
    cpus: Set[int] = set(allowed)
    pcore = _read(os.path.join(root, "cpu_core", "cpus"))
    if pcore:
        cpus &= set(_parse_cpu_list(pcore))
    seen = set()
    for c in sorted(cpus):
        base = os.path.join(root, "system", "cpu", f"cpu{c}", "topology")
        core = _read(os.path.join(base, "core_id"))
        pkg = _read(os.path.join(base, "physical_package_id"))
        if core is None:
            return None
        seen.add(((pkg or "0").strip(), core.strip()))
    return len(seen) or None


def windows_performance_cores() -> Optional[int]:     # pragma: no cover
    """Physical cores of the highest efficiency class (Windows 10+)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = k32.GetLogicalProcessorInformationEx
    fn.argtypes = [ctypes.c_int, ctypes.c_void_p,
                   ctypes.POINTER(wintypes.DWORD)]
    fn.restype = wintypes.BOOL
    size = wintypes.DWORD(0)
    fn(0, None, ctypes.byref(size))           # RelationProcessorCore = 0
    if size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if not fn(0, buf, ctypes.byref(size)):
        return None
    raw = buf.raw[:size.value]
    classes: List[int] = []
    off = 0
    while off + 10 <= len(raw):
        rel = int.from_bytes(raw[off:off + 4], "little")
        rsize = int.from_bytes(raw[off + 4:off + 8], "little")
        if rsize <= 0:
            break
        if rel == 0:
            # PROCESSOR_RELATIONSHIP: BYTE Flags; BYTE EfficiencyClass; ...
            classes.append(raw[off + 9])
        off += rsize
    if not classes:
        return None
    top = max(classes)
    return sum(1 for c in classes if c == top)


def performance_cores() -> int:
    """Physical performance cores available to this process (>= 1)."""
    env = os.environ.get("SKYFRAME_PERF_CORES")
    if env and env.strip().isdigit() and int(env) > 0:
        return int(env)
    if "n" in _CACHE:
        return _CACHE["n"]
    n: Optional[int] = None
    try:
        if sys.platform == "win32":
            n = windows_performance_cores()
        elif hasattr(os, "sched_getaffinity"):
            n = linux_performance_cores(os.sched_getaffinity(0))
    except Exception:                                  # noqa: BLE001
        n = None
    if not n:
        try:
            n = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            n = os.cpu_count() or 1
    _CACHE["n"] = max(1, int(n))
    return _CACHE["n"]
