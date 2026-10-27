import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ConductorCfg } from '../types'

type Props = {
  onClose: () => void
  onNotify?: (msg: string, bad?: boolean) => void
}

/**
 * 主脑配置抽屉。
 *
 * 设计上有一条硬约束：**这个界面永远拿不到 key 明文**。
 * 后端 `GET /api/conductor` 只返回掩码，改 key 只能整体覆盖写入，
 * 所以这里不提供"显示原文"，也不做任何本地缓存。
 */
export default function ConductorPanel({ onClose, onNotify }: Props) {
  const [cfg, setCfg] = useState<ConductorCfg | null>(null)
  const [base, setBase] = useState('')
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const [gran, setGran] = useState<'coarse' | 'medium' | 'fine'>('medium')
  const [extra, setExtra] = useState('')
  const [busy, setBusy] = useState<'' | 'save' | 'test' | 'clear' | 'reset'>('')
  const [msg, setMsg] = useState<{ text: string; bad: boolean } | null>(null)

  const load = async () => {
    try {
      const c = await api.conductor()
      setCfg(c)
      setBase(c.base_url)
      setModel(c.model)
      setKey('')
      setGran(c.granularity)
      setExtra(c.extra_prompt)
    } catch (e: any) {
      setMsg({ text: e?.message ?? '读取配置失败', bad: true })
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const connDirty = !!cfg && (base !== cfg.base_url || model !== cfg.model || key.trim() !== '')
  const styleDirty = !!cfg && (gran !== cfg.granularity || extra !== cfg.extra_prompt)
  const dirty = connDirty || styleDirty

  const save = async () => {
    if (!cfg) return
    const body: {
      base_url?: string
      model?: string
      api_key?: string
      granularity?: string
      extra_prompt?: string
    } = {}
    if (base !== cfg.base_url) body.base_url = base
    if (model !== cfg.model) body.model = model
    if (key.trim()) body.api_key = key.trim()
    if (gran !== cfg.granularity) body.granularity = gran
    if (extra !== cfg.extra_prompt) body.extra_prompt = extra
    if (!Object.keys(body).length) {
      setMsg({ text: '没有改动', bad: false })
      return
    }
    setBusy('save')
    setMsg(null)
    try {
      await api.saveConductor(body)
      await load()
      setMsg({ text: '已保存。下一次拆解就用新配置，不用重启后端。', bad: false })
      onNotify?.('主脑配置已更新')
    } catch (e: any) {
      setMsg({ text: e?.message ?? '保存失败', bad: true })
    } finally {
      setBusy('')
    }
  }

  const test = async () => {
    setBusy('test')
    setMsg(null)
    try {
      const r = await api.testConductor()
      setMsg({ text: `连接正常 · ${r.model} · ${r.latency_ms}ms`, bad: false })
    } catch (e: any) {
      setMsg({ text: e?.message ?? '测试失败', bad: true })
    } finally {
      setBusy('')
    }
  }

  const clearKey = async () => {
    setBusy('clear')
    setMsg(null)
    try {
      await api.saveConductor({ api_key: '' })
      await load()
      setMsg({ text: '已清除界面里保存的 key，回退到 .env 里的配置', bad: false })
      onNotify?.('已清除主脑 key')
    } catch (e: any) {
      setMsg({ text: e?.message ?? '清除失败', bad: true })
    } finally {
      setBusy('')
    }
  }

  const resetStyle = async () => {
    setBusy('reset')
    setMsg(null)
    try {
      await api.saveConductor({ granularity: '', extra_prompt: '' })
      await load()
      setMsg({ text: '已恢复默认拆解风格（中粒度、无额外要求）', bad: false })
      onNotify?.('主脑拆解风格已恢复默认')
    } catch (e: any) {
      setMsg({ text: e?.message ?? '恢复失败', bad: true })
    } finally {
      setBusy('')
    }
  }

  const srcLabel = (s?: string) =>
    s === 'db' ? '界面保存' : s === 'env' || s === '.env' ? '来自 .env' : '未配置'

  return (
    <>
      <div className="drawer-mask" onClick={onClose} />
      <aside className="drawer">
        <header>
          <div>
            <h3>主脑配置</h3>
            <p>决定「主脑」拆解计划时用哪个云端模型。保存即生效。</p>
          </div>
          <button className="btn sm" onClick={onClose}>
            关闭
          </button>
        </header>

        <div className="drawer-body">
          <label className="fld">
            <span>API Base</span>
            <input
              value={base}
              onChange={e => setBase(e.target.value)}
              placeholder="https://api.deepseek.com"
              spellCheck={false}
            />
            <em>{srcLabel(cfg?.base_source)}</em>
          </label>

          <label className="fld">
            <span>模型</span>
            <input
              value={model}
              onChange={e => setModel(e.target.value)}
              placeholder="deepseek-flash"
              spellCheck={false}
              list="conductor-models"
            />
            <datalist id="conductor-models">
              <option value="deepseek-flash" />
              <option value="deepseek-chat" />
              <option value="deepseek-reasoner" />
            </datalist>
            <em>{srcLabel(cfg?.model_source)}</em>
          </label>

          <label className="fld">
            <span>API Key</span>
            <input
              type="password"
              value={key}
              onChange={e => setKey(e.target.value)}
              placeholder={
                cfg?.key_set ? `已配置 ${cfg.key_masked}，留空表示不修改` : '尚未配置，粘贴一个 key'
              }
              autoComplete="new-password"
              spellCheck={false}
            />
            <em>
              {srcLabel(cfg?.key_source)} ·{' '}
              {cfg?.encryption === 'dpapi'
                ? 'DPAPI 加密入库，只有本机当前 Windows 账号能解'
                : '明文存储（当前平台不支持 DPAPI）'}
            </em>
          </label>

          <div className="fld">
            <span>拆解粒度</span>
            <div className="seg">
              {(['coarse', 'medium', 'fine'] as const).map(g => (
                <button
                  key={g}
                  type="button"
                  className={'seg-btn' + (gran === g ? ' on' : '')}
                  onClick={() => setGran(g)}
                >
                  {g === 'coarse' ? '粗' : g === 'medium' ? '中' : '细'}
                </button>
              ))}
            </div>
            <em>
              {gran === 'coarse'
                ? '3~5 个节点，只抓主干'
                : gran === 'medium'
                  ? '5~12 个节点，与原行为一致'
                  : '8~14 个节点，每个可验收动作单独成节点'}
              {' · '}
              {cfg?.granularity_source === 'db' ? '界面保存' : '默认'}
            </em>
          </div>

          <label className="fld">
            <span>额外要求（可选）</span>
            <textarea
              value={extra}
              onChange={e => setExtra(e.target.value)}
              rows={4}
              maxLength={800}
              placeholder="追加到主脑提示词末尾。例如：每个节点都写一条回滚方案；优先复用已有组件；别拆前端 UI 细节"
            />
            <em>
              只影响拆解风格，不改变输出格式；留空即不追加。{extra.length}/800
            </em>
          </label>

          <div className="drawer-tip">
            key 保存后不会再回传到浏览器：面板里显示的永远是掩码，接口也只返回掩码。 想换 key
            就贴新的，想去掉界面里存的就点「清除 key」（会回退到 .env）。
          </div>

          {msg && <div className={'drawer-msg' + (msg.bad ? ' bad' : '')}>{msg.text}</div>}
        </div>

        <footer>
          <button
            className="btn"
            onClick={resetStyle}
            disabled={busy !== '' || (!styleDirty && cfg?.granularity_source !== 'db' && !cfg?.extra_prompt)}
            title="清空粒度与额外要求，回到改造前的拆解风格"
          >
            恢复默认
          </button>
          <button
            className="btn"
            onClick={test}
            disabled={busy !== '' || connDirty || !cfg?.key_set}
            title={
              connDirty
                ? '有未保存的改动，先保存再测试'
                : cfg?.key_set
                  ? '用当前已保存的配置发一个 4 token 的最小请求'
                  : '还没配置 key'
            }
          >
            {busy === 'test' ? '测试中…' : '测试连接'}
          </button>
          <button
            className="btn danger"
            onClick={clearKey}
            disabled={busy !== '' || cfg?.key_source !== 'db'}
            title={cfg?.key_source === 'db' ? '删除界面里保存的 key' : '界面里没有保存过 key'}
          >
            {busy === 'clear' ? '清除中…' : '清除 key'}
          </button>
          <div className="spacer" />
          <button className="btn primary" onClick={save} disabled={busy !== '' || !dirty}>
            {busy === 'save' ? '保存中…' : '保存'}
          </button>
        </footer>
      </aside>
    </>
  )
}
