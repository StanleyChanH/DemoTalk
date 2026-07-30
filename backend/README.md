# DemoTalk 后端

FastAPI 纯 WebSocket API：`/ws`（实时语音对话）+ `/healthz`（健康检查）+ `GET /`（服务信息）。

**前后端分离**：本服务不再托管静态前端，前端在独立的 nginx 容器（`../frontend`），浏览器直连本服务 `/ws`。

## 本地开发

```bash
uv sync
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

配置见 `../.env.example`（`DASHSCOPE_API_KEY` 必填）。

## 测试

```bash
uv run pytest          # 单元测试
```

## 自检（真实全链路，需先起服务 + 配 Key）

```bash
uv run python scripts/selftest.py
```

## Docker

见 `../docker-compose.yml`：`docker compose up backend`（仅后端）/ `docker compose up`（前后端一起）。
