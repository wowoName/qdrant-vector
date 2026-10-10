# ============================================================
# 前端镜像：Node 构建静态文件 → Nginx 托管 + 反代 /api
# ============================================================
FROM node:22-alpine AS build
WORKDIR /app

# 先装依赖（利用层缓存）
COPY package.json package-lock.json ./
RUN npm ci

# 再拷贝源码并构建
COPY . .
RUN npm run build

# ---- 运行阶段：仅保留静态产物 + Nginx ----
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
