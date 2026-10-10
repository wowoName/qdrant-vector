# 阿里云部署文档（Vector Data Manager）

本文档描述如何把本项目**打包**并**部署到阿里云 ECS**（单机方案：Nginx + FastAPI + PostgreSQL + Qdrant + Redis）。

> 适用范围：Ubuntu 22.04 / Alibaba Cloud Linux 3 均可，命令以 Ubuntu 为例（`apt`）。
> 如使用 Alibaba Cloud Linux，把 `apt` 换成本机包管理器（`dnf`/`yum`）即可。

---

## 0. 部署架构

```
                        ┌─────────────── 阿里云 ECS ───────────────┐
   浏览器 ──80/443──►   │  Nginx                                   │
                        │   ├── /            静态前端 (frontend/dist)│
                        │   └── /api/  ───►  Uvicorn/FastAPI :8000  │
                        │                     │  (127.0.0.1, 仅本机) │
                        │                     ├─ PostgreSQL :5432    │
                        │                     ├─ Qdrant     :6333    │
                        │                     └─ Redis      :6379    │
                        │        Docker Compose（仅监听 127.0.0.1） │
                        └───────────────────────────────────────────┘
```

关键点：
- 前端与 API **同源**（Nginx 反代 `/api`），因此前端无需配置 `VITE_API_BASE_URL`（默认 `/api`）。
- PostgreSQL / Qdrant / Redis **只监听 127.0.0.1**，不暴露到公网。
- FastAPI 使用**单进程**运行（原因见 [第 9 节](#9-systemd-守护后端)）。

---

## 1. 前置准备

### 1.1 ECS 规格建议
| 项 | 建议 |
|---|---|
| CPU / 内存 | ≥ 4 vCPU / 8 GB（本地 Embedding 用 CPU 推理，内存偏大；首次加载模型会占用较多内存） |
| 系统盘 | ≥ 40 GB ESSD（还要留出文档存储、向量数据、模型缓存） |
| 系统 | Ubuntu 22.04 64 位 / Alibaba Cloud Linux 3 |
| 带宽 | 按实际访问量，≥ 5 Mbps 起步 |

### 1.2 安全组规则（ECS 控制台 → 安全组）
| 端口 | 协议 | 来源 | 用途 |
|---|---|---|---|
| 22 | TCP | 你的公网 IP | SSH |
| 80 | TCP | 0.0.0.0/0 | HTTP |
| 443 | TCP | 0.0.0.0/0 | HTTPS（启用证书时） |

> **不要**对公网开放 5432 / 6333 / 6334 / 6379 / 8000。这些端口只在 ECS 本机内部使用。

### 1.3 域名与备案（中国大陆地域必读）
- 中国大陆地域 ECS 绑定域名并监听 80/443，域名需完成 **ICP 备案**，否则会被阻断。
- 如暂不备案，可先用 `http://<公网IP>` 访问（安全组放行 80），或仅用 `:8000` 临时调试（不推荐长期）。

---

## 2. 服务器初始化

```bash
# 以 root 或 sudo 执行
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ca-certificates nginx
```

### 2.1 安装 Docker 与 Compose 插件（阿里云镜像加速）

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker

# 阿里云镜像加速器（控制台「容器镜像服务 → 镜像加速器」可获取专属地址）
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://<你的专属ID>.mirror.aliyuncs.com"]
}
EOF
sudo systemctl restart docker

# 校验 compose 插件
docker compose version
```

### 2.2 安装 Python 3.11 与 uv（后端运行时）

```bash
sudo apt install -y python3.11 python3.11-venv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc          # 或重开终端，使 uv 生效
uv --version
```

> 也可使用阿里云 PyPI 源加速依赖安装：
> `export UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/`

### 2.3 安装 Node.js 20 LTS（用于构建前端）

本项目前端使用 **Vite 8**，建议 Node ≥ 20。

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node -v && npm -v
```

> 若不想在服务器上装 Node，可在**本地**执行 `npm run build` 后，仅把 `frontend/dist` 上传到服务器（见第 7 节）。

### 2.4 目录规划

```bash
sudo useradd -m -s /bin/bash vdm 2>/dev/null || true
sudo mkdir -p /opt/vdm
sudo chown -R vdm:vdm /opt/vdm

# 静态站点目录（Nginx）
sudo mkdir -p /var/www/vdm
sudo chown -R vdm:vdm /var/www/vdm
```

服务器目录结构（部署完成后）：

```
/opt/vdm/
├── docker-compose.yml     # 中间件编排
└── backend/               # 后端代码 + .env + data/
    ├── app/
    ├── .venv/
    └── data/              # storage / logs / exports / models
/var/www/vdm/              # 前端 dist 静态文件
```

---

## 3. 上传代码

任选其一（在服务器上操作，使用 `vdm` 用户）：

```bash
# 方式 A：git（推荐）
sudo -iu vdm
cd /opt/vdm
git clone <你的仓库地址> repo && mv repo/backend /opt/vdm/backend \
  && mv repo/docker-compose.yml /opt/vdm/ && rm -rf repo

# 方式 B：本地打包上传（无 git 仓库时）
# 本地执行：tar --exclude=node_modules --exclude=.venv --exclude=data -czf vdm.tar.gz backend docker-compose.yml
# scp vdm.tar.gz root@<公网IP>:/tmp/   然后服务器上解压到 /opt/vdm/
```

> 上传时**不需要**包含：`frontend/node_modules`、`backend/.venv`、`backend/data`、`backup/`。

---

## 4. 部署整套服务

仓库根提供两份编排文件，按场景二选一：

| 文件 | 用途 | 启动命令 |
|---|---|---|
| `docker-compose.yml` | 仅本地开发用中间件（端口全开） | `docker compose up -d qdrant redis` |
| `docker-compose.prod.yml` | 生产整套服务（中间件 + 后端 + 前端，**全容器化**） | `docker compose -f docker-compose.prod.yml up -d --build` |

推荐用 `docker-compose.prod.yml` 一键部署整套服务：宿主机只需 Docker，后端/前端也在容器内运行，只有前端 Nginx 的 80 端口对外，中间件与后端均不暴露。

```bash
cd /opt/vdm
cp backend/.env.example backend/.env      # 先按需修改（DB 账号 / 密钥 / API Key）
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

> 采用全容器化部署时，本文档第 5~9 节（宿主机 `uv sync`、装 Nginx、配 systemd）**可跳过**——那些步骤对应的是「后端/前端直接在宿主机运行」的另一种方式。

compose 中 PostgreSQL 的账号为（**`.env` 必须与之一致**）：

| 变量 | 值 |
|---|---|
| 用户 | `vdm_user` |
| 密码 | `vdm_password` |
| 数据库 | `vdm_db` |

---

## 5. 打包 / 安装后端

后端是 Python 应用（`uv` 管理，入口 `app.main:app`）。生产环境推荐在服务器上安装依赖并生成 `.venv`。

```bash
sudo -iu vdm
cd /opt/vdm/backend

# 生成虚拟环境并安装锁定版本依赖
uv sync --frozen

# 校验可导入
uv run python -c "import app.main; print('backend import OK')"
```

> 依赖中包含 `torch`、`sentence-transformers`，体积较大，首次安装可能较慢；`uv` 已做缓存，重复部署会很快。

---

## 6. 配置后端环境变量

```bash
cd /opt/vdm/backend
cp .env.example .env
# 生成随机 SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

编辑 `.env`，**生产环境至少确认以下几项**：

| 变量 | 生产建议值 | 说明 |
|---|---|---|
| `APP_ENV` | `production` | 运行环境 |
| `APP_DEBUG` | `false` | 关闭调试（关闭后 `/docs` 也不暴露） |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | 由 Nginx 反代，仅本机监听 |
| `WORKERS` | `1` | **必须为 1**，见第 9 节 |
| `SECRET_KEY` | 上一步生成的随机串 | JWT 签名密钥，务必修改 |
| `CORS_ORIGINS` | `https://你的域名` | 同源部署时可收紧；多来源用逗号分隔 |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `vdm_user` / `vdm_password` / `vdm_db` | **与 compose 一致** |
| `DB_HOST` / `DB_PORT` | `127.0.0.1` / `5432` | |
| `CHAT_DB_*` | 与 `DB_*` 相同 | 对话库（可复用同一 PostgreSQL 实例） |
| `QDRANT_HOST` / `QDRANT_PORT` | `127.0.0.1` / `6333` | Windows 环境才建议特殊处理；Linux 用 127.0.0.1 |
| `REDIS_ENABLED` | `true` | |
| `REDIS_HOST` / `REDIS_PORT` | `127.0.0.1` / `6379` | |
| `STORAGE_ROOT` | `./data/storage` | 相对后端工作目录，注意不要改动 systemd 的工作目录 |
| `EMBEDDING_MODEL` | `bge-small-zh-v1.5` | 首次启动会下载到 `data/models/` |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 国内下载模型加速 |
| `ARK_API_KEY` / `LLM_*` | 按实际填写 | LLM 供应商 Key |
| `SMTP_*` | 按实际填写 | 注册验证码邮件；未配置时开发模式才会返回 `dev_code` |
| `TENANT_TRIAL_DAYS` | `7` | 新注册租户试用天数 |
| `LOG_LEVEL` | `INFO` | |

> ⚠️ 常见坑：本地开发用的 `.env` 里 `DB_USER=postgres/root123`，而 compose 里是 `vdm_user/vdm_password`。**二者必须统一**，否则连接失败。

---

## 7. 打包前端

前端产物为纯静态文件（输出到 `frontend/dist/`），由 Nginx 托管。前端请求统一走 `/api`（同源反代），**无需**额外配置后端地址。

### 方式 A：服务器上构建

```bash
sudo -iu vdm
cd /opt/vdm/frontend      # 若只上传了 dist 可跳过本方式
npm ci                    # 依据 package-lock.json 安装
npm run build             # vue-tsc 类型检查 + vite build → dist/
cp -r dist/* /var/www/vdm/
```

### 方式 B：本地构建后上传（省去服务器 Node）

```bash
# 本地（Windows PowerShell 示例）
cd frontend
npm ci
npm run build
scp -r dist/* root@<公网IP>:/var/www/vdm/
```

> 若前后端不同源（例如前端放 OSS/CDN），需在构建前设置 `frontend/.env.production`：
> `VITE_API_BASE_URL=https://你的API域名/api`，并重新 `npm run build`。

---

## 8. 配置 Nginx

新建 `/etc/nginx/conf.d/vdm.conf`：

```nginx
server {
    listen 80;
    server_name your.domain.com;      # 无域名时填公网 IP 或 _

    root /var/www/vdm;
    index index.html;

    # 大文件上传（需 ≥ 后端 UPLOAD_MAX_FILE_SIZE，默认 10GB）
    client_max_body_size 10g;

    # 前端单页应用：找不到的路径回退到 index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 后端 API 反代
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        # SSE 流式对话必须关闭缓冲，否则回答会“攒着一次性返回”
        proxy_buffering off;
        proxy_cache off;

        # 大文件上传 / 长耗时任务
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

校验并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 9. systemd 守护后端

创建 `/etc/systemd/system/vdm-backend.service`：

```ini
[Unit]
Description=VDM Backend (FastAPI)
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User=vdm
WorkingDirectory=/opt/vdm/backend
Environment="PATH=/opt/vdm/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/opt/vdm/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
# 允许长任务在停止时优雅退出
TimeoutStopSec=30
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

> **为什么必须 `--workers 1`？**
> 应用在启动时（`app/main.py` 的 lifespan）会拉起**进程内**的异步任务队列（`start_task_workers`），启动时还会把 `running/pending/queued` 的任务重新入队。如果起多个 worker 进程，每个进程都会各自拉起 worker 并把同样的任务重新入队，导致**文档解析等任务被重复消费**。因此请保持单进程；并发能力由 FastAPI 的异步 IO 承担。

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vdm-backend
sudo systemctl status vdm-backend
```

---

## 10. 初始化与首次验证

### 10.1 数据表与迁移
应用启动时会自动执行：
1. 创建主库/对话库数据表（`init_db` / `init_chat_db`）；
2. 运行结构迁移（`app/core/migrations.py` 中的 v1~v7，均为幂等）。

如需手动执行迁移（排障时）：

```bash
cd /opt/vdm/backend && uv run python -m app.core.migrations
```

### 10.2 Embedding 模型
首次启动会按 `EMBEDDING_MODEL` 下载模型到 `data/models/`（走 `HF_ENDPOINT`）。网络受限时可提前手动下载到该目录，并设置 `EMBEDDING_OFFLINE=true`。

### 10.3 验证清单

```bash
# 后端进程
systemctl status vdm-backend --no-pager

# 健康检查（后端本机）
curl -s http://127.0.0.1:8000/health

# 经由 Nginx
curl -s http://127.0.0.1/api/v1/system/health

# 端口监听（确认 8000 只在本机、5432/6379/6333 不在 0.0.0.0）
ss -ltnp | grep -E '8000|5432|6379|6333'
```

浏览器访问 `http://<公网IP 或 域名>`，注册第一个账号。
> 首个注册用户会**自动成为总管理员（super_admin）**，请牢记该账号密码。

---

## 11. 配置 HTTPS（可选，推荐）

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.com
```

证书自动续期由 certbot 的 systemd timer 处理，可用 `sudo certbot renew --dry-run` 验证。
（也可使用阿里云「数字证书管理服务」签发证书，手动配置 `listen 443 ssl` 与证书路径。）

---

## 12. 备份与运维

| 数据 | 位置 / 方式 |
|---|---|
| PostgreSQL | `docker exec vdm-postgres pg_dump -U vdm_user vdm_db > backup.sql` |
| Qdrant 向量 | Docker 卷 `vdm-qdrant-data`（`docker run --rm -v vdm-qdrant-data:/data ...` 打包） |
| Redis | 卷 `vdm-redis-data`（AOF 已开启） |
| 上传文件 | `/opt/vdm/backend/data/storage` |
| 导出/日志 | `/opt/vdm/backend/data/exports`、`/opt/vdm/backend/data/logs` |

- 建议为 ECS 系统盘**开启快照**策略（每日自动快照）。
- 查看应用日志：`journalctl -u vdm-backend -f` 或 `backend/data/logs/vdm.log`。
- 时区：如日志时间不对，`sudo timedatectl set-timezone Asia/Shanghai` 并重启服务。

---

## 13. 更新发布流程

```bash
sudo -iu vdm
cd /opt/vdm/backend && git pull
uv sync --frozen                       # 依赖有变化时

cd /opt/vdm/frontend && git pull
npm ci && npm run build && cp -r dist/* /var/www/vdm/

sudo systemctl restart vdm-backend
sudo systemctl reload nginx
```

> 数据库结构变更由迁移脚本自动处理，重启后端即生效（迁移幂等）。

---

## 14. 常见问题（FAQ）

| 现象 | 排查方向 |
|---|---|
| 页面 502 Bad Gateway | 后端未启动：`systemctl status vdm-backend`、`journalctl -u vdm-backend` |
| 流式回答“卡住后一次性输出” | Nginx 未关闭缓冲：确认 `proxy_buffering off;` |
| 上传大文件失败（413） | 调大 `client_max_body_size`；确认 `proxy_request_buffering off;` |
| 后端连不上数据库 | `.env` 的 `DB_*` 与 compose 的 `POSTGRES_*` 不一致；或 `DB_HOST` 未用 127.0.0.1 |
| Qdrant 连接失败 | `QDRANT_HOST` 用 `127.0.0.1`，`QDRANT_PORT=6333`；`docker compose ps` 确认容器健康 |
| 模型下载很慢/失败 | 设置 `HF_ENDPOINT=https://hf-mirror.com`；或离线放置模型 + `EMBEDDING_OFFLINE=true` |
| 前端刷新子路由 404 | Nginx 缺少 `try_files $uri $uri/ /index.html;` |
| 跨域错误（CORS） | 同源反代不会跨域；若前后端分离，设置 `CORS_ORIGINS` 为前端域名 |
| 文档任务被重复执行 | 检查是否用了 `--workers > 1`，改回 1 |
| 依赖 `torch` 安装慢 | 使用阿里云 PyPI 镜像；或改用 CPU-only 源 |

---

## 附录 A：可选 —— 容器化打包

若希望前端/后端也以容器方式发布，可参考以下最小配置（与第 4 节 compose 合并使用）。

**backend/Dockerfile**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
ENV APP_HOST=0.0.0.0 APP_PORT=8000 WORKERS=1
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**frontend/Dockerfile**
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

> 提示：容器化后，后端 `.env` 中的 `DB_HOST`/`QDRANT_HOST`/`REDIS_HOST` 要改为 compose 的**服务名**（如 `postgres`/`qdrant`/`redis`），而不是 `127.0.0.1`；同时保持 `WORKERS=1`。

---

## 附录 B：一键检查脚本（可选）

```bash
#!/usr/bin/env bash
set -e
echo "== docker ==";        docker compose -f /opt/vdm/docker-compose.yml ps
echo "== backend ==";       systemctl is-active vdm-backend
echo "== health(api) ==";   curl -sf http://127.0.0.1:8000/health && echo
echo "== health(nginx) ==" ; curl -sf http://127.0.0.1/api/v1/system/health && echo
echo "== ports ==";         ss -ltnp | grep -E '8000|5432|6379|6333' || true
```
