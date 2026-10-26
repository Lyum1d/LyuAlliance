import type { Ev } from '../types'
import { clockOf } from '../lib'

const KEEP = new Set([
  'task.done',
  'task.review',
  'task.failed',
  'task.dispatched',
  'task.cancelled',
  'member.joined',
  'member.removed',
  'workspace.created',
])

function label(e: Ev): { who: string; what: string } {
  const p = e.payload || {}
  switch (e.kind) {
    case 'task.done':
      return {
        who: p.by === 'user' ? '你' : p.by === 'auto' ? '自动' : (e.actor ?? '').replace('agent:', ''),
        what: p.by === 'auto' ? `自动通过「${p.title}」` : `验收「${p.title}」`,
      }
    case 'task.review':
      return { who: p.agent ?? 'Agent', what: `完成「${p.title}」，待你验收` }
    case 'task.failed':
      return { who: '系统', what: `「${p.title}」执行失败` }
    case 'task.dispatched':
      return { who: p.agent ?? '调度', what: `接单「${p.title}」` }
    case 'task.cancelled':
      return { who: '你', what: `中止「${p.title}」` }
    case 'member.joined':
      return { who: p.name ?? '', what: '被邀进协作组' }
    case 'member.removed':
      return { who: p.name ?? '', what: '被移出协作组' }
    case 'workspace.created':
      return { who: '系统', what: p.name ?? '重置了协作组' }
    default:
      return { who: e.actor, what: e.kind }
  }
}

export default function Timeline({ events }: { events: Ev[] }) {
  const list = [...events].filter(e => KEEP.has(e.kind)).slice(-4).reverse()
  return (
    <div className="panel timeline">
      <span className="tl-label">事件流</span>
      {list.map(e => {
        const { who, what } = label(e)
        return (
          <span className="tl-item" key={e.seq}>
            <span className="tl-time">{clockOf(e.ts)}</span>
            <b>{who}</b>
            {what}
          </span>
        )
      })}
      {list.length === 0 && <span className="tl-item">暂无事件</span>}
    </div>
  )
}
