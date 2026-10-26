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
