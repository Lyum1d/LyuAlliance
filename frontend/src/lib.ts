import type { Agent, Task, TaskStatus } from './types'

export const AGENT_COLORS = ['#4f46e5', '#6b7280', '#2563eb', '#0f766e', '#c2410c', '#9333ea', '#b45309', '#0891b2']

export function agentColor(a?: Agent | null): string {
  if (!a) return '#9ca3af'
  if (a.kind === 'conductor') return '#4f46e5'
  if (a.kind === 'human') return '#6b7280'
  return AGENT_COLORS[a.lane % AGENT_COLORS.length]
}

export function initials(name: string): string {
  const n = name.trim()
  if (/^[\x00-\x7F]+$/.test(n)) return n.slice(0, 1).toUpperCase()
  return n.slice(0, 1)
}

export const STATUS_COLOR: Record<TaskStatus, string> = {
  done: '#16a34a',
  running: '#2563eb',
  reviewing: '#d97706',
  blocked: '#dc2626',
  pending: '#d1d5db',
  failed: '#dc2626',
  cancelled: '#9ca3af',
}

export const BADGE_CLASS: Record<TaskStatus, string> = {
  done: 'b-done',
  running: 'b-run',
  reviewing: 'b-rev',
  blocked: 'b-blk',
  pending: 'b-pend',
  failed: 'b-blk',
  cancelled: 'b-pend',
}

export function fmtDuration(ms: number): string {
  if (!ms || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const r = s % 60
  return r ? `${m}m${String(r).padStart(2, '0')}s` : `${m}m`
}

export function elapsedMs(t: Task): number {
  if (!t.started_at) return 0
  const start = new Date(t.started_at).getTime()
  if (Number.isNaN(start)) return 0
  const end = t.ended_at ? new Date(t.ended_at).getTime() : Date.now()
  return Math.max(0, end - start)
}

export function clockOf(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

/** 依赖拓扑分层：level = 1 + max(level(dep)) */
export function layerTasks(tasks: Task[], edges: [string, string][]) {
  const deps = new Map<string, string[]>()
  edges.forEach(([f, t]) => {
    const arr = deps.get(t) ?? []
    arr.push(f)
    deps.set(t, arr)
  })
  const level = new Map<string, number>()
  const calc = (id: string, seen = new Set<string>()): number => {
    if (level.has(id)) return level.get(id)!
    if (seen.has(id)) return 0
    seen.add(id)
    const ds = deps.get(id) ?? []
    const l = ds.length ? Math.max(...ds.map(d => calc(d, seen))) + 1 : 0
    level.set(id, l)
    return l
  }
  tasks.forEach(t => calc(t.id))
  const groups = new Map<number, Task[]>()
  tasks.forEach(t => {
    const l = level.get(t.id) ?? 0
    const arr = groups.get(l) ?? []
    arr.push(t)
    groups.set(l, arr)
  })
  return { level, groups }
}
