import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useAgentMesh } from './store'
import AgentStrip from './components/AgentStrip'
import Swimlane from './components/Swimlane'
import Dag from './components/Dag'
import Outline from './components/Outline'
import Inspector from './components/Inspector'
import AgentPool from './components/AgentPool'
import PlanView from './components/PlanView'
import ConductorPanel from './components/ConductorPanel'
import Timeline from './components/Timeline'

type View = 'board' | 'pool' | 'plan'
type TreeMode = 'lane' | 'dag' | 'outline'

type Action = 'dispatch' | 'force' | 'cancel' | 'approve'

export default function App() {
  const { snap, connected, error, reload } = useAgentMesh()
  const [hashView, hashTree, hashTask] = location.hash.replace('#', '').split('/')
  const [cfgOpen, setCfgOpen] = useState(false)
  const [view, setView] = useState<View>(
    hashView === 'pool' || hashView === 'plan' ? (hashView as View) : 'board',
  )
  const [tree, setTree] = useState<TreeMode>(
    hashTree === 'dag' || hashTree === 'outline' ? (hashTree as TreeMode) : 'lane',
  )
  const [sel, setSel] = useState<string | null>(hashTask || null)
  const [toast, setToast] = useState<{ msg: string; err?: boolean } | null>(null)

  const notify = useCallback((msg: string, err = false) => {
    setToast({ msg, err })
    window.setTimeout(() => setToast(null), 3200)
  }, [])

  useEffect(() => {
    if (sel || !snap) return
    const t =
      snap.tasks.find(x => x.status === 'running') ??
      snap.tasks.find(x => x.status === 'reviewing') ??
      snap.tasks[0]
    if (t) setSel(t.id)
  }, [snap, sel])

  const act = async (kind: Action, tid: string) => {
    try {
      if (kind === 'approve') await api.approve(tid)
      if (kind === 'cancel') await api.cancel(tid)
      if (kind === 'dispatch') await api.dispatch(tid)
      if (kind === 'force') {
        const r = await api.routing(tid)
        const best = r.candidates[0]
        if (!best) throw new Error('没有可派活的成员')
        await api.dispatch(tid, { agent_id: best.agent_id, force: true })
        notify(`已强制派给 ${best.name}，该决定已记入审计`)
      }
      await reload()
    } catch (e: any) {
      notify(e?.message?.replace(/^\d+\s*/, '') ?? '操作失败', true)
    }
  }

  const join = async (id: string) => {
    try {
      await api.join(id)
      await reload()
      notify('已邀进协作组')
    } catch (e: any) {
      notify(e?.message ?? '操作失败', true)
    }
  }

  const leave = async (id: string) => {
    try {
      await api.leave(id)
      await reload()
      notify('已移出协作组')
    } catch (e: any) {
      notify(e?.message ?? '操作失败', true)
    }
  }

  if (!snap) {
    return (
      <div className="app">
        <div className="empty" style={{ height: '100vh' }}>
          <b>{error ? '后端未连接' : '正在连接…'}</b>
          <span>{error ?? 'http://127.0.0.1:8787'}</span>
          {error && (
            <button className="btn" onClick={reload}>
              重试
            </button>
          )}
        </div>
      </div>
    )
  }

  const running = snap.tasks.filter(t => t.status === 'running')
  const selectedTask = snap.tasks.find(t => t.id === sel) ?? null

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <div className="mark">
            <i />
          </div>
          AgentMesh
          <small>v0.1</small>
        </div>
        <div className="nav">
          <button className={view === 'board' ? 'on' : ''} onClick={() => setView('board')}>
            工作台
          </button>
          <button className={view === 'plan' ? 'on' : ''} onClick={() => setView('plan')}>
            主脑计划
          </button>
          <button className={view === 'pool' ? 'on' : ''} onClick={() => setView('pool')}>
            Agent 池
          </button>
        </div>
        <div className="top-right">
          <span className={'pill' + (connected ? '' : ' off')}>
            <span className="dot" />
            {connected ? `${running.length} 个 Agent 工作中` : '后端未连接'}
          </span>
          <span className="timer">{snap.workspace.name}</span>
          <label
            className={'switch' + (snap.workspace.auto_accept ? ' on' : '')}
            title="开启后，每个节点跑完自动通过验收，不再逐个等你点（默认关）"
          >
            <input
              type="checkbox"
              checked={!!snap.workspace.auto_accept}
              onChange={async e => {
                const on = e.target.checked
                try {
                  await api.setAutoAccept(on)
                  await reload()
                  notify(on ? '已开启自动验收：节点跑完直接放行' : '已关闭自动验收：仍需你逐个确认')
                } catch (err: any) {
                  notify(err?.message ?? '切换失败', true)
                }
              }}
            />
            <i />
            自动验收
          </label>
          <button className="btn" onClick={() => setCfgOpen(true)} title="改主脑用的云端模型与 API Key">
            主脑配置
          </button>
          <button
            className="btn"
            onClick={async () => {
              try {
                await api.demoReset()
                await reload()
                notify('演示数据已重置')
              } catch (e: any) {
                notify(e?.message ?? '重置失败', true)
              }
            }}
          >
            重置演示
          </button>
          <button className="btn" onClick={reload}>
            刷新
          </button>
        </div>
      </div>

      {view === 'board' && (
        <>
          <AgentStrip agents={snap.agents} tasks={snap.tasks} onSelectTask={setSel} />
          <div className="main">
            <div className="panel canvas-wrap">
              <div className="canvas-head">
                <h3>任务工作树</h3>
                <div className="seg">
                  <button className={tree === 'lane' ? 'on' : ''} onClick={() => setTree('lane')}>
                    Agent 泳道
                  </button>
                  <button className={tree === 'dag' ? 'on' : ''} onClick={() => setTree('dag')}>
                    分层 DAG
                  </button>
                  <button className={tree === 'outline' ? 'on' : ''} onClick={() => setTree('outline')}>
                    大纲列表
                  </button>
                </div>
                <div className="legend">
                  <span>
                    <i className="lg" style={{ background: '#c2410c' }} />
                    跨泳道 = 交接
                  </span>
                  <span>
                    <i className="lg" style={{ background: '#cbd2db' }} />
                    同泳道 = 串行
                  </span>
                </div>
              </div>
              {tree === 'lane' && (
                <Swimlane
                  agents={snap.agents}
                  tasks={snap.tasks}
                  edges={snap.edges}
                  selected={sel}
                  onSelect={setSel}
                />
              )}
              {tree === 'dag' && (
                <Dag
                  agents={snap.agents}
                  tasks={snap.tasks}
                  edges={snap.edges}
                  selected={sel}
                  onSelect={setSel}
                />
              )}
              {tree === 'outline' && (
                <Outline
                  agents={snap.agents}
                  tasks={snap.tasks}
                  edges={snap.edges}
                  selected={sel}
                  onSelect={setSel}
                />
              )}
            </div>
            <Inspector
              task={selectedTask}
              agents={snap.agents}
              allTasks={snap.tasks}
              edges={snap.edges}
              onAction={act}
              onNotify={notify}
              onChanged={reload}
            />
          </div>
          <Timeline events={snap.events} />
        </>
      )}

      {view === 'pool' && (
        <AgentPool
          agents={snap.agents}
          onJoin={join}
          onLeave={leave}
          onRescan={reload}
          onProbe={async id => {
            try {
              const r = await api.probeAgent(id)
              await reload()
              notify(r.health === 'online' ? `探测成功：${r.evidence}` : `仍不可用：${r.evidence}`, r.health !== 'online')
            } catch (e: any) {
              notify((e?.message ?? '探测失败').replace(/^\d+\s*/, ''), true)
            }
          }}
        />
      )}

      {view === 'plan' && <PlanView onNotify={notify} onChanged={reload} />}

      {cfgOpen && <ConductorPanel onClose={() => setCfgOpen(false)} onNotify={notify} />}

      {toast && <div className={'toast' + (toast.err ? ' err' : '')}>{toast.msg}</div>}
    </div>
  )
}
