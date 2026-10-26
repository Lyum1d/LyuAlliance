import type { Agent, Task } from '../types'
import { STATUS_TEXT } from '../types'
import { agentColor, elapsedMs, fmtDuration } from '../lib'

interface Props {
  agents: Agent[]
  tasks: Task[]
  onSelectTask: (id: string) => void
}

export default function AgentStrip({ agents, tasks, onSelectTask }: Props) {
  const running = tasks.filter(t => t.status === 'running')
  const team = agents.filter(a => a.in_team)

  const ordered = [...team].sort((a, b) => {
    const wa = running.some(t => t.assignee === a.id) ? 0 : 1
    const wb = running.some(t => t.assignee === b.id) ? 0 : 1
    if (wa !== wb) return wa - wb
    if (a.kind === 'conductor') return -1
    if (b.kind === 'conductor') return 1
    return a.lane - b.lane
  })

  return (
    <div className="strip">
      {ordered.map(a => {
        const t = running.find(x => x.assignee === a.id)
        const isConductor = a.kind === 'conductor'
        const tagClass =
          a.kind === 'conductor' ? 'conductor' : a.kind === 'bridge' ? 'bridge' : 'native'
        return (
          <div
            key={a.id}
            className={
              'agent-card' + (t ? ' working' : '') + (a.health !== 'online' ? ' offline' : '')
            }
            onClick={() => t && onSelectTask(t.id)}
            style={{ cursor: t ? 'pointer' : 'default' }}
          >
            <div className="ac-head">
              <span className="sdot" style={{ background: t ? '#2563eb' : agentColor(a) }} />
              <span className="ac-name" title={a.name}>
                {a.name}
              </span>
              <span className={'ac-tag ' + tagClass}>{a.kind}</span>
            </div>

            {t ? (
              <>
                <div className="ac-task">
                  {t.title} · {t.id}
                </div>
                <div className="hbar">
                  <i style={{ width: `${t.progress}%` }} />
                </div>
                <div className="ac-meta">
                  <span>{t.progress}%</span>
                  {a.adapter === 'ollama' ? (
                    <span className="heart">
                      <b />
                      流式生成
                    </span>
                  ) : a.adapter === 'doubao' ? (
                    <span className="heart" style={{ color: '#C2410C' }}>
                      <b style={{ background: '#C2410C' }} />
                      桥接执行中
                    </span>
                  ) : (
                    <span style={{ color: '#b45309' }}>模拟推进</span>
                  )}
                  <span>{fmtDuration(elapsedMs(t))}</span>
                </div>
              </>
            ) : isConductor ? (
              <>
                <div className="ac-task muted">休眠中 · 等下一个 digest</div>
                <div className="ac-meta">
                  <span>计划需你批准</span>
                  <span>预算 0%</span>
                </div>
              </>
            ) : (
              <>
                <div className="ac-task muted">
                  {a.health === 'degraded' ? '降级 · 待接入' : '空闲 · 待命中'}
                </div>
                <div className="ac-meta">
                  <span>{a.cost === 'free' ? '本地免费' : '按量计费'}</span>
                  <span>{a.kind === 'human' ? '需要时介入' : '待命中'}</span>
                </div>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}
