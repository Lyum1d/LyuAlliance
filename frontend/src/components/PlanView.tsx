import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Plan, PlanResp } from '../types'
import PlanNodeEditor from './PlanNodeEditor'
import { STAGES } from '../types'

interface Props {
  onNotify: (msg: string, err?: boolean) => void
  onChanged: () => void
}

export default function PlanView({ onNotify, onChanged }: Props) {
  const [data, setData] = useState<PlanResp | null>(null)
  const [goal, setGoal] = useState('')
  const [busy, setBusy] = useState<'' | 'draft' | 'approve' | 'reject'>('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.plan())
    } catch (e: any) {
      setErr(e?.message ?? '加载失败')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const plan: Plan | null = data?.plan ?? null

  const draft = async () => {
    if (goal.trim().length < 4) {
      onNotify('先把目标写清楚一点（至少 4 个字）', true)
      return
    }
    setBusy('draft')
    setErr(null)
    try {
      const r = await api.planDraft(goal.trim())
      setData(d => (d ? { ...d, plan: r.plan, budget: r.budget } : d))
      onNotify(`主脑拆出 ${r.plan.tasks.length} 个节点，等你批准`)
    } catch (e: any) {
      const m = (e?.message ?? '').replace(/^\d+\s*/, '')
      setErr(m)
      onNotify('拆解失败', true)
    } finally {
      setBusy('')
    }
  }

  const approve = async () => {
    if (!plan) return
    setBusy('approve')
    try {
      const r = await api.planApprove()
      onNotify(`已按草案开工，替换为 ${r.tasks} 个任务`)
      await load()
      onChanged()
    } catch (e: any) {
      onNotify((e?.message ?? '批准失败').replace(/^\d+\s*/, ''), true)
    } finally {
      setBusy('')
    }
  }

  const reject = async () => {
    setBusy('reject')
    try {
      await api.planReject(note.trim() || '未填写意见')
      onNotify('已驳回这份草案')
      setNote('')
      await load()
    } catch (e: any) {
      onNotify((e?.message ?? '驳回失败').replace(/^\d+\s*/, ''), true)
    } finally {
      setBusy('')
    }
  }

  // 抛错而不是内部吞掉：节点编辑器要靠它判断保存是否成功，失败时保留用户输入
  const patch = async (key: string, body: Record<string, unknown>) => {
    const r = await api.planPatch(key, body)
    setData(d => (d ? { ...d, plan: r.plan } : d))
  }

  const budget = data?.budget
  const simCount = plan ? plan.tasks.filter(t => t.assigned && !t.real).length : 0

  return (
    <div className="planview">
      <div className="plan-goal">
        <input
          value={goal}
          onChange={e => setGoal(e.target.value)}
          placeholder="写一句目标，主脑会拆成任务 DAG。例：做一个团队待办看板，能注册登录、建待办、拖拽排序，数据存 SQLite"
          onKeyDown={e => {
            if (e.key === 'Enter' && !busy) void draft()
          }}
        />
        <button className="btn primary" disabled={!!busy} onClick={draft}>
          {busy === 'draft' ? '拆解中…' : plan ? '重新拆解' : '让主脑拆解'}
        </button>
      </div>

      {err && <div className="plan-err">{err}</div>}

      {!plan && (
        <div className="panel plan-empty">
          <b>还没有草案</b>
          <span>主脑只做规划、不执行。拆出来的节点会逐条列在这里，你改完、批准后才会真正开工。</span>
          {data?.last && (
            <span style={{ fontSize: 11, color: 'var(--faint)' }}>
              上一份草案「{data.last.goal_restated || data.last.goal}」已
              {data.last.status === 'approved' ? '批准开工' : data.last.status === 'rejected' ? `驳回（${data.last.note || '无意见'}）` : '作废'}
            </span>
          )}
        </div>
      )}

      {plan && (
        <>
          <div className="plan-summary">
            <div>
              <span className="ps-label">主脑理解的目标</span>
              <b>{plan.goal_restated || plan.goal}</b>
            </div>
            <div className="ps-nums">
              <span>
                <em>{plan.tasks.length}</em> 节点
              </span>
              {simCount > 0 && (
                <span className="warn">
                  <em>{simCount}</em> 个派给无真实执行体的成员（开工后由模拟推进）
                </span>
              )}
            </div>
          </div>

          <div className="plan-body">
            <div className="panel plan-list">
              <div className="plan-list-head">
                <h3>拆解草案</h3>
                <span className="hint">点执行者可换人，批准前后都不会自动开工</span>
              </div>

              {STAGES.map((sname, i) => {
                const stage = i + 1
                const nodes = plan.tasks.filter(t => t.stage === stage)
                if (!nodes.length) return null
                return (
                  <div key={stage} className="plan-group">
                    <div className="plan-group-head">
                      <span className="stage-no">{stage}</span>
                      {sname}
                      <em>{nodes.length} 个</em>
                    </div>
                    {nodes.map(n => (
                      <div key={n.key} className="pnode">
                        {editingKey === n.key ? (
                          <PlanNodeEditor
                            node={n}
                            allKeys={plan.tasks.map(t => t.key)}
                            onSave={async body => {
                              await patch(n.key, body)
                              setEditingKey(null)
                              onNotify(`${n.key} 已更新`)
                            }}
                            onCancel={() => setEditingKey(null)}
                          />
                        ) : (
                          <>
                            <div className="pn-main">
                              <div className="pn-title">
                                <code>{n.key}</code>
                                {n.title}
                                {n.real ? (
                                  <span className="pn-badge real">真实执行</span>
                                ) : n.assigned ? (
                                  <span className="pn-badge sim">模拟</span>
                                ) : (
                                  <span className="pn-badge none">未指派</span>
                                )}
                              </div>
                              {n.objective && <div className="pn-obj">{n.objective}</div>}
                              <div className="pn-meta">
                                {n.required_caps.map(c => (
                                  <span key={c} className="cap">
                                    {c}
                                  </span>
                                ))}
                                {n.deps.length > 0 && (
                                  <span className="pn-deps">依赖 {n.deps.join(', ')}</span>
                                )}
                                {n.acceptance[0] && (
                                  <span className="pn-acc">验收：{n.acceptance[0]}</span>
                                )}
                              </div>
                            </div>

                            <div className="pn-side">
                              <select
                                value={n.assigned ?? ''}
                                onChange={e =>
                                  void patch(n.key, { assigned: e.target.value || null }).catch(
                                    (x: any) =>
                                      onNotify(
                                        (x?.message ?? '换人失败').replace(/^\d+\s*/, ''),
                                        true,
                                      ),
                                  )
                                }
                              >
                                <option value="">未指派</option>
                                {(data?.agents ?? []).map(a => (
                                  <option key={a.id} value={a.id}>
                                    {a.name}
                                    {a.real ? '' : '（模拟）'}
                                  </option>
                                ))}
                              </select>
                              {n.why && <div className="pn-why">{n.why}</div>}
                              <div className="pn-side-btns">
                                <button className="btn sm" onClick={() => setEditingKey(n.key)}>
                                  编辑
                                </button>
                                <button
                                  className="btn danger sm"
                                  onClick={() =>
                                    void patch(n.key, { remove: true })
                                      .then(() => {
                                        setEditingKey(null)
                                        onNotify(`${n.key} 已删除`)
                                      })
                                      .catch((x: any) =>
                                        onNotify(
                                          (x?.message ?? '删除失败').replace(/^\d+\s*/, ''),
                                          true,
                                        ),
                                      )
                                  }
                                >
                                  删掉
                                </button>
                              </div>
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                )
              })}

              {adding ? (
                <PlanNodeEditor
                  node={null}
                  allKeys={plan.tasks.map(t => t.key)}
                  onSave={async body => {
                    await patch('new', body)
                    setAdding(false)
                    onNotify('已添加节点')
                  }}
                  onCancel={() => setAdding(false)}
                />
              ) : (
                <button className="btn additive" onClick={() => setAdding(true)}>
                  + 加一个节点
                </button>
              )}
            </div>

            <div className="plan-side">
              <div className="panel side-card">
                <h4>拆解理由</h4>
                <p>{plan.rationale || '（模型未给出）'}</p>
              </div>

              <div className="panel side-card">
                <h4>风险</h4>
                {plan.risks.length ? (
                  <ul>
                    {plan.risks.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">（模型未给出）</p>
                )}
              </div>

              <div className="panel side-card">
                <h4>这次拆解的开销</h4>
                <div className="kv">
                  <span>模型</span>
                  <b>{budget?.model ?? '-'}</b>
                </div>
                <div className="kv">
                  <span>本次 tokens</span>
                  <b>
                    {(plan.cost?.prompt_tokens ?? 0) + (plan.cost?.completion_tokens ?? 0)}
                  </b>
                </div>
                <div className="kv">
                  <span>累计</span>
                  <b>
                    {budget?.used_tokens ?? 0} / {budget?.limit_tokens ?? 0}
                  </b>
                </div>
                <div className="bar">
                  <i
                    style={{
                      width: `${Math.min(
                        100,
                        ((budget?.used_tokens ?? 0) / Math.max(1, budget?.limit_tokens ?? 1)) * 100,
                      )}%`,
                    }}
                  />
                </div>
                <div className="kv">
                  <span>估算花费</span>
                  <b>¥{(budget?.est_cost_cny ?? 0).toFixed(4)}</b>
                </div>
                <div className="tiny">
                  {budget?.price_note}
                  {budget?.since && ` · 自 ${String(budget.since).slice(5, 16).replace('T', ' ')} 起累计`}
                </div>
                <button
                  className="btn sm"
                  style={{ marginTop: 6, alignSelf: 'flex-start' }}
                  onClick={async () => {
                    try {
                      await api.resetBudget()
                      await load()
                      onNotify('用量账本已清空')
                    } catch (e: any) {
                      onNotify((e?.message ?? '清空失败').replace(/^\d+\s*/, ''), true)
                    }
                  }}
                >
                  清空账本
                </button>
              </div>
            </div>
          </div>

          <div className="plan-foot">
            <input
              value={note}
              onChange={e => setNote(e.target.value)}
              placeholder="驳回意见（可选），会记进审计事件"
            />
            <button className="btn" disabled={!!busy} onClick={reject}>
              驳回
            </button>
            <button className="btn primary" disabled={!!busy} onClick={approve}>
              {busy === 'approve' ? '正在开工…' : '批准并开工'}
            </button>
          </div>
          <div className="plan-warn">
            批准会<b>替换</b>当前任务图（草案代表一个新项目），并立刻开始按依赖调度
          </div>
        </>
      )}
    </div>
  )
}
