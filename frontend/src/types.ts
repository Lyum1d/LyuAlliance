export type TaskStatus =
  | 'pending'
  | 'running'
  | 'reviewing'
  | 'done'
  | 'blocked'
  | 'failed'
  | 'cancelled'

export interface Agent {
  id: string
  name: string
  kind: 'native' | 'bridge' | 'conductor' | 'human'
  role: string
  model: string
  caps: Record<string, number>
  lane: number
  health: 'online' | 'degraded' | 'offline' | 'unavailable'
  in_team: boolean
  cost: 'free' | 'paid'
  evidence: string
  sort: number
  adapter?: 'ollama' | 'doubao' | 'codex' | 'workbuddy' | 'wb-cdp' | 'none'
}

export interface Task {
  id: string
  title: string
  objective: string
  status: TaskStatus
  assignee: string | null
  stage: number
  progress: number
  priority: number
  required_caps: string[]
  acceptance: string[]
  reason: string | null
  duration_ms: number
  started_at: string | null
  ended_at: string | null
  output: string | null
  context?: Packet | null
}

// 交接上下文包（后端 handoff.build_packet 产出）
export interface Packet {
  version?: number
  handoff_id?: string
  goal?: string
  task?: { id: string; title: string; objective: string; acceptance: string[] }
  upstream?: {
    task_id: string
    title: string
    agent: string | null
    chars: number
    excerpt: string
    real: boolean
  }[]
  decisions?: { text: string; source: string; auto: boolean }[]
  failures?: { ts: string; reason: string }[]
}

export interface Ev {
  seq: number
  ts: string
  kind: string
  actor: string
  payload: Record<string, any>
  persist?: boolean
}

export interface Snapshot {
  workspace: {
    id: string
    name: string
    goal: string
    concurrency: number
    mode: string
    auto_accept?: number
  }
  agents: Agent[]
  tasks: Task[]
  edges: [string, string][]
  events: Ev[]
  seq: number
}

export interface Candidate {
  agent_id: string
  name: string
  score: number
  cap_match: number
  health: string
  cost: string
  kind: string
}

export const STAGES = ['澄清', '设计', '实现', '验证', '收尾']

export const STATUS_TEXT: Record<TaskStatus, string> = {
  pending: '待开始',
  running: '运行中',
  reviewing: '待验收',
  done: '完成',
  blocked: '阻塞',
  failed: '失败',
  cancelled: '已取消',
}

// ---------------------------------------------------------------- 主脑计划

export interface PlanNode {
  key: string
  title: string
  objective: string
  stage: number
  required_caps: string[]
  acceptance: string[]
  deps: string[]
  assigned: string | null
  assigned_name: string | null
  why: string
  real?: boolean
  candidates?: Candidate[]
}

export interface PlanBudget {
  model: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  used_tokens: number
  limit_tokens: number
  est_cost_cny: number
  since?: string | null
  price_note: string
}

export interface Plan {
  goal: string
  goal_restated: string
  tasks: PlanNode[]
  rationale: string
  risks: string[]
  estimate: Record<string, any>
  cost?: {
    model: string
    prompt_tokens: number | null
    completion_tokens: number | null
    budget: PlanBudget
  }
  status?: string
  note?: string
  created_at?: string
}

export interface PlanAgent {
  id: string
  name: string
  caps: Record<string, number>
  real: boolean
  health: string
}

export interface PlanResp {
  plan: Plan | null
  last: Plan | null
  budget: PlanBudget
  agents: PlanAgent[]
}

export interface ConductorCfg {
  base_url: string
  model: string
  /** 永远只有掩码，接口不下发明文 */
  key_masked: string
  key_set: boolean
  base_source: 'db' | '.env'
  model_source: 'db' | '.env'
  key_source: 'db' | 'env' | 'none'
  encryption: 'dpapi' | 'plain'
  budget_tokens: number
  /** 拆解粒度 */
  granularity: 'coarse' | 'medium' | 'fine'
  granularity_label: string
  granularity_hint: string
  granularity_source: 'db' | '默认'
  /** 追加到主脑提示词末尾的自定义要求 */
  extra_prompt: string
}

/** 云端账户体检（真实余额 + 本地 token 账本） */
export interface BillingStatus {
  level: 'ok' | 'warn' | 'critical' | 'unknown'
  message: string
  balance: number | null
  currency: string | null
  available: boolean | null
  balance_known: boolean
  warn_below: number
  stop_below: number
  /** 按最近消耗估算还能拆几次 */
  est_runs: number | null
  per_run_cost: number
  budget_used: number
  budget_limit: number
  budget_pct: number
  model: string
  checked_at: string
}
