import { useState } from 'react'
import type { Agent } from '../types'
import { agentColor, initials } from '../lib'

interface Props {
  agents: Agent[]
  onJoin: (id: string) => Promise<void>
  onLeave: (id: string) => Promise<void>
  onRescan: () => void
  onProbe: (id: string) => Promise<void>
}

const KIND_TEXT: Record<string, string> = {
  native: '可直接派活',
  bridge: '需接管界面',
  conductor: '调度者',
  human: '人工节点',
}

const DESC: Record<string, string> = {
  a_qwen: '长上下文主力，262k 窗口，适合读大文件与模板化代码。',
  a_wb: '直接驱动桌面端（CDP :9444）。代价：派活时会操作你正在用的窗口；需带调试端口重启一次。',
  a_doubao: '唯一能联网检索的成员。通过 CDP 驱动桌面端「工作任务」，执行时会占用它的窗口。',
  a_dsh: '代码编写主力。本地网关要求鉴权，key 未取到，当前降级为人工桥接。',
  a_codex: '适配器已写完（codex exec --json），但本机无额度且未登录，默认不启用：不会发出任何真实请求。',
  a_r1: '推理优于 qwen，但速度慢。适合数学/逻辑题，不适合长文。',
  a_brain: '拆解目标、排期、派活、汇总。计划必须经你批准才能开工。',
  a_human: '需要人判断的节点（技术选型、联调验收）。',
}

// 已接入但不可用：不给「邀进」按钮，改成「检测」，避免出现"进了组却永远派不到活"的假象
const UNAVAILABLE_TEXT: Record<string, string> = {
  a_codex: '未启用',
  a_wb: '未开启',
}

export default function AgentPool({ agents, onJoin, onLeave, onRescan, onProbe }: Props) {
  const [busy, setBusy] = useState<string | null>(null)

  const act = async (a: Agent) => {
    setBusy(a.id)
    try {
      if (a.in_team) await onLeave(a.id)
      else await onJoin(a.id)
    } finally {
      setBusy(null)
    }
  }

  const probe = async (a: Agent) => {
    setBusy(a.id)
    try {
      await onProbe(a.id)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="pool">
      <div className="pool-head">
        <h2>已发现 {agents.length} 个 Agent</h2>
        <span className="hint">
          探测方式：进程 / 端口 / CLI / 配置指纹 ·
          标 <b style={{ color: '#c2410c' }}>bridge</b> 的无编程接口，只能靠界面接管
        </span>
        <button className="btn" style={{ marginLeft: 'auto' }} onClick={onRescan}>
          刷新
        </button>
      </div>

      <div className="pool-grid">
        {agents.map(a => {
          const unavailable = a.health === 'unavailable'
          const canProbe = unavailable && !!a.adapter && a.adapter !== 'none'
          return (
          <div key={a.id} className={'pcard' + (a.in_team ? '' : ' off')}>
            <div className="pc-top">
              <div className="avatar" style={{ background: agentColor(a) }}>
                {initials(a.name)}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="pc-name">
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {a.name}
                  </span>
                  <span className={'ac-tag ' + (a.kind === 'bridge' ? 'bridge' : a.kind === 'conductor' ? 'conductor' : 'native')}>
                    {a.kind}
                  </span>
                </div>
                <div className="pc-sub">
                  {a.model || KIND_TEXT[a.kind]} · {a.evidence}
                </div>
              </div>
              <span
                className="sdot"
                style={{
                  marginTop: 6,
                  background:
                    a.health === 'online' ? '#16a34a' : a.health === 'degraded' ? '#d97706' : '#9ca3af',
                }}
              />
            </div>

            <div className="pc-desc">{DESC[a.id] ?? KIND_TEXT[a.kind]}</div>

            <div className="caps">
              {Object.entries(a.caps)
                .sort((x, y) => y[1] - x[1])
                .slice(0, 4)
                .map(([k, v]) => (
                  <span key={k} className={'cap' + (v >= 0.8 ? ' hi' : '')}>
                    {k} {v.toFixed(2)}
                  </span>
                ))}
            </div>

            <div className="pc-foot">
              <button
                className={'btn' + (a.in_team ? '' : canProbe ? '' : ' primary')}
                disabled={busy === a.id || a.kind === 'conductor' || (unavailable && !canProbe)}
                onClick={() => (canProbe ? probe(a) : act(a))}
                title={
                  a.kind === 'conductor'
                    ? '主脑固定在场'
                    : canProbe
                      ? '现场探测它现在能不能用（未登录时几乎零成本）'
                      : unavailable
                        ? '已接入但不可用，且没有可探测的执行体'
                        : ''
                }
              >
                {a.kind === 'conductor'
                  ? '常驻'
                  : canProbe
                    ? busy === a.id
                      ? '检测中…'
                      : '检测'
                    : unavailable
                      ? (UNAVAILABLE_TEXT[a.id] ?? '不可用')
                      : a.in_team
                        ? '移出'
                        : '邀进'}
              </button>
              <span style={{ fontSize: 11, color: 'var(--faint)' }}>
                {unavailable ? '接入未启用' : a.in_team ? '已在组内' : '未在组内'}
              </span>
              <span className="ev">{a.cost === 'free' ? '免费' : '计费'}</span>
            </div>
          </div>
          )
        })}
      </div>
    </div>
  )
}
