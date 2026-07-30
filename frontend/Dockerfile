# syntax=docker/dockerfile:1.7
#
# DemoTalk 前端镜像：nginx 纯静态 + 启动时按 DEMOTALK_BACKEND_URL 生成 config.js。
# 浏览器加载本容器的静态页面，直连后端容器的 /ws（跨域）。
# 构建上下文为本目录（frontend/）。

FROM nginx:stable-alpine

# 静态资源：index.html 置于 root（/ 返回它），其余放 /static/（保持原引用路径 /static/app.js 等）
COPY index.html /usr/share/nginx/html/index.html
COPY static/ /usr/share/nginx/html/static/

# nginx 站点配置 + 启动脚本（生成 config.js 注入后端 WS 地址）
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /entrypoint.sh
# Windows 检出可能带 CRLF，去掉避免 alpine /bin/sh 报错
RUN chmod +x /entrypoint.sh && sed -i 's/\r$//' /entrypoint.sh

EXPOSE 80

CMD ["/entrypoint.sh"]
