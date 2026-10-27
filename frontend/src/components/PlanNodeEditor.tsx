import { useState } from 'react'
import type { PlanNode } from '../types'

/** 可派活的能力词（与后端 CAP_VOCAB_FOR_MODEL 对齐，planning/decide 是保留词不列） */
const CAPS = [
  'general', 'code', 'code_write', 'debug', 'doc', 'research', 'web_search',
  'review', 'shell', 'reasoning', 'summarize', 'long_context', 'refactor',
  'deploy', 'slides',
]

const STAGES = ['澄清', '设计', '实现', '验证', '收尾']

type Props = {
  /** null 表示新增 */
  node: PlanNode | null
  /** 可选的依赖目标（同草案里的其他节点 key） */
  allKeys: string[]
  onSave: (body: Record<string, unknown>) => Promise<void>
  onCancel: () => void
}

export default function PlanNodeEditor({ node, allKeys, onSave, onCancel }: Props) {
  const [title, setTitle] = useState(node?.title ?? '')
  const [objective, setObjective] = useState(node?.objective ?? '')
  const [stage, setStage] = useState(node?.stage ?? 3)
  const [accText, setAccText] = useState((node?.acceptance ?? []).join('\n'))
  const [deps, setDeps] = useState<string[]>(node?.deps ?? [])
  const [caps, setCaps] = useState<string[]>(node?.required_caps ?? ['general'])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 后端限制每节点 1~2 个能力；选满 2 个后其余置灰，全不选时回落到 general
  const toggleCap = (c: string, on: boolean) => {
    setCaps(prev => {
      if (on) return prev.includes(c) || prev.length >= 2 ? prev : [...prev, c]
      const next = prev.filter(x => x !== c)
      return next.length ? next : ['general']
    })
  }

  const toggleDep = (k: string, on: boolean) =>
    setDeps(prev => (on ? [...prev, k] : prev.filter(x => x !== k)))

  const save = async () => {
    if (!title.trim()) {
      setErr('标题不能为空')
      return
    }
    const acceptance = accText.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 4)
    setBusy(true)
    setErr('')
    try {
      if (node) {
        await onSave({
          title: title.trim(),
          objective: objective.trim(),
          stage,
          acceptance,
          deps,
          required_caps: caps,
        })
      } else {
        await onSave({
          add: {
            title: title.trim(),
            objective: objective.trim(),
            stage,
            acceptance,
            required_caps: caps,
          },
        })
      }
    } catch (e: any) {
      setErr(e?.message ?? '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pn-edit">
      <div className="pn-edit-row">
        <label className="grow">
          <span>标题</span>
          <input
            value={title}
            onChange={e => setTitle(e.target.value)}
            maxLength={24}
            placeholder="不超过 24 字"
          />
        </label>
        <label className="fix">
          <span>阶段</span>
          <select value={stage} onChange={e => setStage(Number(e.target.value))}>
            {STAGES.map((s, i) => (
              <option key={s} value={i + 1}>
                {i + 1} {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label>
        <span>这一步要产出什么</span>
        <textarea
          rows={2}
          value={objective}
          onChange={e => setObjective(e.target.value)}
          maxLength={200}
          placeholder="一句话说清交付物，别写成「负责 XX 模块」"
        />
      </label>

      <label>
        <span>验收标准（一行一条，最多 4 条）</span>
        <textarea
          rows={3}
          value={accText}
          onChange={e => setAccText(e.target.value)}
          placeholder={'要能被外部检查，例如：\n给出 3 条可落地建议\n错误密码返回 401'}
        />
      </label>

      <div className="pn-edit-row wrap">
        <span className="lbl">能力（1~2 个）</span>
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

      {node && allKeys.length > 1 && (
        <div className="pn-edit-row wrap">
          <span className="lbl">依赖（等这些节点跑完才开工）</span>
          {allKeys
            .filter(k => k !== node.key)
            .map(k => (
              <label key={k} className={'chip-check' + (deps.includes(k) ? ' on' : '')}>
                <input
                  type="checkbox"
                  checked={deps.includes(k)}
                  onChange={e => toggleDep(k, e.target.checked)}
                />
                {k}
              </label>
            ))}
        </div>
      )}

      {err && <div className="pn-edit-err">{err}</div>}

      <div className="pn-edit-actions">
        <button className="btn sm" onClick={onCancel} disabled={busy}>
          取消
        </button>
        <button className="btn primary sm" onClick={save} disabled={busy}>
          {busy ? '保存中…' : node ? '保存修改' : '添加节点'}
        </button>
      </div>
    </div>
  )
}
