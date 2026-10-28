import type { BillingStatus, Candidate, ConductorCfg, Plan, PlanResp, Snapshot, Task } from './types'

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) {
    const msg = await r.text().catch(() => '')
    throw new Error(`${r.status} ${msg.slice(0, 200)}`)
  }
  return r.json() as Promise<T>
}

export const api = {
  snapshot: () => j<Snapshot>('/api/snapshot'),
  routing: (tid: string) => j<{ candidates: Candidate[] }>(`/api/tasks/${tid}/routing`),
  join: (agentId: string) =>
    j('/api/members', { method: 'POST', body: JSON.stringify({ agent_id: agentId }) }),
  leave: (agentId: string) => j(`/api/members/${agentId}`, { method: 'DELETE' }),
  dispatch: (tid: string, body: { agent_id?: string; force?: boolean } = {}) =>
    j(`/api/tasks/${tid}/dispatch`, { method: 'POST', body: JSON.stringify(body) }),
  cancel: (tid: string) => j(`/api/tasks/${tid}/cancel`, { method: 'POST' }),
  approve: (tid: string) => j(`/api/tasks/${tid}/approve`, { method: 'POST' }),
  demoReset: () => j('/api/demo/reset', { method: 'POST' }),
  setAutoAccept: (on: boolean) =>
    j<{ ok: boolean; auto_accept: boolean }>('/api/workspace/auto-accept', {
      method: 'POST',
      body: JSON.stringify({ on }),
    }),
  resetBudget: () =>
    j<{ ok: boolean; budget: any }>('/api/plan/budget/reset', { method: 'POST' }),
  probeAgent: (id: string) =>
    j<{ ok: boolean; health: string; evidence: string }>(`/api/agents/${id}/probe`, {
      method: 'POST',
    }),
  rejectHandoff: (hid: string, note: string) =>
    j(`/api/handoffs/${hid}/reject`, { method: 'POST', body: JSON.stringify({ note }) }),

  // 主脑计划
  plan: () => j<PlanResp>('/api/plan'),
  planDraft: (goal: string) =>
    j<{ ok: boolean; plan: Plan; budget: any }>('/api/plan/draft', {
      method: 'POST',
      body: JSON.stringify({ goal }),
    }),
  planApprove: () => j<{ ok: boolean; tasks: number }>('/api/plan/approve', { method: 'POST' }),
  planReject: (note: string) =>
    j('/api/plan/reject', { method: 'POST', body: JSON.stringify({ note }) }),
  // 增删改都走这个：{add: {...}} 新增 / {remove: true} 删除 / 其余按字段更新
  planPatch: (key: string, body: Record<string, unknown>) =>
    j<{ ok: boolean; plan: Plan }>(`/api/plan/task/${key}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  // 已开工图里的单个任务：按状态限定能改什么，后端会拒并说明原因
  taskPatch: (tid: string, body: Record<string, unknown>) =>
    j<{ ok: boolean; task: Task }>(`/api/tasks/${tid}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  taskRemove: (tid: string) =>
    j<{ ok: boolean; freed: string[] }>(`/api/tasks/${tid}`, { method: 'DELETE' }),

  // 账户体检：真实余额 + token 账本
  billing: (force = false) => j<BillingStatus>(`/api/billing?force=${force ? 'true' : 'false'}`),

  // 主脑（云端模型）配置：接口只回掩码，明文永不下发
  conductor: () => j<ConductorCfg>('/api/conductor'),
  saveConductor: (body: {
    base_url?: string
    model?: string
    api_key?: string
    granularity?: string
    extra_prompt?: string
  }) =>
    j<{ ok: boolean } & ConductorCfg>('/api/conductor', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  testConductor: () =>
    j<{ ok: boolean; model: string; latency_ms: number; reply: string }>('/api/conductor/test', {
      method: 'POST',
    }),
}
