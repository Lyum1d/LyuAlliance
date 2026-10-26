import { Fragment, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Agent, Task } from '../types'
import { STAGES, STATUS_TEXT } from '../types'
import { STATUS_COLOR, agentColor } from '../lib'

interface Props {
  agents: Agent[]
  tasks: Task[]
  edges: [string, string][]
  selected: string | null
  onSelect: (id: string) => void
}

const NONE = '__none__'

type Geo = { x: number; y: number; w: number; h: number }
type Path = { d: string; soft: boolean }
type E = { a: string; b: string; soft: boolean }

export default function Swimlane({ agents, tasks, edges, selected, onSelect }: Props) {
  const gridRef = useRef<HTMLDivElement>(null)
  const [paths, setPaths] = useState<Path[]>([])
  const [size, setSize] = useState({ w: 0, h: 0 })

  const laneAgents = useMemo(() => {
    const ids = new Set<string>()
    tasks.forEach(t => t.assignee && ids.add(t.assignee))
    return agents.filter(a => a.in_team && ids.has(a.id)).sort((x, y) => x.lane - y.lane)
  }, [agents, tasks])

  const hasUnassigned = useMemo(
    () => tasks.some(t => !t.assignee || !laneAgents.find(a => a.id === t.assignee)),
    [tasks, laneAgents],
  )

  const rows = useMemo(() => {
    const list = laneAgents.map(a => ({
      id: a.id,
      name: a.name,
      role: a.role,
      color: agentColor(a),
    }))
    if (hasUnassigned) list.push({ id: NONE, name: '未指派', role: 'blocked', color: '#dc2626' })
    return list
  }, [laneAgents, hasUnassigned])

  const laneOf = useMemo(() => {
    const m = new Map<string, number>()
    tasks.forEach(t => {
      const idx = rows.findIndex(r => r.id === t.assignee)
      m.set(t.id, idx >= 0 ? idx : rows.length - 1)
    })
    return m
  }, [tasks, rows])

  const colOf = useMemo(() => {
    const m = new Map<string, number>()
    tasks.forEach(t => m.set(t.id, t.stage))
    return m
  }, [tasks])

  const byCell = useMemo(() => {
    const m = new Map<string, Task[]>()
    tasks.forEach(t => {
      // stage 是 1 起始，列号 ci 是 0 起始 —— 必须减 1，否则整张图右移一列，
      // 且 stage=5 会落到网格外（实测因此丢过两个节点）。
      const ci = Math.min(STAGES.length - 1, Math.max(0, (t.stage ?? 3) - 1))
      const k = `${laneOf.get(t.id) ?? 0}:${ci}`
      const arr = m.get(k)
      if (arr) arr.push(t)
      else m.set(k, [t])
    })
    return m
  }, [tasks, laneOf])

  useLayoutEffect(() => {
    const g = gridRef.current
    if (!g) return

    const compute = () => {
      const gr = g.getBoundingClientRect()
      const geo = new Map<string, Geo>()
      g.querySelectorAll<HTMLElement>('[data-tid]').forEach(el => {
        const r = el.getBoundingClientRect()
        geo.set(el.dataset.tid!, { x: r.left - gr.left, y: r.top - gr.top, w: r.width, h: r.height })
      })
      setSize({ w: gr.width, h: gr.height })

      const out: Path[] = []
      const push = (d: string, soft: boolean) => out.push({ d, soft })
      const gv = (id: string) => geo.get(id)!
      const cy = (g2: Geo) => g2.y + g2.h / 2
      const cr = (g2: Geo) => g2.x + g2.w

      const cross: E[] = []
      edges.forEach(([a, b]) => {
        if (!geo.get(a) || !geo.get(b)) return
        const soft = (laneOf.get(a) ?? 0) === (laneOf.get(b) ?? 0)
        if ((colOf.get(a) ?? 0) === (colOf.get(b) ?? 0)) {
          /* 同阶段跨泳道 → 垂直连线 */
          const ga = gv(a)
          const gb = gv(b)
          const down = gb.y > ga.y
          const y1 = down ? ga.y + ga.h : ga.y
          const y2 = down ? gb.y : gb.y + gb.h
          push(`M${ga.x + ga.w / 2} ${y1} L${ga.x + ga.w / 2} ${y2}`, soft)
          return
        }
        cross.push({ a, b, soft })
      })

      /* ① 同一源、同一目标列 → 扇出：先主干后分支 */
      const fanOut: Record<string, E[]> = {}
      cross.forEach(e => {
        const k = `${e.a}|${colOf.get(e.b)}`
        ;(fanOut[k] = fanOut[k] ?? []).push(e)
      })

      const rest: E[] = []
      Object.values(fanOut).forEach(list => {
        if (list.length === 1) {
          rest.push(list[0])
          return
        }
        const first = gv(list[0].a)
        const chX = Math.round(Math.min(...list.map(o => gv(o.b).x)) - 11)
        const ys = list.map(o => cy(gv(o.b)))
        const sY = cy(first)
        const yTop = Math.min(sY, ...ys)
        const yBot = Math.max(sY, ...ys)
        push(`M${cr(first)} ${sY} L${chX} ${sY}`, false)
        push(`M${chX} ${yTop} L${chX} ${yBot}`, false)
        list.forEach(o => {
          const gb = gv(o.b)
          push(`M${chX} ${cy(gb)} L${gb.x} ${cy(gb)}`, o.soft)
        })
      })

      /* ② 同一目标、同一来源列 → 扇入：先汇流后进目标 */
      const fanIn: Record<string, E[]> = {}
      rest.forEach(e => {
        const k = `${e.b}|${colOf.get(e.a)}`
        ;(fanIn[k] = fanIn[k] ?? []).push(e)
      })

      const singles: E[] = []
      Object.values(fanIn).forEach(list => {
        if (list.length === 1) {
          singles.push(list[0])
          return
        }
        const gb = gv(list[0].b)
        const lo = Math.max(...list.map(o => cr(gv(o.a)))) + 6
        const hi = gb.x - 6
        const chX = Math.round(lo <= hi ? (lo + hi) / 2 : lo)
        const ys = list.map(o => cy(gv(o.a)))
        const tY = cy(gb)
        list.forEach(o => {
          const ga = gv(o.a)
          push(`M${cr(ga)} ${cy(ga)} L${chX} ${cy(ga)}`, o.soft)
        })
        push(`M${chX} ${Math.min(tY, ...ys)} L${chX} ${Math.max(tY, ...ys)}`, false)
        push(`M${chX} ${tY} L${gb.x} ${tY}`, false)
      })

      /* ③ 剩下的单条边：Z 型折线，竖线段错开避免重叠 */
      const usedMx: Record<string, number> = {}
      singles.forEach(({ a, b, soft }) => {
        const ga = gv(a)
        const gb = gv(b)
        const x1 = cr(ga)
        const y1 = cy(ga)
        const x2 = gb.x
        const y2 = cy(gb)
        if (x2 <= x1) {
          push(`M${ga.x + ga.w / 2} ${ga.y + ga.h} L${ga.x + ga.w / 2} ${y2}`, soft)
          return
        }
        let mx = Math.round((x1 + x2) / 2)
        const k = String(Math.round(mx / 6))
        usedMx[k] = (usedMx[k] ?? 0) + 1
        mx += (usedMx[k] - 1) * 11
        push(`M${x1} ${y1} L${mx} ${y1} L${mx} ${y2} L${x2} ${y2}`, soft)
      })

      setPaths(out)
    }

    compute()
    const ro = new ResizeObserver(compute)
    ro.observe(g)
    return () => ro.disconnect()
  }, [tasks, edges, laneOf, colOf, rows.length])

  return (
    <div className="scrollbox">
      <div
        className="lanegrid"
        ref={gridRef}
        style={{
          gridTemplateRows: `36px repeat(${Math.max(rows.length, 1)}, minmax(46px, 1fr))`,
        }}
      >
        <div className="cellh corner">执行者 ＼ 阶段</div>
        {STAGES.map((s, i) => (
          <div key={s} className={'cellh' + (i === STAGES.length - 1 ? ' last' : '')}>
            {s}
          </div>
        ))}

        {rows.map((row, li) => (
          <Fragment key={row.id}>
            <div className="lanename">
              <span className="sdot" style={{ background: row.color }} />
              <b title={row.name}>{row.name}</b>
              <span className="role">{row.role}</span>
            </div>
            {STAGES.map((_, ci) => {
              const list = byCell.get(`${li}:${ci}`) ?? []
              return (
                <div
                  key={ci}
                  className={
                    'cell' +
                    (li % 2 ? ' alt' : '') +
                    (row.id === NONE ? ' blkc' : '') +
                    (ci === STAGES.length - 1 ? ' edge' : '')
                  }
                >
                  {list.slice(0, 1).map(t => {
                    const agent = agents.find(a => a.id === t.assignee)
                    return (
                      <div
                        key={t.id}
                        data-tid={t.id}
                        data-s={t.status}
                        className={'node lane-node' + (selected === t.id ? ' sel' : '')}
                        onClick={() => onSelect(t.id)}
                        title={`${t.title} · ${STATUS_TEXT[t.status]}`}
                      >
                        <div className="nt">{t.title}</div>
                        <div className="ns">
                          <span
                            className="sdot"
                            style={{ background: STATUS_COLOR[t.status] }}
                          />
                          {t.status === 'running'
                            ? `${t.progress}%`
                            : t.status === 'blocked'
                              ? '阻塞'
                              : STATUS_TEXT[t.status]}
                          {agent && t.status === 'running' ? ' · ' + agent.name.split(' ')[0] : ''}
                        </div>
                        {t.status === 'running' && (
                          <div className="np">
                            <i style={{ width: `${t.progress}%` }} />
                          </div>
                        )}
                      </div>
                    )
                  })}
                  {list.length > 1 && (
                    <span
                      style={{
                        position: 'absolute',
                        right: 4,
                        top: 4,
                        fontSize: 9,
                        color: '#9ca3af',
                      }}
                    >
                      +{list.length - 1}
                    </span>
                  )}
                </div>
              )
            })}
          </Fragment>
        ))}

        <svg className="wires" width={size.w} height={size.h}>
          <defs>
            <marker
              id="mg"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto-start-reverse"
            >
              <path d="M2 1L8 5L2 9" fill="none" stroke="#cbd2db" strokeWidth="1.5" strokeLinecap="round" />
            </marker>
            <marker
              id="mo"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto-start-reverse"
            >
              <path d="M2 1L8 5L2 9" fill="none" stroke="#c2410c" strokeWidth="1.5" strokeLinecap="round" />
            </marker>
          </defs>
          {paths.map((p, i) => (
            <path
              key={i}
              d={p.d}
              fill="none"
              strokeLinejoin="round"
              stroke={p.soft ? '#cbd2db' : '#c2410c'}
              strokeWidth={p.soft ? 1.2 : 1.35}
              strokeDasharray={p.soft ? undefined : '6 4'}
              opacity={p.soft ? 1 : 0.7}
              markerEnd={`url(#${p.soft ? 'mg' : 'mo'})`}
            />
          ))}
        </svg>
      </div>
    </div>
  )
}
