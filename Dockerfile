# syntax=docker/dockerfile:1.7
#
# DemoTalk Docker 镜像：FastAPI + 静态前端 + WebSocket（实时语音助手）
# 多阶段构建：builder 用 uv 装依赖，runtime 只带 .venv + 运行所需源码/静态资源。
# 用法见 README「Docker 部署」；推荐用 docker compose（docker-compose.yml）。

# ============ builder：用 uv 装依赖到 .venv ============
FROM python:3.12-slim AS builder

# 取 uv 二进制（官方推荐：从 uv 镜像 COPY 二进制，无需单独作 base）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 用镜像自带的 Python 3.12（不联网下解释器）；预编译字节码加速冷启动；
# copy 模式避开容器内硬链接 / bind mount 缓存的问题
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 第一步：只装第三方依赖（仅 bind uv.lock + pyproject.toml）。
# 源码改动不会命中这一层缓存失效，显著加速日常 rebuild。
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# 第二步：复制源码与运行所需静态资源（README 仅 hatchling 打包 wheel 时需要读取）
COPY app/ ./app/
COPY static/ ./static/
COPY mcp.json README.md ./

# 装项目本体（保持源码原位安装；app/main.py 以 __file__ 定位 static/，
# 不用 --no-editable，确保 BASE_DIR/static 路径语义与本地开发一致）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ============ runtime：精简运行镜像 ============
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    # 容器内必须监听 0.0.0.0 才能被宿主访问（compose 也会覆盖此项，双保险）
    HOST=0.0.0.0

WORKDIR /app

# 仅拷运行所需：.venv（含全部依赖与 demotalk 入口）+ 源码 + 静态 + mcp 配置
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/static /app/static
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

# 直接走 uvicorn 命令（透明、可加 --workers 等参数）；host 固定 0.0.0.0
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
