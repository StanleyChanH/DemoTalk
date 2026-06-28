# 前端功能开关（中断 / MCP / 语义结束）设计

日期：2026-06-28

## 背景与目标

DemoTalk 当前通过 `.env` 的三个布尔配置控制运行时行为：

- `ENABLE_BARGE_IN`：助手说话时用户开口是否自动打断（barge-in）
- `ENABLE_MCP`：是否接入外部 MCP 工具服务器
- `ENABLE_END_BY_VOICE`：是否允许用户用自然语言（「再见」等）结束对话

这些配置仅在**后端启动时**读取，运行中无法调整。目标：在前端增加开关，让用户在浏览器中实时切换这三项能力；`.env` 的值作为**默认值**，前端开关可覆盖。

## 范围

**包含：**

- 前端设置面板（顶栏齿轮入口），三个 toggle 开关
- 后端会话级开关状态 + 动态生效
- 新增 WS 协议消息：`config_defaults`（后端→前端）、`set_flags`（前端→后端）
- localStorage 记住用户上次选择

**不包含：**

- 不改动 STT/LLM/TTS 核心链路
- 不卸载/重载 MCP server 连接（进程级保持，仅会话级屏蔽工具）
- 不新增 HTTP 端点（复用现有 WS）
- 不把开关状态持久化到后端（仅前端 localStorage）

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 生效时机 | **立即生效**（会话中动态） | 三个开关技术上都能做到；符合设置开关直觉 |
| MCP 关闭语义 | **会话级屏蔽工具**（保持连接） | MCP 连接进程级共享，卸载会影响其他会话 |
| 开关 UI 位置 | **设置面板**（顶栏齿轮弹出） | 主界面已满（双列），齿轮最干净，会话中可随时切换 |
| 状态记忆 | **localStorage 记住上次** | 跨刷新/会话保留；用户偏好优先于 `.env` 默认 |

## 设计

### 1. 协议与数据流

新增两条 WS 消息：

| 方向 | type | 时机 | 字段 |
|---|---|---|---|
| 后端→前端 | `config_defaults` | WS 连接后，`start()` 内（紧接 `tts_format`/`vision_config`） | `barge_in` / `mcp` / `end_by_voice`（`.env` 值）、`mcp_available`（bool） |
| 前端→后端 | `set_flags` | ① 连接后首次（由 `config_defaults` 触发）② 用户每次切换 | `barge_in` / `mcp` / `end_by_voice`（bool） |

**同步时序：**

```
[连接]
后端 start() ──config_defaults(.env默认 + mcp_available)──► 前端
前端：flag = localStorage有值 ? 上次值 : .env默认；渲染 toggle；立即 set_flags 同步
[会话中]
用户切 toggle ──set_flags──► 后端动态应用 ──► 立即生效
```

**"立即生效"语义：**

- **barge_in**：后端读取改为会话属性 `self.barge_in_enabled`（替代 `config.ENABLE_BARGE_IN`）；`set_flags` 更新它 → 下一句句末生效
- **mcp / end_by_voice**：`set_flags` 动态增删 `tool_registry` → 下一轮 `_run_turn` 调 `schemas()` 时 LLM 看到新工具列表。MCP 连接（进程级）保持不动。

**优先级**：`localStorage 上次值` > `.env 默认` > 代码硬编码默认。用户改 `.env` 后，已有 localStorage 的老用户仍用旧偏好（符合"用户偏好优先于配置默认"）。

### 2. 后端改造

**`app/tools/registry.py`** — 来源标记 + 增删能力：

- `_tools: dict[str, tuple[Tool, str]]`（name → (tool, source)）
- `register(tool, source="builtin")`
- 新增 `unregister(name)`、`clear_by_source(source)`
- `get / schemas / execute` 适配 tuple

**`app/mcp/manager.py`** — 标 source + 暴露可用性：

- `register_into` 改为 `registry.register(a, source="mcp")`
- 新增 `has_tools() -> bool`（`len(self._adapters) > 0`）

**`app/session.py`** — 会话级状态 + 下发 + handler：

- `__init__`：加 `self.barge_in_enabled = config.ENABLE_BARGE_IN`（工具注册逻辑不变）
- `start()`：下发 `config_defaults`（三字段 + `mcp_available = mcp_manager.has_tools()`）
- `_on_final`：`config.ENABLE_BARGE_IN` → `self.barge_in_enabled`
- 新增 `set_flags(msg)`，按字段动态应用（每个字段 `if key in msg` 守卫，部分/重复均幂等）：
  - `barge_in` → `self.barge_in_enabled = bool(...)`
  - `mcp`：True 且当前无 mcp 工具 → `mcp_manager.register_into(registry)`；False 且有 → `registry.clear_by_source("mcp")`
  - `end_by_voice`：True 且无 → `registry.register(EndConversationTool())`；False 且有 → `registry.unregister("end_conversation")`

**`app/main.py`** — 消息分发：

- ws_endpoint 加 `elif t == "set_flags": await session.set_flags(obj)`

**并发安全**：`set_flags` 与 `_run_turn` 同在事件循环单线程；registry 的 dict 增删仅在 `async for astream_once` 让出点之间发生，下一轮 `schemas()` 读到新值。正在执行的 tool_call 照常完成，下一轮才变（符合"立即生效"=下一句/下一轮）。

### 3. 前端改造

**`static/index.html`**：

- 顶栏 status 区左侧加齿轮按钮 `#btnSettings`
- 设置面板 `#settingsPanel`（默认隐藏），三行 toggle：
  - 允许打断 — 助手说话时你开口，立即停止并进入新一轮
  - MCP 工具 — 允许助手调用外部工具服务器
  - 语义结束 — 说「再见」等由助手自动结束对话
- 底部小字：「默认值来自 .env，切换后立即生效」

**`static/style.css`**：

- 齿轮：玻璃质感图标按钮，hover 暖琥珀高亮
- 面板：绝对定位弹出卡片，毛玻璃 + 暖琥珀描边，与现有 `.panel` 一致
- 开关 `.switch`：关=灰、开=暖琥珀渐变 + 微光，knob 滑动过渡

**`static/app.js`**（不动现有 WS/音频/打字机逻辑）：

- 状态：`flags = {barge_in, mcp, end_by_voice}`、`mcpAvailable`
- `loadFlags()` / `saveFlags()`：localStorage key `demotalk.flags`（JSON）
- `config_defaults` handler → `applyDefaults()`：每个 flag = localStorage 有该 key ? 上次值 : `.env` 默认；`mcpAvailable = obj.mcp_available`，为 false 时 MCP toggle `disabled`；渲染三个开关 → **立即 `sendFlags()`**
- 切换 toggle → `setFlag(key, val)`：更新 flags、`saveFlags()`、渲染、`sendFlags()`
- `sendFlags()`：`ws` 可写时发 `{type:"set_flags", ...flags}`；未连接只存本地（连接后由 `config_defaults` 触发同步）
- 齿轮点击切面板；面板外点击关闭

**同步点**：放在 `config_defaults` handler（而非 `ws.onopen`），因为 `config_defaults` 一定在连接建立后由后端主动下发，时序确定——到达即初始化 + 同步，避免 onopen 与首条事件的竞态。

**未连接也可切**：设置面板始终可操作，切换只写 localStorage；下次连接 `config_defaults` 到达时用 localStorage 值初始化并同步。

### 4. 边缘情况与测试

**边缘情况（均已覆盖）：**

1. `set_flags` 与 `_run_turn` 并发 → 事件循环单线程，下一轮生效（第 2 节已述）
2. `mcp_available=false`（`.env` 没开 MCP 或加载失败）→ 前端 MCP toggle `disabled`；即便强发 `mcp=true`，后端 `register_into` 遍历空 adapter 列表，无害
3. 告别语收尾中（`_ending=True`）切 end_by_voice → 当前轮已 in-flight 不受影响，只改下一轮工具列表
4. speaking 中切 barge_in → 当前播报不中断，下一句句末按新值
5. `set_flags` 字段缺失/重复 → `if key in msg` 守卫 + 操作幂等
6. localStorage 损坏/非布尔 → `JSON.parse` try/catch 返回空，回退 `.env` 默认
7. 断开重连 → 新会话重发 `config_defaults`，localStorage 保留，到达时重新初始化 + 同步

**测试（pytest，沿用现有 `tests/` 结构）：**

- `tests/tools/test_registry.py`（新）：source 标记、`unregister`、`clear_by_source`、`schemas/execute` 适配 tuple
- `tests/mcp/test_manager.py`（新）：`register_into` 标 `source="mcp"`、`has_tools()`
- `tests/test_session_flags.py`（新）：
  - `set_flags` 改 `barge_in_enabled`；`_on_final` 在 speaking 时按会话属性决定是否 barge-in（开→打断 / 关→忽略）
  - `set_flags` mcp / end_by_voice 的 on/off 增删工具
  - `start()` 下发 `config_defaults`（断言三字段 + `mcp_available`）
- **前端**：项目无 JS 测试框架，沿用现有做法**手动验证**——启动后端，浏览器切三个开关观察立即生效，刷新验证 localStorage 保持、`.env` 改动后默认值回显

## 文件清单

| 文件 | 改动 |
|---|---|
| `app/tools/registry.py` | 改造：source 分组 + `unregister`/`clear_by_source` |
| `app/mcp/manager.py` | `register_into` 标 source；加 `has_tools()` |
| `app/session.py` | `barge_in` 会话属性；`config_defaults` 下发；`set_flags` handler |
| `app/main.py` | ws_endpoint 分发 `set_flags` |
| `static/index.html` | 齿轮 + 设置面板 |
| `static/style.css` | 面板 + 开关样式（暖夜风格） |
| `static/app.js` | flags 状态 + localStorage + 同步逻辑 |
| `tests/tools/test_registry.py` | 新增 |
| `tests/mcp/test_manager.py` | 新增 |
| `tests/test_session_flags.py` | 新增 |
| `README.md` | 补充三开关的前端控制说明（可选） |

## 验收标准

1. 浏览器打开页面，点齿轮弹出三个开关，初始状态 = `.env` 默认（首次无 localStorage）
2. 会话中切换任一开关，立即生效：barge_in 下一句句末、mcp/end_by_voice 下一轮 LLM 调用
3. 刷新页面，开关保持上次选择（localStorage）
4. 改 `.env` 后，无 localStorage 的开关显示新默认值（localStorage 优先于 `.env`）
5. `.env` 关闭 MCP 时，MCP toggle `disabled`
6. 全部 pytest 通过
