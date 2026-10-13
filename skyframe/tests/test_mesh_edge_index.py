"""The edge-beam pre-filter (core.mesh.edge_index_scope) must return exactly
what the plain all-members scan returns, member for member and in order."""

import random

from skyframe.core import mesh as M
from skyframe.core import templates_sys as T
from skyframe.core.builder import quick_building


def _scan(model, pa, pb):
    return [(m.uid, s0, s1) for m, s0, s1 in M._members_on_edge(model, pa, pb)]


def _both(model, edges):
    plain = [_scan(model, a, b) for a, b in edges]
    with M.edge_index_scope(model):
        fast = [_scan(model, a, b) for a, b in edges]
    return plain, fast


def test_tolerance_band_cases_identical():
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    tol = M._TOL
    z = 3.0
    sec = m.members[0].section
    # on the line, just inside / outside the tolerance, overlapping past the
    # ends, touching an end, zero-length overlap, collinear beyond the edge
    for off in (0.0, 0.5 * tol, 0.999 * tol, 1.001 * tol, 2.0 * tol):
        m.add_member("beam", sec, (-1.0, off, z), (0.5 * tol, off, z))
        m.add_member("beam", sec, (2.0, off, z), (7.0, -off, z))
        m.add_member("beam", sec, (6.0 - 0.5 * tol, off, z), (9.0, off, z))
        m.add_member("beam", sec, (6.0 + 2 * tol, off, z), (9.0, off, z))
    edges = [((0.0, 0.0, z), (6.0, 0.0, z)), ((6.0, 0.0, z), (0.0, 0.0, z)),
             ((0.0, 0.0, z), (0.0, 6.0, z)), ((0.0, 0.0, z), (6.0, 6.0, z))]
    plain, fast = _both(m, edges)
    assert plain == fast
    assert any(plain)                        # the test exercises real hits


def test_random_edges_identical():
    m, _ = T.build_system("steel_deck", {"stories": 4})
    rnd = random.Random(7)
    pts = [tuple(map(float, p)) for mm in m.members for p in (mm.pi, mm.pj)]
    edges = []
    for _ in range(300):
        a, b = rnd.sample(pts, 2)
        if a != b:
            edges.append((a, b))
    for r in m.shells:                       # real slab edges
        c = [tuple(map(float, p)) for p in r.corners]
        edges += [(c[k], c[(k + 1) % len(c)]) for k in range(len(c))]
    plain, fast = _both(m, edges)
    assert plain == fast


def test_mesh_identical_and_scope_cleared():
    m, _ = T.build_system("steel_deck", {"stories": 3})
    with_idx = M.mesh_model(m)
    assert not M._EDGE_IDX                   # scope removed after meshing
    saved = M.edge_index_scope.__enter__
    M.edge_index_scope.__enter__ = lambda self: self   # disable the index
    try:
        plain = M.mesh_model(m)
    finally:
        M.edge_index_scope.__enter__ = saved
    assert repr(with_idx.points) == repr(plain.points)
    assert repr(with_idx.membrane_loads) == repr(plain.membrane_loads)
    assert repr(sorted((k, sorted(v.items())) for k, v in with_idx.membrane_nodal.items())) \
        == repr(sorted((k, sorted(v.items())) for k, v in plain.membrane_nodal.items()))


def test_edited_members_fall_back():
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    e = ((0.0, 0.0, 3.0), (6.0, 0.0, 3.0))
    with M.edge_index_scope(m):
        m.add_member("beam", m.members[0].section, (0.0, 0.0, 3.0), (6.0, 0.0, 3.0))
        got = _scan(m, *e)                   # index is stale -> full scan
    assert got == _scan(m, *e)
