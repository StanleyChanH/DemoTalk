#!/bin/sh
# 按 DEMOTALK_BACKEND_URL（浏览器视角的后端 WS 地址）生成 config.js，供 app.js 读取。
# 默认 ws://localhost:8000（本地 docker compose：前端宿主 8080、后端宿主 8000）。
# 生产改 wss://api.example.com（由后端前置 TLS 代理终止）。
set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.DEMOTALK_WS_URL = "${DEMOTALK_BACKEND_URL:-ws://localhost:8000}/ws";
EOF

exec nginx -g 'daemon off;'
