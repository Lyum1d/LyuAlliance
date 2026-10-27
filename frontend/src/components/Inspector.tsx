import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Agent, Candidate, Task } from '../types'
import { STATUS_TEXT } from '../types'
import { BADGE_CLASS, elapsedMs, fmtDuration } from '../lib'
import TaskEditor from './TaskEditor'

interface Props {
  task: Task | null
  agents: Agent[]
  allTasks: Task[]
  /** [from, to] —— to 依赖 from */
  edges: [string, string][]
  onAction: (kind: 'dispatch' | 'force' | 'cancel' | 'approve', tid: string) => void
  onNotify?: (msg: string, err?: boolean) => void
  onChanged?: () => void
}

export default function Inspector({
  task,
  agents,
  allTasks,
  edges,
  onAction,
  onNotify,
  onChanged,
}: Props) {
  const [cands, setCands] = useState<Candidate[]>([])
  const [editing, setEditing] = useState(false)
  const outRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    if (!task) {
      setCands([])
      return
    }
    api
      .routing(task.id)
      .then(r => alive && setCands(r.candidates ?? []))
      .catch(() => alive && setCands([]))
    return () => {
      alive = false
    }
  }, [task?.id, task?.status])

  // 切到别的任务就退出编辑，免得把上一条的输入带过来
  useEffect(() => {
    setEditing(false)
  }, [task?.id])

  // 流式输出时始终贴底
  useEffect(() => {
    const el = outRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [task?.output])

  if (!task) {
    return (
      <div className="panel inspector">
        <div className="empty">
          <b>未选中任务</b>
          <span>点图上任意节点查看详情</span>
        </div>
      </div>
    )
  }

  const agent = agents.find(a => a.id === task.assignee)
  const top = cands[0]
  const streaming = task.status === 'running'
  const hasOut = !!task.output && task.output.length > 0

  if (editing) {
    const deps = edges.filter(([, to]) => to === task.id).map(([from]) => from)
    return (
      <div className="panel inspector">
        <div className="insp-edit-head">
          <div>
            <b>编辑 {task.id}</b>
            <span>{task.title}</span>
          </div>
          <button className="btn sm" onClick={() => setEditing(false)}>
            关闭
          </button>
        </div>
        <TaskEditor
          task={task}
          allTasks={allTasks}
          agents={agents}
          deps={deps}
          onSave={async body => {
            try {
              await api.taskPatch(task.id, body)
              setEditing(false)
              onNotify?.('已保存修改')
              onChanged?.()
            } catch (e: any) {
              throw e            // 交给编辑器显示错误，保住用户输入
            }
          }}
          onCancel={() => setEditing(false)}
        />
      </div>
    )
  }

  return (
    <div className="panel inspector">
      <div className="ins-head">
        <h2>{task.title}</h2>
        <span className={'badge ' + (BADGE_CLASS[task.status] ?? 'b-pend')}>
          {STATUS_TEXT[task.status]}
        </span>
      </div>
      <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 6 }}>
        {task.id} ·{' '}
        {task.status === 'running'
          ? `已运行 ${fmtDuration(elapsedMs(task))}`
          : task.duration_ms
            ? `耗时 ${fmtDuration(task.duration_ms)}`
            : '未开始计时'}
      </div>

      <div className="kv">
        <span>执行者</span>
        <span>
          <b>{agent?.name ?? '未指派'}</b>{' '}
          {agent && <span style={{ color: 'var(--faint)' }}>{agent.kind}</span>}
        </span>
      </div>
      <div className="kv">
        <span>阶段</span>
        <span>{['澄清', '设计', '实现', '验证', '收尾'][task.stage] ?? '—'}</span>
      </div>
      <div className="kv">
        <span>依赖</span>
        <span>{task.reason ? task.reason : '前置已满足'}</span>
      </div>
      {task.required_caps.length > 0 && (
        <div className="kv">
          <span>能力要求</span>
          <span>{task.required_caps.join(' / ')}</span>
        </div>
      )}

      <div className="sec">
        <div className="sec-t">
          产出
          {streaming && hasOut && (
            <span className="streaming" style={{ marginLeft: 8 }}>
              <b />
              流式输出中
            </span>
          )}
        </div>
        {hasOut ? (
          <>
            <div className="out" ref={outRef}>
              {task.output}
            </div>
            <div className="out-meta">
              <span>{task.output!.length} 字符</span>
              {agent?.adapter === 'ollama' && <span>来源：{agent.model} 真实生成</span>}
              {agent?.adapter === 'doubao' && <span>来源：豆包工作任务（CDP 桥接）真实生成</span>}
              {streaming && <span>仍在生成…</span>}
            </div>
          </>
        ) : (
          <div className="out empty">
            {streaming ? (
              agent?.adapter === 'ollama' ? (
                <>
                  <span className="streaming">
                    <b />
                    等待首个 token…
                  </span>
                  <span style={{ fontSize: 11 }}>9B 模型冷启动 + 预填充约需 8-10 秒</span>
                </>
              ) : agent?.adapter === 'doubao' ? (
                <>
                  <span className="streaming">
                    <b />
                    正在驱动豆包「新工作任务」…
                  </span>
                  <span style={{ fontSize: 11 }}>
                    建任务 → 注入 → 发送 → 回读，通常 10-30 秒；期间豆包窗口会被占用
                  </span>
                </>
              ) : (
                <>
                  <span style={{ fontSize: 11.5 }}>
                    该 Agent 尚未接入真实执行体，进度由模拟器推进，不会有文本产出。
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--faint)' }}>
                    M3 已接：qwen3.5:9b（Ollama）、豆包（CDP 工作任务）
                  </span>
                </>
              )
            ) : task.status === 'done' &&
              (!agent?.adapter || agent.adapter === 'none') ? (
              <>
                <span style={{ fontSize: 11.5 }}>
                  这个「完成」是模拟器推进的结果，<b>没有真实产出</b>——执行者还没接执行体。
                </span>
                <span style={{ fontSize: 11, color: 'var(--faint)' }}>
                  别把它当成交付物。要真产出就把节点改派给 qwen3.5:9b 或豆包。
                </span>
              </>
            ) : (
              <span>还没有产出</span>
            )}
          </div>
        )}
      </div>

      {task.context && (task.context.upstream?.length || task.context.decisions?.length) ? (
        <div className="sec">
          <div className="sec-t">交接上下文 · 上游给的</div>
          <div className="ho">
            {(task.context.upstream ?? []).map(u => (
              <div className="ho-up" key={u.task_id}>
                <div className="ho-head">
                  <b>{u.title}</b>
                  <span>
                    {u.chars} 字
                    {u.real ? '' : ' · 无真实产出'}
                  </span>
                </div>
                {u.real && <div className="ho-ex">{u.excerpt.slice(0, 160)}…</div>}
              </div>
            ))}
            {(task.context.decisions ?? []).length > 0 && (
              <div className="ho-dec">
                <span className="ho-tag">已定决策 · 自动抽取</span>
                {(task.context.decisions ?? []).slice(0, 5).map((d, i) => (
                  <div key={i} className="crit">
                    <b>·</b>
                    <span>{d.text}</span>
                  </div>
                ))}
              </div>
            )}
            {(task.context.failures ?? []).length > 0 && (
              <div className="ho-dec">
                <span className="ho-tag" style={{ color: '#b91c1c' }}>
                  之前失败过
                </span>
                {(task.context.failures ?? []).map((f, i) => (
                  <div key={i} className="crit">
                    <b>×</b>
                    <span>{f.reason}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {task.context.handoff_id && (
            <button
              className="btn sm"
              style={{ marginTop: 7 }}
              onClick={async () => {
                try {
                  await api.rejectHandoff(task.context!.handoff_id!, '人工退回：上下文不足以开工')
                  onNotify?.('已退回，下游任务回到待开始')
                  onChanged?.()
                } catch (e: any) {
                  onNotify?.((e?.message ?? '退回失败').replace(/^\d+\s*/, ''), true)
                }
              }}
            >
              退回这个交接
            </button>
          )}
        </div>
      ) : null}

      {task.acceptance.length > 0 && (
        <div className="sec">
          <div className="sec-t">验收标准</div>
          {task.acceptance.map((c, i) => (
            <div className="crit" key={i}>
              <b>{task.status === 'done' ? '✓' : '·'}</b>
              <span>{c}</span>
            </div>
          ))}
        </div>
      )}

      <div className="sec">
        <div className="sec-t">为什么选它（能力路由）</div>
        <div className="route">
          {cands.length === 0 && <span>暂无候选</span>}
          {cands.slice(0, 3).map(c => (
            <div className={'rrow' + (c.agent_id === task.assignee ? ' mine' : '')} key={c.agent_id}>
              <span>
                {c.name}
                <span style={{ color: 'var(--faint)' }}>
                  {' '}
                  · 匹配 {c.cap_match} · {c.kind}
                </span>
              </span>
              <b>{c.score.toFixed(2)}</b>
            </div>
          ))}
          {top && (task.status === 'blocked' || task.status === 'failed') && top.cap_match < 0.35 && (
            <div style={{ marginTop: 6, color: '#b45309' }}>
              所有成员能力匹配都低于 0.35，系统不会凑合派活。请在 Agent 池邀进 Codex，或手动强制派活。
            </div>
          )}
        </div>
      </div>

      <div className="actions">
        {task.status !== 'done' && task.status !== 'failed' && (
          <button className="btn" onClick={() => setEditing(true)}>
            编辑
          </button>
        )}
        {task.status === 'running' && (
          <button className="btn danger" onClick={() => onAction('cancel', task.id)}>
            中止运行
          </button>
        )}
        {task.status === 'reviewing' && (
          <>
            <button className="btn" onClick={() => onAction('cancel', task.id)}>
              打回重做
            </button>
            <button className="btn primary" onClick={() => onAction('approve', task.id)}>
              通过验收
            </button>
          </>
        )}
        {task.status === 'pending' && (
          <button className="btn primary" onClick={() => onAction('dispatch', task.id)}>
            派活给最佳候选
          </button>
        )}
        {task.status === 'blocked' && (
          <button className="btn" onClick={() => onAction('force', task.id)}>
            强制派活（留痕）
          </button>
        )}
        {task.status === 'failed' && (
          <button className="btn" onClick={() => onAction('dispatch', task.id)}>
            重新派活
          </button>
        )}
        {task.status === 'done' && (
          <span style={{ fontSize: 12, color: 'var(--faint)' }}>已验收完成，无可用操作</span>
        )}
      </div>
    </div>
  )
}
