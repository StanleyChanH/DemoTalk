# MCP 接入 设计文档

- 日期：2026-06-23
- 状态：待评审
- 子项目：DemoTalk 能力扩展 · 2/3（MCP）
- 关联：复用视觉子项目建的 `app/tools/` 框架；后续 Skills 复用同一框架

## 1. 背景与目标

视觉子项目建立了通用 tool 框架（`app/tools/`：`ToolResult`/`ToolContext`/`Tool` 协议 + `ToolRegistry` + tool-calling 循环）。本子项目接入 **MCP（Model Context Protocol）**：DemoTalk 作为 MCP client，连接外部 MCP server，把 server 的 tools 暴露给 LLM。

**目标**：
- **统一配置**（`mcp.json`，标准 `mcpServers` 格式）接入多个 MCP server
- 支持 **SSE + stdio** 两种 transport
- **启动时进程级加载**，MCP tools 与内置 `take_photo` 共存
- **完全复用**现有 tool-calling 循环（session/llm 零改动）

**非目标（本子项目不做）**：
- Skills 系统（另立子项目）
- MCP resources / prompts（只接 tools；resources 后续）
- 自动重连（连接断开不自动恢复，YAGNI）
- 会话级隔离（用进程级共享）

## 2. 已验证的技术约束

实测官方 `mcp` Python SDK：
- ✅ SSE transport 连 howtocook-mcp（`https://mcp.api-inference.modelscope.net/11cb95ca0ea64c/sse`）成功，server `howtocook-mcp v1.6.0`
- ✅ `list_tools` 返回 5 个 tools：`mcp_howtocook_getAllRecipes` / `getRecipesByCategory` / `recommendMeals` / `whatToEat` / `getRecipeById`
- ✅ `mcp` SDK（`sse_client` / `stdio_client` / `ClientSession`）可用

## 3. 总体架构

**核心**：MCP server 的 tools → `McpToolAdapter`（实现 `Tool` 协议）→ 注册进 `ToolRegistry`。session/llm 的 tool-calling 循环**零改动**。

### 3.1 新增模块 `app/mcp/`

- `config.py` — 读 `mcp.json`，解析为 server 配置列表（name / type=sse|stdio / url 或 command+args+env）
- `client.py` — `McpClient`：连单个 server（SSE 用 `sse_client`，stdio 用 `stdio_client`），`initialize` / `list_tools` / `call_tool` / `close`
- `adapter.py` — `McpToolAdapter`：实现 `Tool` 协议；`schema` 取自 MCP tool 的 name/description/inputSchema；`execute(ctx)` 把 `ctx.args` 转发给 `McpClient.call_tool`，结果（文本）包成 `ToolResult`
- `manager.py` — `McpManager`：启动时遍历配置连所有 server，为每个 tool 建 `McpToolAdapter` 注册进 `ToolRegistry`；连接失败跳过并记日志；关闭时断开所有

### 3.2 启动接入

`app/main.py` 用 FastAPI lifespan：startup 调 `McpManager.load_all(tool_registry)`；shutdown 调 `close_all()`。

### 3.3 session / llm 零改动

`ToolRegistry` 现在有 `take_photo` + MCP tools。`session._run_turn` 的 tool 循环不变。

## 4. 配置文件 `mcp.json`

项目根，标准 `mcpServers` 格式：

```json
{
  "mcpServers": {
    "howtocook-mcp": { "type": "sse", "url": "https://mcp.api-inference.modelscope.net/11cb95ca0ea64c/sse" },
    "some-local":    { "type": "stdio", "command": "npx", "args": ["-y", "some-mcp-server"], "env": {} }
  }
}
```

## 5. 生命周期

- **startup**：`McpManager.load_all(registry)` — 遍历 servers，每个 `McpClient` 连接 → `initialize` → `list_tools` → 为每个 tool 建 `McpToolAdapter` 注册进 registry
- **shutdown**：`McpManager.close_all()` — 断开所有 client（SSE 关连接 / stdio 终止子进程）
- **进程级共享**：所有 WebSocket 会话复用同一组 MCP 连接

## 6. 数据流（MCP tool 调用）

```
用户「晚上吃什么」→ STT → LLM(tools = take_photo + MCP tools)
→ LLM 选 mcp_howtocook_whatToEat → session tool 循环
→ registry.execute → McpToolAdapter.execute → McpClient.call_tool → howtocook server
→ 菜谱文本 → ToolResult(text) → add_tool → LLM 基于 result 回答 → TTS
```

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| `mcp.json` 不存在 | 跳过 MCP 加载（无 MCP tools），主服务 + 视觉 take_photo 正常 |
| 某 server 连接 / 初始化失败 | 跳过该 server，记日志，其他 server 不受影响 |
| MCP tool 调用失败 | 复用 `ToolRegistry.execute` 兜底（错误 `ToolResult`），LLM 据此回答 |
| 连接中途断开 | 下次调用 `McpClient` 报错 → registry 兜底；不自动重连（YAGNI） |

## 8. tool 共存与命名

- registry 同时注册 `take_photo`（视觉）+ 各 MCP tools（如 `mcp_howtocook_*`）
- LLM 一次性看到所有 tool schemas
- 命名：MCP tool 用其原始 name（howtocook 已带 `mcp_howtocook_` 前缀，天然唯一）；万一与 `take_photo` 或跨 server 冲突，退化为 `<server>_<toolname>`

## 9. 测试

- **单元**：config 解析 `mcp.json`（sse/stdio 两种）；`McpToolAdapter`（mock client，验证 schema 透传 + execute 转发 args、result 转 ToolResult）；`McpManager`（mock client，验证多 server 加载 + 单个失败跳过不影响其他）
- **集成**：selftest 新增 **Phase 5** — 启动时 howtocook-mcp 注册成功；问「晚上吃什么」→ 期望 LLM 调 `mcp_howtocook_whatToEat` → 基于菜谱结果回答 + TTS
- **手动**：浏览器问菜谱问题

## 10. 配置项（`.env` / `app/config.py`，新增）

```
ENABLE_MCP=true              # MCP 总开关
MCP_CONFIG_FILE=mcp.json     # MCP 配置文件路径
```

## 11. 将来扩展

- **Skills 子项目（3/3）**：同理把 skills 暴露为 tools 注册进 `ToolRegistry`，复用 tool-calling 循环。
- MCP resources / prompts 后续按需接入。
