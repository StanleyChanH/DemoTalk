# syntax=docker/dockerfile:1.7
#
# DemoTalk 后端镜像：FastAPI 纯 WebSocket API（/ws + /healthz），不含前端静态资源。
# 前端在独立容器（见 ../frontend）。构建上下文为本目录（backend/）。

# ============ builder：用 uv 装依赖到 .venv ============
FROM python:3.12-slim AS builder

# 取 uv 二进制（官方推荐：从 uv 镜像 COPY 二进制，无需单独作 base）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 用镜像自带的 Python 3.12；copy 模式避开容器内硬链接 / bind mount 缓存问题
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 第一步：只装第三方依赖（仅 bind uv.lock + pyproject.toml），源码改动不失效这层缓存
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# 第二步：复制源码（README 供 hatchling 打包读取；mcp.json 运行时需要）
COPY app/ ./app/
COPY mcp.json README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ============ runtime：精简运行镜像 ============
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    # 容器内必须监听 0.0.0.0 才能被宿主/浏览器访问
    HOST=0.0.0.0

WORKDIR /app

# 仅拷运行所需：.venv + 源码 + mcp 配置（不含 tests/scripts/static）
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/mcp.json /app/mcp.json

# 非 root 运行（最小权限）
RUN groupadd --system --gid 1001 app && \
    useradd --system --uid 1001 --gid app --home-dir /app --shell /sbin/nologin app && \
    chown -R app:app /app
USER app

EXPOSE 8000
# 复用应用现成的 /healthz（slim 镜像无 curl，用 python urllib 探测）
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2).status==200 else 1)"

# 后端纯 WS API：浏览器直连 ws://<host>:8000/ws（生产经前置 TLS 代理成 wss）
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
