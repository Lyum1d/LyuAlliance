import { useMemo } from 'react'
import type { Agent, Task } from '../types'
import { layerTasks } from '../lib'

interface Props {
  agents: Agent[]
  tasks: Task[]
  edges: [string, string][]
  selected: string | null
  onSelect: (id: string) => void
}

const W = 180
const GAP = 24
const CANVAS = 920
const ROW_H = 96
const NODE_H = 52

export default function Dag({ agents, tasks, edges, selected, onSelect }: Props) {
  const { groups } = useMemo(() => layerTasks(tasks, edges), [tasks, edges])

  const pos = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>()
    groups.forEach((arr, level) => {
      const total = arr.length * W + (arr.length - 1) * GAP
      const start = Math.max(24, (CANVAS - total) / 2)
      arr.forEach((t, i) => m.set(t.id, { x: start + i * (W + GAP), y: 24 + level * ROW_H }))
    })
    return m
  }, [groups])

  const maxLevel = Math.max(0, ...[...groups.keys()])
  const height = 24 + maxLevel * ROW_H + NODE_H + 44

  const paths = useMemo(() => {
    return edges
      .map(([a, b]) => {
        const pa = pos.get(a)
        const pb = pos.get(b)
        if (!pa || !pb) return null
        const x1 = pa.x + W / 2
        const y1 = pa.y + NODE_H
        const x2 = pb.x + W / 2
        const y2 = pb.y
        const dy = Math.max(16, Math.abs(y2 - y1) * 0.45)
        const target = tasks.find(t => t.id === b)
        const running = target?.status === 'running'
        return { d: `M${x1} ${y1} C${x1} ${y1 + dy} ${x2} ${y2 - dy} ${x2} ${y2}`, running }
      })
      .filter(Boolean) as { d: string; running: boolean }[]
  }, [edges, pos, tasks])

  return (
    <div className="dagbox">
      <div className="daginner" style={{ height }}>
        <svg className="wires" width={CANVAS} height={height}>
          <defs>
            <marker id="ag" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M2 1L8 5L2 9" fill="none" stroke="#c7cdd6" strokeWidth="1.5" strokeLinecap="round" />
            </marker>
            <marker id="ab" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M2 1L8 5L2 9" fill="none" stroke="#2563eb" strokeWidth="1.5" strokeLinecap="round" />
            </marker>
          </defs>
          {paths.map((p, i) => (
            <path
              key={i}
              d={p.d}
              fill="none"
              stroke={p.running ? '#2563eb' : '#c7cdd6'}
              strokeWidth={p.running ? 1.6 : 1.4}
              strokeDasharray={p.running ? '5 4' : undefined}
              markerEnd={p.running ? 'url(#ab)' : 'url(#ag)'}
            />
          ))}
        </svg>

        {tasks.map(t => {
          const p = pos.get(t.id)
          if (!p) return null
          const agent = agents.find(a => a.id === t.assignee)
          return (
            <div
              key={t.id}
              data-s={t.status}
              className={'node' + (selected === t.id ? ' sel' : '')}
              style={{ left: p.x, top: p.y, width: W, height: NODE_H }}
              onClick={() => onSelect(t.id)}
            >
              <div className="nt">{t.title}</div>
              <div className="ns">
                <span
                  className="sdot"
                  style={{ background: t.status === 'done' ? '#16a34a' : t.status === 'running' ? '#2563eb' : t.status === 'blocked' ? '#dc2626' : '#d1d5db' }}
                />
                {t.status === 'running' ? `${t.progress}% · ${agent?.name ?? ''}` : agent?.name ?? '未指派'}
              </div>
              {t.status === 'running' && (
                <div className="np">
                  <i style={{ width: `${t.progress}%` }} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
