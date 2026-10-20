// The contract between Supa and the solver: what goes in (FemModel), what comes back (FemResult).
// Copied from Supa's app/src/model/fem.ts. Units: kip and foot throughout.

export type Case = 'D' | 'L' | 'Wx' | 'Wy' | 'Ex' | 'Ey'
export const CASES: Case[] = ['D', 'L', 'Wx', 'Wy', 'Ex', 'Ey']
export const CASE_NAMES: Record<Case, string> = { D: 'Dead', L: 'Live', Wx: 'Wind along x', Wy: 'Wind along y', Ex: 'Earthquake along x', Ey: 'Earthquake along y' }

/** The model, as plain arrays, to hand to the solver. */
export interface FemModel {
  /** x y z a node. */
  xyz: number[]
  fixed: boolean[]
  /** Each element: its nodes, stiffness, the way its local y lies, its end releases, where it came from. */
  els: {
    i: number; j: number
    E: number; G: number; A: number; Iy: number; Iz: number; J: number
    /** A hint for local y: any vector not along it. Local z is x × y. */
    yh: [number, number, number]
    /** Bending released at end i, end j: a pin. */
    ri: boolean; rj: boolean
    /** The piece it stands for, by id; arms none. */
    src: string
    kind: 'frame' | 'wall' | 'arm'
    /** Load along it, global, k/ft, a case each. */
    q: Partial<Record<Case, [number, number, number]>>
  }[]
  /** Rigid diaphragms: their centre, and the nodes on each. */
  dia: { id: string; x: number; y: number; z: number; nodes: number[] }[]
  /** Loads at nodes (fx fy fz a node) and at diaphragm centres (fx fy mz a diaphragm), a case each. */
  nodeLoads: Record<Case, number[]>
  diaLoads: Record<Case, number[]>
  /** What could not be put in: a floor's lateral load with no diaphragm to take it. */
  notes: string[]
}

export interface FemResult {
  /** Per piece (member or wall), per case: axial (tension +), the largest moment, the largest shear. */
  pieces: Record<string, Record<Case, { N: number; M: number; V: number }>>
  /** Per diaphragm, per case: its movement in plan, ft, and its z; for drift. */
  dia: { id: string; z: number; u: Record<Case, [number, number]> }[]
  /** Per case: the loads put in and the reactions at the ground, each [fx, fy, fz]. */
  balance: Record<Case, { applied: [number, number, number]; reactions: [number, number, number] }>
  /** Every node's movement, x y z a node, ft, per case: the deflected shape. */
  nodeU: Record<Case, number[]>
  /** Largest node movement, ft, per case; and the nodes that moved more than a foot — unstable. */
  maxU: Record<Case, number>
  /** The node that moved most, by index, per case. */
  maxAt: Record<Case, number>
  loose: number
  /** Some of those nodes, by index, to look at. */
  looseNodes?: number[]
  dofs: number
  notes: string[]
}
