import { useState } from 'react'
import type { Agent, Task } from '../types'

const CAPS = [
  'general', 'code', 'code_write', 'debug', 'doc', 'research', 'web_search',
  'review', 'shell', 'reasoning', 'summarize', 'long_context', 'refactor',
  'deploy', 'slides',
]

type Props = {
  task: Task
  allTasks: Task[]
  agents: Agent[]
  /** 当前任务的依赖（从图里取，Task 本身不带） */
  deps: string[]
  onSave: (body: Record<string, unknown>) => Promise<void>
  onCancel: () => void
}

/**
 * 改一个**已开工**的任务。
 *
 * 和后端的策略保持一致：running / reviewing 只允许改验收标准。
 * 不是技术限制，是语义——prompt 已经交给执行体了，改标题目标纯属自欺，
 * 但"算不算通过"的判断依据随时可以调。
 */
export default function TaskEditor({ task, allTasks, agents, deps: curDeps, onSave, onCancel }: Props) {
  const contentLocked = task.status === 'running' || task.status === 'reviewing'

  const [title, setTitle] = useState(task.title)
  const [objective, setObjective] = useState(task.objective ?? '')
  const [accText, setAccText] = useState((task.acceptance ?? []).join('\n'))
  const [caps, setCaps] = useState<string[]>(task.required_caps ?? ['general'])
  const [assignee, setAssignee] = useState<string>(task.assignee ?? '')
  const [deps, setDeps] = useState<string[]>(curDeps)
  const [priority, setPriority] = useState(task.priority ?? 1)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const toggleCap = (c: string, on: boolean) => {
    setCaps(prev => {
      if (on) return prev.includes(c) || prev.length >= 2 ? prev : [...prev, c]
      const next = prev.filter(x => x !== c)
      return next.length ? next : ['general']
    })
  }

  const save = async () => {
    if (!title.trim()) {
      setErr('标题不能为空')
      return
    }
    const accArr = accText.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 5)
    const body: Record<string, unknown> = {}

    if (!contentLocked) {
      if (title.trim() !== task.title) body.title = title.trim()
      if (objective.trim() !== (task.objective ?? '')) body.objective = objective.trim()
      if (caps.join('|') !== (task.required_caps ?? []).join('|')) body.required_caps = caps
      if (assignee !== (task.assignee ?? '')) body.assignee = assignee || null
      if (priority !== task.priority) body.priority = priority
      if (deps.slice().sort().join('|') !== curDeps.slice().sort().join('|')) body.deps = deps
    }
    if (accArr.join('|') !== (task.acceptance ?? []).join('|')) body.acceptance = accArr

    if (!Object.keys(body).length) {
      setErr('没有改动')
      return
    }

    setBusy(true)
    setErr('')
    try {
      await onSave(body)
    } catch (e: any) {
      setErr((e?.message ?? '保存失败').replace(/^\d+\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pn-edit">
      {contentLocked && (
        <div className="task-lock-tip">
          这个任务已经是「{task.status}」，标题 / 目标 / 执行者都改不动了——prompt 早就发出去。
          现在只能调验收标准，或者先取消再重来。
        </div>
      )}

      {!contentLocked && (
        <>
          <div className="pn-edit-row">
            <label className="grow">
              <span>标题</span>
              <input value={title} onChange={e => setTitle(e.target.value)} maxLength={40} />
            </label>
            <label className="fix">
              <span>优先级</span>
              <select value={priority} onChange={e => setPriority(Number(e.target.value))}>
                {[0, 1, 2, 3, 4, 5].map(p => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label>
            <span>目标（派活时原样写进 prompt）</span>
            <textarea
              rows={2}
              value={objective}
              onChange={e => setObjective(e.target.value)}
              maxLength={400}
            />
          </label>

          <label>
            <span>执行者</span>
            <select value={assignee} onChange={e => setAssignee(e.target.value)}>
              <option value="">未指派（按能力自动路由）</option>
              {agents
                .filter(a => a.in_team && a.role !== 'human' && a.kind !== 'conductor')
                .map(a => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                    {a.health === 'unavailable' ? '（不可用）' : ''}
                  </option>
                ))}
            </select>
          </label>

          <div className="pn-edit-row wrap">
            <span className="lbl">能力（1~2 个，影响自动路由）</span>
            {CAPS.map(c => {
              const on = caps.includes(c)
              const disabled = !on && caps.length >= 2
              return (
                <label key={c} className={'chip-check' + (on ? ' on' : '') + (disabled ? ' off' : '')}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={disabled}
                    onChange={e => toggleCap(c, e.target.checked)}
                  />
                  {c}
                </label>
              )
            })}
          </div>

          {allTasks.length > 1 && (
            <div className="pn-edit-row wrap">
              <span className="lbl">依赖（这些跑完它才能开工）</span>
              {allTasks
                .filter(t => t.id !== task.id)
                .map(t => (
                  <label
                    key={t.id}
                    className={'chip-check' + (deps.includes(t.id) ? ' on' : '')}
                    title={t.title}
                  >
                    <input
                      type="checkbox"
                      checked={deps.includes(t.id)}
                      onChange={e =>
                        setDeps(prev =>
                          e.target.checked ? [...prev, t.id] : prev.filter(x => x !== t.id),
                        )
                      }
                    />
                    {t.title.slice(0, 8)}
                  </label>
                ))}
            </div>
          )}
        </>
      )}

      <label>
        <span>验收标准（一行一条，最多 5 条）</span>
        <textarea
          rows={4}
          value={accText}
          onChange={e => setAccText(e.target.value)}
          placeholder={'要能被外部检查，例如：\n错误密码返回 401\n导出 CSV 含表头'}
        />
      </label>

      {err && <div className="pn-edit-err">{err}</div>}

      <div className="pn-edit-actions">
        <button className="btn sm" onClick={onCancel} disabled={busy}>
          取消
        </button>
        <button className="btn primary sm" onClick={save} disabled={busy}>
          {busy ? '保存中…' : '保存修改'}
        </button>
      </div>
    </div>
  )
}
