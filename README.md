# AgentMesh

多 Agent 协同工作台：多个异构 Agent 在同一张任务图上接力干活，全过程 Web 可视。

---

## 快速启动

双击 `dev.bat`，会自动拉起后端（:8787）+ 前端（:5173）并打开浏览器。

手动启动：

```bash
# 后端
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787

# 前端
cd frontend
npm run dev
```

- 前端 <http://localhost:5173>
- 后端健康检查 <http://127.0.0.1:8787/api/health>

> 注意：前端必须用 `localhost` 访问，`127.0.0.1` 可能连不上（Vite 默认绑 `::1`）。

---

## 当前进度

| 里程碑 | 状态 |
|---|---|
| M0 骨架（SQLite / 事件总线 / WS / REST） | ✅ 完成 |
| M1 界面（三视图 + 实时快照 + 邀进移出） | ✅ 完成 |
| M2 能力路由打分 + 可解释明细 | ✅ 完成（简化版） |
| **M3-a 真实接入：Ollama** | ✅ **完成**（qwen3.5:9b 真调用、真流式、产出落库） |
| **M3-b Codex CLI** | 🟡 **已接入，未启用**（adapter 写完 + 零成本探测；本机无额度、未登录，默认拒绝调用） |
| **M3-c 真实接入：豆包「工作任务」** | ✅ **完成**（CDP 驱动，真建任务、真执行、真回读） |
| **M3-d WorkBuddy 桌面端** | ✅ **完成**（CDP 定位 + OS 级 SendInput 输入，真派活、真回读；CLI 路线因凭据存不住、API Key 拿不到而弃用） |
| **M4 主脑 Conductor** | ✅ **完成**（真实拆解 → 逐节点可改 → 批准后替换任务图并开工） |
| M4.1 自动验收开关 + 用量账本 | ✅ 完成 |
| **M5 交接 ContextPacket** | ✅ **完成**（自动打包上游成果/决策/失败教训，注入下游 prompt，可退回） |

### 豆包不是只有对话框——「工作任务」才是它的执行体

之前我把豆包当成纯聊天，判断错了。它的侧边栏有 **`新工作任务 Ctrl N`**，点进去是一个完整的 Agent 工作区：

- 底部工具栏：**`本地电脑` / `项目` / `全部允许` / `技能` / `插件`** —— 意味着它能读写本机文件、调技能与插件
- 右侧面板：**`对话摘要` / `对话产物` / `最近文件`** —— 它交付的是文件，不只是文字
- 中间对话区：任务目标与执行过程

**CDP 实测链路（已封装进 `backend/app/doubao.py`）**：

| 步骤 | 做法 | 实测结果 |
|---|---|---|
| 建工作任务 | 点 `[data-testid="create_office_task_button"] [role="button"]` | URL 从 `doubao-chat/chat/<id>` 变为 `doubao-chat/chat` |
| 送任务 | 焦点 `.tiptap.ProseMirror` + CDP `Input.insertText` | 文本正确写入 |
| 发送 | 找 aria-label 含「发送」的按钮，兜底 Enter | 成功触发 |
| **判定完成** | 指令里要求它在末行输出哨兵 `⟦END⟧`，见到即完成 | **可靠**（见下方"三个坑"第 2 条） |
| **取回结果** | `[data-testid="message_content"]` 行切片：标记行之后的全部消息行 | 取到干净正文，无界面噪声 |
| 取回文件产物 | `[data-testid="right-panel-workbench-layer"]` | docx/xlsx 类产物在此 |

**三个坑（都踩过，都已修）**：

1. **不能用「输入框的祖先面板」回读**。那会把侧栏模式菜单（对话 / 图像生成 / 帮我写作 / 录音转写…）一起抓进来，正文被埋在中间。必须按 `message_content` 消息行切片。
2. **`office-loading-skeleton-docx` 不能当「生成中」信号**。纯文本任务里这个骨架永远不消失，会把每一轮都判成生成中——实测导致一次 180 秒空等到超时。同理「停止」按钮在生成期间也扫不到（既无 aria-label 也无 innerText 命中）。最终方案：**让豆包在回复末行输出哨兵 `⟦END⟧`**，见到即完成，残留 UI 状态一概不管。
3. **回读要按「快照」不能按「增量」**。豆包生成初期会先塞一个「正在思考」占位符，随后被正文覆盖，开头几个字会被顶掉。用长度增量拼接会把这个占位符永久留在结果里。所以豆包侧每轮回传全量正文，前端整体替换（Ollama 仍走增量追加）。

**实测耗时**：轻量问答题约 **12 秒**；带工具调用的任务（如让它查项目结构）**分钟级**，且会逐步输出执行轨迹。超时默认 300 秒。

**安全提醒（重要）**：任务栏那个 **`全部允许`** 是"允许操作本地电脑"的总开关。实测它真的会执行 `Get-ChildItem`、`node -v`、`npm install` 并在自己的 `Doubao\chats\<日期>\<会话>` 目录里建项目。因此引擎里加了一道硬闸门：

- 任务 `required_caps` 命中 `code_write / shell / deploy` → **直接拒绝派给豆包**，哪怕被强制派活
- 建议把豆包的本地权限调成「每次询问」，而不是「全部允许」

### 真实接入状态（重要）

| Agent | 执行方式 | 界面上怎么标 |
|---|---|---|
| qwen3.5:9b | **真调用 Ollama**，流式回传，产出写回 `task.output` | 卡片标「流式生成」，产出区标「来源：qwen3.5:9b 真实生成」 |
| 豆包 | **真驱动桌面端「工作任务」**（CDP 建任务→注入→发送→回读） | 卡片标「桥接执行中」（橙色），产出区标「来源：豆包工作任务（CDP 桥接）真实生成」 |
| deepseek-r1:7b | 同 qwen（adapter 已配，未在组内） | 同上 |
| Codex CLI | **已接入但未启用**：`codex exec --json` 适配器写完，`CODEX_ENABLED=false` 时直接拒绝，不起子进程、不发请求 | 池里显示「未启用」，按钮置灰，不可邀进 |
| WorkBuddy 桌面端 | **真驱动桌面端**（CDP 定位 + 系统级键鼠注入 + 消息区回读） | 卡片标「桥接执行中」，产出区标「来源：WorkBuddy 桌面端（CDP 桥接）真实生成」 |
| DSH / 主脑 | **模拟器推进，不产生文本** | 卡片标「模拟推进」（琥珀色），产出区明确说明 |

这个区分是刻意做的：**不能让模拟的进度看起来像真的**。

### WorkBuddy 桌面端：CDP 只能看，输入得靠系统级事件

WorkBuddy 是最难接的一个，卡点不在选择器，而在**它的输入框对外部注入有很强的抵抗**。

先走的是 CLI 路线（`codebuddy -p`），死在认证：本机存不住 OAuth 凭据
（`[FileAuthenticationStorage][AtRestEncryption] unavailable adapter=credential-fields … category=missing-key`），
API Key 也拿不到，只能改驱动桌面端界面。

CDP 连上之后发现 `Input` 域被这个构建禁了：

| 尝试 | 结果 |
|---|---|
| `Input.insertText` / `Input.dispatchKeyEvent` | **静默无效**，窗口提到前台（`SetForegroundWindow`）也一样 |
| `Emulation.setFocusEmulationEnabled` + `Page.bringToFront` | 无效 |
| 页面内 `document.execCommand('insertText')` | DOM 内容变了，但 React state 不动，发送按钮仍 `disabled` |
| 从 `__reactFiber$` 取出 Slate editor 实例改 `editor.children` | 内部值变了，WorkBuddy 判「有没有内容」的是另一层 state，按钮依旧禁 |
| 直调 `.cr-send-button` 的 React `onClick` | 有内容守卫，直接返回不发 |

对照组说明这不是 CDP 的锅：**豆包桥接用的正是 `Input.insertText`，一直正常**。

最终方案是降到 OS 级（`backend/app/wb_input.py`，纯 `ctypes`，无第三方依赖）：

- `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` —— 不然坐标被虚拟化
- 点击：`SetCursorPos` + `SendInput` 鼠标事件，坐标 `(screenX + rect.x) * devicePixelRatio`
- 键盘：`keybd_event` 发真实 VK 码（**`KEYEVENTF_UNICODE` 实测无效**，Chromium 不吃合成 WM_CHAR）
- 中文/换行：写系统剪贴板 + `Ctrl+V`，这是唯一可靠的路径
- 清空：`Ctrl+A` + `Delete`

回读侧有两个坑，都踩过：

1. 最早让模型在末尾输出哨兵 `⟦END⟧` 判定完成 —— 但**哨兵字符串必须写在 prompt 里教它**，
   于是「用户消息」里也有 `⟦END⟧`，回读时误判成已完成、把我们自己的 prompt 当答案返回。
2. 改用 UI 的生成状态 `--sending` 判定 —— 生成中按钮会短暂闪烁，抓到中间态「深度思考中」。

现方案：prompt 末尾放一个独有的分隔符 `⟪PROMPT-END⟫`（必须是最后一个字符序列），
回答区 = 分隔符之后的文本；完成判定看 UI 打出的「已完成 Ns」；
`_clean_answer()` 负责剥掉时间戳 / `WorkBuddy` / `已处理 Ns` / `准备中` 等状态行和尾部用量统计。

会话隔离：WorkBuddy 没有豆包那样的独立「工作任务」页，驱动它 = 操作你正在用的窗口。
所以每次派活前先点「新建任务」开一个干净会话；若当前已是空会话则跳过
（再点一次会触发重渲染，把刚注入的文本冲掉）。

还有一个几何上的死结：**驱动 WorkBuddy 的 agent 本身就跑在 WorkBuddy 里**，
它执行工具调用期间窗口恒为 `sending`，所以 `fresh=True`（新建会话）时**绝不能**做空闲等待，
否则永远等不到；只有 `fresh=False`（往当前对话插话）才需要等。

### 真跑的三个坑（都已处理）

1. **qwen3.5:9b 默认开 thinking**。不关掉的话 token 预算全烧在 `thinking` 字段，`content` 永远是空的，任务会"完成但没有输出"。适配器里固定 `think: false`。
2. **首 token 延迟约 9 秒**（模型冷启动 + 预填充）。UI 上必须给"等待首个 token"的提示，否则用户以为卡死。
3. **流式 token 不能落库**。一次生成几百个 chunk，逐条写 `event` 表会把库撑爆。所以事件分两类：`persist=true` 落库可回放，`persist=false` 只推前端。

### 验收闸门

真跑完成的任务进入 **`reviewing`（待验收）**，不会自动变 done——必须你点「通过验收」。
模拟任务仍然自动完成（它们是演示占位，没有真实产出可验收）。

---

## 架构

```
浏览器 ──REST 快照──┐
        ──WS 增量───┤
                    ▼
              FastAPI :8787（单 worker）
                    │
        ┌───────────┼────────────┬──────────────┐
        ▼           ▼            ▼              ▼
    EventBus    Scheduler    Service 层    Adapter(待接)
   Queue+seq   能力路由打分   状态机+审计    Ollama/Codex/CDP
        │           │            │
        └───────────┴──────┬─────┘
                           ▼
                    SQLite (WAL)
              tasks / edges / agents / events
```

三条约束：

1. **单一事实源** —— 状态只在 SQLite，前端只持快照 + 增量。
2. **唯一写入口** —— 只有 service/sim 改状态，每次改动写一条 event。
3. **可回放** —— `events` 表 append-only，`seq` 单调，支持断线续传。

---

## 界面

三个工作树视图（顶栏切换，默认泳道）：

- **Agent 泳道** —— 纵轴 Agent、横轴阶段（澄清/设计/实现/验证/收尾）。跨泳道的橙色虚线就是**交接**，末行「未指派」收纳能力不足的任务。
- **分层 DAG** —— 纵向分层看依赖拓扑。
- **大纲列表** —— 表格 + 分组折叠，节点多时用这个。

右侧详情含「为什么选它」的路由打分明细。

### 路由打分

```
score = 0.50·能力匹配 + 0.20·健康度 + 0.15·(1-负载) + 0.10·(1-成本) + 0.05·近期
硬过滤：cap_match < 0.35 直接排除，宁可 blocked 也不凑合派
```

`单元测试` 任务就是这么卡住的：要求 `code_write`，组内没人达标（Codex 未邀进），于是停在「阻塞 · 未指派」。这是设计行为，不是 bug。

---

## 目录

```
agentmesh/
├─ backend/
│  ├─ app/
│  │  ├─ store.py       # SQLite schema / 种子 / 查询
│  │  ├─ bus.py         # 事件总线（asyncio.Queue + seq）
│  │  ├─ sim.py         # 演示模拟器 + 能力路由
│  │  ├─ adapters.py    # Ollama 适配器
│  │  ├─ llm.py         # 云端模型客户端（token 硬上限）
│  │  ├─ planner.py     # 主脑拆解 / 规范化 / 找环 / 批准
│  │  ├─ handoff.py     # 交接 ContextPacket
│  │  ├─ doubao.py      # 豆包 CDP 桥接
│  │  ├─ wb_bridge.py   # WorkBuddy CDP 桥接（定位 + 组装 + 回读）
│  │  ├─ wb_input.py    # OS 级键鼠注入（ctypes SendInput，无第三方依赖）
│  │  ├─ probe.py       # 统一能力探测（手动 / 心跳 / 派活兜底共用）
│  │  ├─ bridge_watch.py# 桥接成员心跳（快档 15s / 慢档 120s）
│  │  └─ main.py        # FastAPI 路由 + WebSocket
│  ├─ data/agentmesh.db
│  └─ requirements.txt
├─ frontend/
│  └─ src/
│     ├─ App.tsx
│     ├─ store.ts       # 快照 + WS 增量归并
│     ├─ lib.ts         # 拓扑分层 / 颜色 / 时长
│     └─ components/    # Swimlane Dag Outline Inspector AgentPool AgentStrip Timeline
├─ mock/                # 设计原型（3 个工作树方案）
├─ spike/               # 豆包 CDP 桥接验证脚本
└─ dev.bat
```

---

## 接口

```
GET    /api/health
GET    /api/snapshot                     # 全量快照 + 最近 40 条事件
GET    /api/agents
GET    /api/tasks/{id}/routing           # 路由候选与分数
POST   /api/members                      # 邀进 {agent_id}
DELETE /api/members/{agent_id}           # 移出
POST   /api/tasks/{id}/dispatch          # 派活 {agent_id?, force?}
POST   /api/tasks/{id}/cancel
POST   /api/tasks/{id}/approve
POST   /api/demo/reset                   # 重置演示数据
WS     /ws                               # 事件流
```

事件格式：

```jsonc
{ "v":1, "type":"event", "seq":1024, "ts":"...", "kind":"task.progress",
  "actor":"agent:a_qwen", "payload":{ "task_id":"t5", "progress":71 } }
```

---

## 主脑 Conductor（M4）

一句话目标 → 拆成任务 DAG 草案 → 你在「主脑计划」页逐节点过目/改派/删减 → 批准后才开工。

**它的权限边界（故意做窄）**

- 只规划，不执行：没有 `run_shell`、没有 `write_file`，输出只有结构化任务图
- 派活对象只能从"当前协作组里真正可派"的成员里挑，模型编造的 id 会被丢弃
- 拆出来的草案**不会自动开工**，`POST /api/plan/approve` 才会落成真实任务
- 批准是**替换**语义：草案代表一个新项目，会清掉当前任务图

**提示词与校验双保险**

模型输出会经过 `_normalize()` 再落库：剔除非法节点、修正指向不存在节点的依赖、**DFS 查环并断环**、给每个节点补真实的 `route_scores` 候选（而不是信模型自己说的"为什么派给它"）。

**实测**：一次拆解 8 个节点，token 2929，估算 ¥0.0037（deepseek-flash）。产出的依赖顺序合理，风险条目具体到"重排序接口要一次性提交完整 id 顺序并在单事务内更新"这种粒度。

### 三个坑

1. **`planning` / `decide` 是保留能力**。这两个只属于主脑与人类，一旦被写进节点的 `required_caps`，路由永远匹配不到执行者，任务会永久卡在 `pending`。现在提示词里不给这两个词，`_normalize` 里也会过滤掉。
2. **已指派的节点不该再被能力下限否决**。调度器原本要求 `cap_match ≥ 0.35` 才派活，但主脑已经指派、用户也批准了，再挡一次就变成"批准了也永远不动"。现在：计划节点按 `assignee` 直接派（`task.dispatched` 事件带 `pinned: true`），只有未指派节点才走打分。
3. **泳道图的阶段列有 off-by-one**。`stage` 是 1 起始、列号是 0 起始，之前直接拿 `stage` 当列索引，导致整张图右移一列、`stage=5` 掉出网格（实测丢了两个节点）。已在 `byCell` 里统一减 1 并夹紧范围。

## 主脑配置（改模型 / 换 key，界面上完成）

顶栏右侧「主脑配置」打开右侧抽屉，可以改 `API Base` / `模型` / `API Key`，**保存即生效**，
不用改 `.env` 也不用重启后端（`CloudLLM` 每次实例化都重新读配置）。

### key 的处理方式

这里有一条硬约束：**前端永远拿不到明文**。

| 环节 | 做法 |
|---|---|
| 存储 | Windows DPAPI 加密后入库（`secretbox.py`），密文绑定当前登录账号，拷到别的机器/账号解不开 |
| 下发 | `GET /api/conductor` 只返回掩码，如 `sk-a••••••••9671`；没有任何接口能回读明文 |
| 修改 | 只能整体覆盖写入：留空＝不改，点「清除 key」＝删掉库里的，回退到 `.env` |
| 来源 | 每项都标来源（`界面保存` / `来自 .env`），一眼看出当前生效的是哪套 |

优先级：**库 > `.env` > 内置默认**。没在界面上配过就沿用 `.env`，所以老配置不会被悄悄改掉。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/conductor` | 配置视图（key 只有掩码） |
| PUT | `/api/conductor` | `{base_url?, model?, api_key?}`；`api_key` 缺省=不改，`""`=清除 |
| POST | `/api/conductor/test` | 发一个 4 token 的最小请求，验证 key / 模型名 / base_url |

「测试连接」只在你没有未保存改动时可用——避免测了半天测的是旧配置。

---

## 自动验收开关 + 用量账本（M4.1）

**自动验收**（顶栏右侧开关，默认**关**）

关着的时候：每个节点跑完进 `reviewing`，必须点「通过验收」才放行下游。
开着的时候：跑完直接 `done`，并额外发一条 `task.auto_accepted` 事件，时间线显示「自动 自动通过「…」」。

开关存 `workspace.auto_accept`，走 `POST /api/workspace/auto-accept`。
**默认关的理由**：真模型的产出没人看过就往下游传，错了会一路传到底。要快可以开，但要清楚代价。

**用量账本**（`llm_usage` 表）

之前预算计数放在进程内存里，后端一重启就归零——等于没有预算。现在每次云端调用落一行，`budget()` 从表里求 SUM，**跨重启累计**，并记录 `since`（第一笔时间）。界面显示「累计 X / 200000 · 自 xx 起」。

`POST /api/plan/budget/reset` 可清空（计划页有「清空账本」按钮）。

## 现场探测（/api/agents/{id}/probe）

"能不能用"这件事没法从配置里读出来——它取决于登录态和额度。所以给每个成员加了探测接口，Agent 池里不可用的卡显示「检测」按钮：

| 成员 | 探测方式 | 成本 |
|---|---|---|
| qwen / r1 | `GET :11434/api/tags` | 0 |
| 豆包 | `GET :9333/json/version` | 0 |
| Codex | `codex --version` + `codex login status` | 0 |
| WorkBuddy | 跑一次 `-p --tools ""` 的空请求，看是否返回 `Authentication required` | 未登录时 0.4s / 0 token |

探测结果会写回 `agent.health` 与 `evidence`，所以登录之后点一下「检测」就能自动变回可用，不用改库。

## 交接 ContextPacket（M5）

上游完成 → 打包上下文 → 交给下游。**不是把聊天记录甩过去**。

packet 只装四样东西（`backend/app/handoff.py`）：

| 字段 | 内容 | 为什么必须有 |
|---|---|---|
| `goal` | 项目目标 | 下游常常不知道自己在为谁做 |
| `upstream[]` | 上游任务标题 + 字数 + 摘要（≤900 字） | 不重做已完成的活 |
| `decisions[]` | 已定决策 | **缺这一项，接手的人必然推翻重来** |
| `failures[]` | 该任务之前失败的 reason | 不重复踩同一个坑 |

`upstream[].real` 标出"有没有真实产出"：模拟推进的任务会标 `real=false`，提示词里写成「没有真实产出，别当依据」，而不是让下游误以为拿到了东西。

**注入方式**：`adapters.build_prompt()` 把 packet 压成一段「【交接上下文】」，实测 prompt 697 字（含目标/验收/上游摘要/决策）。塞太多 9B 会抓不住重点，所以只给摘要不给全文。

**接收语义**：下游是模型，没法真的"表态"，所以建记录时状态就是 `accepted` + `auto=true`，界面上写清「自动接收」。要否决就点「退回这个交接」→ 下游任务回 `pending`、上下文清空、事件留痕。

**已实测**：`p_n6 代码评审` 验收 → 自动生成 `h_p_n6__p_n7`，p_n7 的 context 里带上 386 字评审摘要（内容真是关于 float 精度和跨年月份边界），拼进 prompt 后模型确实拿到了。

## 已知限制

- **豆包必须带调试端口启动**才能被驱动（`--remote-debugging-port=9333`）。没带端口时任务会明确失败并提示怎么重启，不会静默假装在跑。
- 豆包的「工作任务」是一整个 GUI 会话，**一个任务会占用它的窗口**；并发派多个豆包任务会互相干扰，当前一次只跑一个。
- 豆包回读包含它的执行轨迹（`已执行代码 …`），这是工作任务模式的固有形态，没有做成"只保留结论"的过滤。
- DSH / 主脑仍是模拟进度（已明确标注）。
- **Codex 未启用**：本机 `logged_in=false` 且无额度，`CODEX_ENABLED=false`。真要说它"能用"得先把登录与额度恢复，再跑一次端到端验证。
- **WorkBuddy 桌面端会抢焦点**：它的输入框只认系统级键盘事件（见下方桥接小节），所以跑任务期间窗口必须在前台，且**一次只能跑一个**（`_WINDOW_LOCK` 全局串行）。
- **WorkBuddy 必须带调试端口启动**，且每次派活会新建一个会话（视图会切走，原对话在侧边栏）。
- **WorkBuddy 正常双击启动时 agentmesh 接不上**：只能探到「进程在跑但端口没开」，需要重跑 `spike/wb_debug_launch.bat`。心跳能自动恢复「重开且带了端口」的情况，但没法把没带端口的拉起来。
- **WorkBuddy CLI 路线已弃用**：本机 `codebuddy` 存不住 OAuth 凭据（at-rest 加密缺 key），API Key 也拿不到，`-p` 模式跑不通，故改走桌面端 UI。
- **主脑拆解没有本地兜底**：云端 key 失效或余额耗尽就拆不出来（会明确报错，不会假装成功）。
- 自动验收默认关；打开后 8 节点计划会一路跑完，**没有人看过中间产物**。
- 泳道图一个格子只放一个任务，同 Agent 同阶段多任务会折叠成 `+N`。
- DAG 视图纵向超过一屏时需要滚动，没做自动缩放。
- 主脑目前只有路由打分，没有拆解与计划审批（M4）。
- 真跑进度封顶 96%，完成后跳到 100（因为无法预估总 token 数）。
