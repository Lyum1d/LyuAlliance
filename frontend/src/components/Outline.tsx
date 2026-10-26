import { Fragment, useMemo, useState } from 'react'
import type { Agent, Task } from '../types'
import { STAGES, STATUS_TEXT } from '../types'
import { STATUS_COLOR, elapsedMs, fmtDuration } from '../lib'

interface Props {
  agents: Agent[]
  tasks: Task[]
  edges: [string, string][]
  selected: string | null
  onSelect: (id: string) => void
}

const BADGE: Record<string, string> = {
  done: 's-done',
  running: 's-run',
  reviewing: 's-rev',
  blocked: 's-blk',
  pending: 's-pend',
  failed: 's-blk',
}

export default function Outline({ agents, tasks, edges, selected, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())

  const depsOf = useMemo(() => {
    const m = new Map<string, string[]>()
    edges.forEach(([f, t]) => m.set(t, [...(m.get(t) ?? []), f]))
    return m
  }, [edges])

  const indexOf = useMemo(() => {
    const m = new Map<string, number>()
    tasks.forEach((t, i) => m.set(t.id, i + 1))
    return m
  }, [tasks])

  const byStage = useMemo(() => {
    const m = new Map<number, Task[]>()
    tasks.forEach(t => m.set(t.stage, [...(m.get(t.stage) ?? []), t]))
    return [...m.entries()].sort((a, b) => a[0] - b[0])
  }, [tasks])

  const toggle = (stage: number) => {
    const next = new Set(collapsed)
    if (next.has(stage)) next.delete(stage)
    else next.add(stage)
    setCollapsed(next)
  }

  const counts = {
    done: tasks.filter(t => t.status === 'done').length,
    running: tasks.filter(t => t.status === 'running').length,
    blocked: tasks.filter(t => t.status === 'blocked').length,
    pending: tasks.filter(t => t.status === 'pending').length,
  }

  return (
    <div className="tree">
      <table className="tb">
        <thead>
          <tr>
            <th style={{ width: '46%' }}>任务</th>
            <th style={{ width: 86 }}>状态</th>
            <th style={{ width: 128 }}>执行者</th>
            <th style={{ width: 96 }}>进度</th>
            <th style={{ width: 84 }}>依赖</th>
            <th style={{ width: 74 }}>耗时</th>
          </tr>
        </thead>
        <tbody>
          <tr className="grp">
            <td colSpan={6} style={{ paddingLeft: 10 }}>
              <span style={{ fontWeight: 500 }}>全部任务</span>
              <span style={{ color: 'var(--faint)', fontWeight: 400, marginLeft: 10 }}>
                {counts.done} 完成 · {counts.running} 进行 · {counts.blocked} 阻塞 · {counts.pending} 待开始
              </span>
            </td>
          </tr>

          {byStage.map(([stage, list]) => {
            const isCollapsed = collapsed.has(stage)
            return (
              <Fragment key={stage}>
                <tr className="grp" onClick={() => toggle(stage)}>
                  <td colSpan={6} style={{ paddingLeft: 10 }}>
                    {isCollapsed ? '▸' : '▾'} {STAGES[stage] ?? `阶段 ${stage}`}
                    <span style={{ color: 'var(--faint)', fontWeight: 400, marginLeft: 8 }}>
                      {list.length} 个
                    </span>
                  </td>
                </tr>
                {!isCollapsed &&
                  list.map(t => {
                    const agent = agents.find(a => a.id === t.assignee)
                    const deps = (depsOf.get(t.id) ?? []).map(d => indexOf.get(d)).filter(Boolean)
                    return (
                      <tr
                        key={t.id}
                        className="row"
                        onClick={() => onSelect(t.id)}
                        style={{ background: selected === t.id ? '#f5f8ff' : undefined }}
                      >
                        <td>
                          <div className="tdcell" style={{ paddingLeft: 30 }}>
                            <span className="tw" />
                            <span className="bar" style={{ background: STATUS_COLOR[t.status] }} />
                            <span className="tick">{indexOf.get(t.id)}</span>
                            <span>{t.title}</span>
                          </div>
                        </td>
                        <td>
                          <span className={'tag-s ' + (BADGE[t.status] ?? 's-pend')}>
                            {STATUS_TEXT[t.status]}
                          </span>
                        </td>
                        <td>
                          <span className="who" style={{ color: agent ? undefined : 'var(--faint)' }}>
                            <span
                              className="sdot"
                              style={{
                                background: agent ? (agent.kind === 'conductor' ? '#4f46e5' : agent.kind === 'human' ? '#6b7280' : '#2563eb') : '#d1d5db',
                              }}
                            />
                            {agent?.name ?? '未指派'}
                          </span>
                        </td>
                        <td>
                          {t.status === 'running' ? (
                            <>
                              <span className="mini">
                                <i style={{ width: `${t.progress}%` }} />
                              </span>{' '}
                              <span className="num">{t.progress}%</span>
                            </>
                          ) : (
                            <span className="num">—</span>
                          )}
                        </td>
                        <td className="dep">{deps.length ? deps.join(',') : '—'}</td>
                        <td className="num">
                          {t.status === 'running' || t.status === 'done' ? fmtDuration(elapsedMs(t)) : '—'}
                        </td>
                      </tr>
                    )
                  })}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
