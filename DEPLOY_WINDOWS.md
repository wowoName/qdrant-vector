# Windows 部署文档（Vector Data Manager）

本文档描述如何把本项目**部署到 Windows**（单机方案：Nginx + FastAPI + PostgreSQL + Qdrant + Redis）。

> 与 [DEPLOY_ALIYUN.md](DEPLOY_ALIYUN.md)（Linux/阿里云）互为姊妹篇，命令全部换成 **PowerShell**。
> 适用范围：Windows 10 / 11、Windows Server 2019 / 2022。

---

## 0. 部署架构

```
                        ┌──────────────── Windows 主机 ────────────────┐
   浏览器 ──80/443──►   │  Nginx (Windows)                             │
                        │   ├── /            静态前端 (frontend/dist)  │
                        │   └── /api/  ───►  Uvicorn/FastAPI :8000     │
                        │                     │  (127.0.0.1, 仅本机)    │
                        │                     ├─ PostgreSQL :5432       │
                        │                     ├─ Qdrant     :6333       │
                        │                     └─ Redis      :6379       │
                        │        Docker Desktop（Qdrant/Redis）         │
                        └──────────────────────────────────────────────┘
```

关键点：
- 前端与 API **同源**（Nginx 反代 `/api`），前端无需配置 `VITE_API_BASE_URL`（默认 `/api`）。
- **PostgreSQL / Qdrant / Redis 只监听 `127.0.0.1`**，不暴露到局域网/公网。
- 后端使用**单进程**运行（原因见 [第 9 节](#9-后端开机自启windows-服务)）。
- ⚠️ Windows + Docker Desktop 环境下，后端连接中间件**一律用 `127.0.0.1`，不要用 `localhost`**：`localhost` 可能被解析成 IPv6 `::1`，被 Docker Desktop 的 `wslrelay` 拦截导致连接失败 / 502。

---

## 1. 前置准备

### 1.1 硬件与系统
| 项 | 建议 |
|---|---|
| CPU / 内存 | ≥ 4 核 / 8 GB（本地 Embedding 用 CPU 推理，内存偏大；首次加载模型占用较多内存） |
| 系统盘 | ≥ 40 GB（文档存储 + 向量数据 + 模型缓存） |
| 系统 | Windows 10/11 或 Windows Server 2019/2022（64 位） |

### 1.2 依赖清单
| 组件 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.11 | 后端运行时 |
| uv | 最新版 | 后端依赖管理（`uv sync` / `uv run`） |
| Node.js | ≥ 20 LTS | 构建前端 |
| Docker Desktop | 最新版 | 跑 Qdrant / Redis（WSL2 后端） |
| PostgreSQL | 16 | 业务元数据 + SaaS 用户体系 |
| Nginx for Windows | 1.24+ | 静态托管 + API 反代 |

> 若不想在服务器上装 Node，可在**本地** `npm run build` 后只把 `frontend/dist` 拷到服务器。

### 1.3 Windows 防火墙规则
以**管理员身份**打开 PowerShell：

```powershell
# 放行 HTTP / HTTPS（仅当用 Nginx 对公网提供服务时）
New-NetFirewallRule -DisplayName "VDM HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow
New-NetFirewallRule -DisplayName "VDM HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

> **不要**放行 5432 / 6333 / 6334 / 6379 / 8000。这些端口只在主机内部使用。
> 若仅本机/内网访问，可跳过第 1.3 节，只放行内网来源。

---

## 2. 环境安装（winget 方式，可选）

```powershell
# 若未安装 winget，可到 Microsoft Store 安装“应用安装程序”
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Docker.DockerDesktop -e
winget install --id PostgreSQL.PostgreSQL.16 -e

# uv（官方安装脚本）
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 重新打开终端后校验
python --version; node --version; npm --version; uv --version; docker --version
```

安装完成后启动 **Docker Desktop**，等任务栏图标变为运行中再继续（后续 `docker compose` 依赖它）。

---

## 3. 目录规划

本项目在 Windows 上直接以仓库目录为部署目录（示例 `F:\Qdrant-vector`）：

```
F:\Qdrant-vector\
├── docker-compose.yml     # 中间件编排
├── backend\               # 后端代码 + .env + .venv + data\
│   ├── app\
│   ├── .venv\
│   └── data\              # storage / logs / exports / models
└── frontend\
    └── dist\              # 前端构建产物（由 Nginx 托管）
```

---

## 4. 启动中间件（Qdrant / Redis）

项目自带 `docker-compose.yml`（仓库根）。**先做一处安全加固**：把端口改为只绑定本机，避免中间件暴露到局域网。编辑 [docker-compose.yml](docker-compose.yml)，将相关服务的 `ports` 改为：

```yaml
  qdrant:
    ports:
      - '127.0.0.1:6333:6333'
      - '127.0.0.1:6334:6334'
  redis:
    ports:
      - '127.0.0.1:6379:6379'
```

启动（在仓库根目录）：

```powershell
docker compose up -d qdrant redis
docker compose ps
```

> 本机 PostgreSQL 为**原生安装**（Windows 服务，非 Docker），因此这里只起 `qdrant` 和 `redis`。
> 若你更希望 PostgreSQL 也用 Docker，执行 `docker compose up -d postgres`，并注意：
> - compose 里 PostgreSQL 账号为 `vdm_user / vdm_password / vdm_db`，需与后端 `.env` 保持一致；
> - 原生 PostgreSQL 与容器 PostgreSQL **都会抢占 5432**，二选一，不要同时启用。

### 4.1 原生 PostgreSQL 准备

确认服务已启动，并创建数据库（若尚未创建）：

```powershell
# 查看服务
Get-Service -Name postgresql*

# 创建数据库 vdm_db（会提示输入 postgres 用户密码）
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h 127.0.0.1 -c "CREATE DATABASE vdm_db;"
```

> 本机 PostgreSQL 连接信息（与后端 `.env` 一致）：`postgres / root123 @ 127.0.0.1:5432 / vdm_db`。

---

## 5. 安装后端依赖

```powershell
cd F:\Qdrant-vector\backend

# 生成/同步虚拟环境（依据 uv.lock 锁定版本）
uv sync --frozen

# 校验可导入
uv run python -c "import app.main; print('backend import OK')"
```

> 依赖包含 `torch`、`sentence-transformers`，体积较大，首次安装较慢；uv 有缓存，重复部署很快。
> 若 Python 版本过新导致部分包无预编译轮子，可用 uv 指定解释器：`uv python install 3.12` 后 `uv sync --frozen --python 3.12`。

---

## 6. 配置后端环境变量

后端 `.env` 位于 [backend/.env](backend/.env)（已存在则直接编辑；不存在则从模板复制）：

```powershell
cd F:\Qdrant-vector\backend
Copy-Item .env.example .env -ErrorAction SilentlyContinue
# 生成随机 SECRET_KEY
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

生产环境**至少确认以下几项**：

| 变量 | 生产建议值 | 说明 |
|---|---|---|
| `APP_ENV` | `production` | 运行环境 |
| `APP_DEBUG` | `false` | 关闭调试（关闭后 `/docs` 也不再暴露） |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | 由 Nginx 反代，仅本机监听 |
| `WORKERS` | `1` | **必须为 1**，见第 9 节 |
| `SECRET_KEY` | 上一步生成的随机串 | JWT 签名密钥，务必修改 |
| `CORS_ORIGINS` | `http://你的域名或IP` | 同源部署时可收紧；多来源用逗号分隔 |
| `DB_DRIVER` | `postgresql+asyncpg` | 固定 |
| `DB_HOST` / `DB_PORT` | `127.0.0.1` / `5432` | 不要用 `localhost` |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `postgres` / `root123` / `vdm_db` | 与第 4 节实际一致 |
| `CHAT_DB_*` | 与 `DB_*` 相同 | 对话库（可复用同一 PostgreSQL 实例） |
| `QDRANT_HOST` / `QDRANT_PORT` | `127.0.0.1` / `6333` | **必须 127.0.0.1** |
| `REDIS_ENABLED` | `true` | |
| `REDIS_HOST` / `REDIS_PORT` | `127.0.0.1` / `6379` | |
| `STORAGE_ROOT` | `./data/storage` | 相对后端工作目录 |
| `EMBEDDING_MODEL` | `bge-small-zh-v1.5` | 首次启动下载到 `data/models/` |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 国内下载模型加速 |
| `ARK_API_KEY` / `LLM_*` | 按实际填写 | LLM 供应商 Key |
| `SMTP_*` | 按实际填写 | 注册验证码邮件 |
| `LOG_LEVEL` | `INFO` | |

> ⚠️ 常见坑：`.env` 的 `DB_USER/DB_PASSWORD` 与数据库实际账号必须一致；`DB_HOST` / `QDRANT_HOST` / `REDIS_HOST` 一律 `127.0.0.1`。

---

## 7. 构建前端

前端产物为纯静态文件（输出到 `frontend/dist/`），由 Nginx 托管。前端请求统一走 `/api`（同源反代），无需额外配置后端地址。

### 方式 A：服务器上构建

```powershell
cd F:\Qdrant-vector\frontend
npm ci                 # 依据 package-lock.json 安装
npm run build          # vue-tsc 类型检查 + vite build → dist\
```

### 方式 B：本地构建后拷贝（省去服务器 Node）

```powershell
cd F:\Qdrant-vector\frontend
npm ci
npm run build
# 将 dist 内容拷到 Nginx 站点目录
Copy-Item .\dist\*  F:\web\vdm\ -Recurse -Force
```

> 若前后端不同源（例如前端放 OSS/CDN），需在构建前创建 `frontend\.env.production`：
> `VITE_API_BASE_URL=https://你的API域名/api`，再重新 `npm run build`。

---

## 8. 配置 Nginx（Windows）

1. 从 https://nginx.org/en/download.html 下载 Windows 版（zip），解压到例如 `C:\nginx`。
2. 编辑 `C:\nginx\conf\nginx.conf`（Windows 版为单文件配置，默认不包含 `conf.d`）：

```nginx
worker_processes  1;

events {
    worker_connections  1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile        on;
    keepalive_timeout  65;

    server {
        listen       80;
        server_name  _;              # 无域名时填公网 IP 或 _

        # 前端静态目录（注意用正斜杠）
        root   F:/Qdrant-vector/frontend/dist;
        index  index.html;

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
}
```

启动 / 重载 Nginx（在 `C:\nginx` 目录下）：

```powershell
# 首次启动
Start-Process -FilePath .\nginx.exe -WindowStyle Hidden

# 校验配置
.\nginx.exe -t
# 重载
.\nginx.exe -s reload
# 停止
.\nginx.exe -s stop
```

> 也可用 **Caddy for Windows** 作为替代（配置更简单、可自动 HTTPS）：
> ```
> :80 {
>     root * F:/Qdrant-vector/frontend/dist
>     try_files {path} /index.html
>     handle /api/* {
>         reverse_proxy 127.0.0.1:8000 {
>             flush_interval -1
>         }
>     }
>     file_server
> }
> ```

---

## 9. 后端开机自启（Windows 服务）

Windows 没有 systemd，推荐用 **NSSM**（Non-Sucking Service Manager）把后端注册成服务。

```powershell
# 安装 NSSM（任选其一）
winget install --id NSSM.NSSM -e
# 或手动下载 nssm.exe（https://nssm.cc/download）解压后用其完整路径

# 注册服务（在管理员 PowerShell 中执行）
nssm install VDM-Backend "F:\Qdrant-vector\backend\.venv\Scripts\uvicorn.exe" `
  "app.main:app --host 127.0.0.1 --port 8000 --workers 1"
nssm set VDM-Backend AppDirectory "F:\Qdrant-vector\backend"
nssm set VDM-Backend AppStdout    "F:\Qdrant-vector\backend\data\logs\service-out.log"
nssm set VDM-Backend AppStderr    "F:\Qdrant-vector\backend\data\logs\service-err.log"
nssm set VDM-Backend Start SERVICE_AUTO_START

nssm start VDM-Backend
Get-Service VDM-Backend
```

> **为什么必须 `--workers 1`？**
> 应用启动时（[backend/app/main.py](backend/app/main.py) 的 lifespan）会拉起**进程内**的异步任务队列（`start_task_workers`），并把 `running/pending/queued` 的任务重新入队。若起多个 worker，每个进程都会各自拉起 worker 并重复入队，导致**文档解析等任务被重复消费**。并发能力由 FastAPI 的异步 IO 承担，保持单进程即可。

> 若无法使用 NSSM，也可用**任务计划程序**：`taskschd.msc` → 创建任务 → 触发器“计算机启动时” → 操作程序填 `.venv\Scripts\uvicorn.exe`，起始于填 `F:\Qdrant-vector\backend`。

---

## 10. 初始化与首次验证

### 10.1 数据表与迁移
后端启动时会自动执行：
1. 创建主库/对话库数据表（`init_db` / `init_chat_db`）；
2. 运行结构迁移（[backend/app/core/migrations.py](backend/app/core/migrations.py)，均为幂等）。

手动执行迁移（排障时）：

```powershell
cd F:\Qdrant-vector\backend
uv run python -m app.core.migrations
```

### 10.2 Embedding 模型
首次启动会按 `EMBEDDING_MODEL` 下载模型到 `data/models/`（走 `HF_ENDPOINT`）。网络受限时可提前手动下载到该目录，并设置 `EMBEDDING_OFFLINE=true`。

### 10.3 验证清单

```powershell
# 后端服务
Get-Service VDM-Backend

# 健康检查（后端本机）
Invoke-RestMethod http://127.0.0.1:8000/health

# 经由 Nginx
Invoke-RestMethod http://127.0.0.1/api/v1/system/health

# 端口监听（确认 8000/中间件绑定情况）
Get-NetTCPConnection -State Listen -LocalPort 8000,5432,6333,6379 | Select LocalAddress,LocalPort
```

浏览器访问 `http://<本机IP 或域名>`，注册第一个账号。
> 首个注册用户会**自动成为总管理员（super_admin）**，请牢记该账号密码。

---

## 11. 配置 HTTPS（可选，推荐）

Windows 版 Nginx **不带 certbot**，可：
- 使用 **阿里云/腾讯云「数字证书管理服务」** 申请免费证书，下载 Nginx 格式后手工配置 `listen 443 ssl;` 与证书路径；或
- 使用 **Caddy for Windows**，其可自动申请并续期 Let's Encrypt 证书（需公网可达的域名）。

---

## 12. 备份与运维

| 数据 | 位置 / 方式 |
|---|---|
| PostgreSQL | `& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U postgres vdm_db > F:\backup\vdm_db.sql` |
| Qdrant 向量 | Docker 卷 `vdm-qdrant-data`（用 `docker run --rm -v vdm-qdrant-data:/data ...` 打包） |
| Redis | 卷 `vdm-redis-data`（已开启 AOF） |
| 上传文件 | `F:\Qdrant-vector\backend\data\storage` |
| 导出/日志 | `F:\Qdrant-vector\backend\data\exports`、`F:\Qdrant-vector\backend\data\logs` |

- 建议为数据盘**开启 Windows 卷影副本（VSS）**或定期拷贝备份。
- 查看应用日志：`Get-Content F:\Qdrant-vector\backend\data\logs\vdm.log -Wait`，或 NSSM 输出的 `service-out.log` / `service-err.log`。
- 时区：`Set-TimeZone -Id "China Standard Time"`。

---

## 13. 更新发布流程

```powershell
# 后端
cd F:\Qdrant-vector\backend
git pull
uv sync --frozen                 # 依赖有变化时
Restart-Service VDM-Backend

# 前端
cd F:\Qdrant-vector\frontend
git pull
npm ci; npm run build
Copy-Item .\dist\* F:\Qdrant-vector\frontend\dist -Recurse -Force
# 若 Nginx 直接指向该 dist，则无需额外拷贝；否则拷到 Nginx 站点目录
& C:\nginx\nginx.exe -s reload
```

> 数据库结构变更由迁移脚本自动处理，重启后端即生效（迁移幂等）。

---

## 14. 常见问题（FAQ）

| 现象 | 排查方向 |
|---|---|
| 页面 502 Bad Gateway | 后端未启动：`Get-Service VDM-Backend`、查看 `service-err.log` |
| 连不上 Qdrant/Redis/PostgreSQL | `.env` 里的 host 必须是 `127.0.0.1`；`docker compose ps` 确认容器健康；`localhost` 可能走 IPv6 导致失败 |
| Docker 中间件无法从后端连接 | 确认 Docker Desktop 已启动；端口映射改为 `127.0.0.1:xxxx:xxxx` 后重启容器 |
| 端口被占用（5432 等） | 原生 PostgreSQL 与容器 PostgreSQL 二选一；`Get-NetTCPConnection -LocalPort 5432` 查占用进程 |
| 流式回答“卡住后一次性输出” | Nginx 未关闭缓冲：确认 `proxy_buffering off;` |
| 上传大文件失败（413） | 调大 `client_max_body_size`；确认 `proxy_request_buffering off;` |
| Nginx 中文路径/反斜杠报错 | 配置里路径用**正斜杠** `F:/...` |
| PowerShell 里激活 venv 被拦截 | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 后再运行 `.\.venv\Scripts\Activate.ps1` |
| 前端刷新子路由 404 | Nginx 缺少 `try_files $uri $uri/ /index.html;` |
| 文档任务被重复执行 | 检查是否用了 `--workers > 1`，改回 1 |
| 模型下载很慢/失败 | 设置 `HF_ENDPOINT=https://hf-mirror.com`；或离线放置模型 + `EMBEDDING_OFFLINE=true` |
| 依赖 `torch` 安装慢 / 无轮子 | 换用受支持的 Python 版本（如 3.12）；使用国内 PyPI 镜像 |

---

## 附录：一键检查脚本（PowerShell）

```powershell
Write-Host "== docker ==";   docker compose -f F:\Qdrant-vector\docker-compose.yml ps
Write-Host "== backend ==";  Get-Service VDM-Backend | Select Name,Status
Write-Host "== health(api) ==";    Invoke-RestMethod http://127.0.0.1:8000/health
Write-Host "== health(nginx) ==";  Invoke-RestMethod http://127.0.0.1/api/v1/system/health
Write-Host "== ports ==";    Get-NetTCPConnection -State Listen -LocalPort 8000,5432,6379,6333 -ErrorAction SilentlyContinue | Select LocalAddress,LocalPort
```
