import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Ev, Snapshot, Task } from './types'

function patchTask(t: Task, ev: Ev): Task {
  const p = ev.payload
  switch (ev.kind) {
    case 'task.progress':
      return t.id === p.task_id ? { ...t, progress: p.progress } : t
    case 'task.token':
      return t.id === p.task_id
        ? {
            ...t,
            // 豆包走快照（整体替换），Ollama 走增量（追加）
            output: p.snapshot
              ? String(p.delta ?? '').slice(-20000)
              : ((t.output ?? '') + (p.delta ?? '')).slice(-20000),
          }
        : t
    case 'task.dispatched':
      return t.id === p.task_id
        ? { ...t, status: 'running', assignee: p.agent_id, progress: 0, reason: null, output: '' }
        : t
    case 'task.review':
      return t.id === p.task_id ? { ...t, status: 'reviewing', progress: 100 } : t
    case 'task.done':
      return t.id === p.task_id ? { ...t, status: 'done', progress: 100 } : t
    case 'task.failed':
      return t.id === p.task_id ? { ...t, status: 'failed', reason: p.error ?? null } : t
    case 'task.cancelled':
      return t.id === p.task_id ? { ...t, status: 'pending', progress: 0 } : t
    case 'task.idle':
      // 派活时发现执行体不可用：任务退回待办，把原因挂上，别让它看着像在跑
      return t.id === p.task_id
        ? { ...t, status: 'pending', progress: 0, reason: p.reason ?? null }
        : t
    default:
      return t
  }
}

function reduce(snap: Snapshot, ev: Ev): Snapshot {
  let agents = snap.agents
  if (ev.kind === 'member.joined' || ev.kind === 'member.removed') {
    const on = ev.kind === 'member.joined'
    agents = snap.agents.map(a => (a.id === ev.payload.agent_id ? { ...a, in_team: on } : a))
  }
  // 后台心跳 / 手动探测的结果：让池子里的健康态自己跟着变，不用手动刷新
  if (ev.kind === 'agent.health' || ev.kind === 'agent.probed') {
    agents = agents.map(a => a.id === ev.payload.agent_id
      ? { ...a, health: ev.payload.health, evidence: ev.payload.evidence }
      : a)
  }
  // 流式 token 不落库，只更新任务内容，不进事件流
  const ephemeral = ev.persist === false
  return {
    ...snap,
    agents,
    tasks: snap.tasks.map(t => patchTask(t, ev)),
    events: ephemeral ? snap.events : [...snap.events, ev].slice(-120),
    seq: ephemeral ? snap.seq : Math.max(snap.seq, ev.seq),
  }
}

export function useAgentMesh() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)

  const load = useCallback(async () => {
    try {
      const s = await api.snapshot()
      setSnap(s)
      setError(null)
    } catch (e: any) {
      setError(e?.message ?? '连接后端失败')
    }
  }, [])

  useEffect(() => {
    load()
    let stop = false

    const connect = () => {
      if (stop) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        retryRef.current = 0
        setConnected(true)
      }
      ws.onmessage = m => {
        try {
          const ev = JSON.parse(m.data)
          if (ev.type === 'hello') return
          if (ev.type === 'event') setSnap(s => (s ? reduce(s, ev) : s))
        } catch {
          /* ignore */
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (stop) return
        retryRef.current += 1
        const delay = Math.min(5000, 400 * retryRef.current)
        setTimeout(connect, delay)
      }
      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      stop = true
      wsRef.current?.close()
    }
  }, [load])

  return { snap, connected, error, reload: load }
}
