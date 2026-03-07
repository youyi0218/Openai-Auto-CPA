# OpenAI Pool Orchestrator

自动化 OpenAI 账号注册、Token 管理与账号池维护的 Web 工具。

## 功能

- Web 可视化注册流程（SSE 实时日志）
- 多邮箱提供商：`mailtm`、`duckmail`、`moemail`、`cloudflare_temp_email`
- 多线程注册（1~10）
- 代理与代理池支持
  - 固定代理：HTTP/SOCKS5
  - 代理池：`zenproxy_api`、`dreamy_socks5_pool`、`docker_warp_socks`
- 平台同步
  - CPA（CLIProxyAPI）
  - Sub2Api
- 账号池维护
  - CPA 自动测活清理
  - Sub2Api 自动维护
  - 本地 Token 定时测活/删号
  - 本地数量不足时自动补号
- Token 列表展示增强：邮箱、Plan、剩余额度、近期 Token 用量、总近期 Token

## 项目结构

```text
openai-pool-orchestrator/
├── openai_pool_orchestrator/
│   ├── server.py
│   ├── register.py
│   ├── pool_maintainer.py
│   ├── mail_providers.py
│   ├── __main__.py
│   └── static/
├── data/
│   ├── sync_config.json
│   ├── state.json
│   └── tokens/
├── config/
│   └── sync_config.example.json
├── deploy/
│   └── cpa/
│       ├── docker-compose.yml
│       ├── config.example.yaml
│       ├── config.yaml
│       └── README.md
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── run.py
```

## 环境要求

- Python 3.10+
- Docker / Docker Compose（容器部署时）
- 可用代理网络（不支持 CN/HK 出口）

## 快速开始（推荐：Docker 一键整合）

### 1) 修改 CPA 密钥

编辑 `deploy/cpa/config.yaml`：

- `remote-management.secret-key`: 改成强随机字符串

### 2) 启动整套服务

```bash
docker compose up -d --build
```

### 3) 访问页面

- `http://localhost:18421`

### 4) 在 Web 中填写 CPA 配置

- `cpa_base_url`: `http://cliproxyapi:8317`
- `cpa_token`: 与 `deploy/cpa/config.yaml` 中 `remote-management.secret-key` 一致

注意：容器内访问 CPA 不能用 `127.0.0.1`，要用服务名 `cliproxyapi`。

## 本地运行（不使用 Docker）

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 初始化配置

```bash
cp config/sync_config.example.json data/sync_config.json
```

### 3) 启动 Web

```bash
python run.py
```

或：

```bash
python -m openai_pool_orchestrator
```

或安装命令行入口：

```bash
pip install -e .
openai-pool
```

## CLI 模式

```bash
python run.py --cli --proxy http://127.0.0.1:7897 --once
python run.py --cli --proxy http://127.0.0.1:7897
python run.py --cli --proxy http://127.0.0.1:7897 --sleep-min 10 --sleep-max 60
```

参数：

- `--proxy`: 代理地址
- `--once`: 只执行一次
- `--sleep-min`: 循环最短等待秒数（默认 5）
- `--sleep-max`: 循环最长等待秒数（默认 30）

## 配置说明（`data/sync_config.json`）

常用字段：

- 注册与并发
  - `multithread`
  - `thread_count`
  - `auto_register`
  - `upload_mode`: `snapshot` / `decoupled`
- 本地池维护
  - `desired_token_count`
  - `local_auto_maintain`
  - `local_maintain_interval_minutes`
  - `local_probe_timeout_seconds`
- 代理池
  - `proxy_pool_enabled`
  - `proxy_pool_provider`: `zenproxy_api` / `dreamy_socks5_pool` / `docker_warp_socks`
  - `proxy_pool_api_url`
  - `proxy_pool_auth_mode`: `query` / `header`（ZenProxy）
  - `proxy_pool_api_key`
  - `proxy_pool_count`
  - `proxy_pool_country`
- CPA
  - `cpa_base_url`
  - `cpa_token`
  - `min_candidates`
  - `used_percent_threshold`
  - `auto_maintain`
  - `maintain_interval_minutes`
- Sub2Api
  - `base_url`
  - `bearer_token`
  - `email`
  - `password`
  - `auto_sync`
  - `sub2api_min_candidates`
  - `sub2api_auto_maintain`
  - `sub2api_maintain_interval_minutes`
- 邮箱
  - `mail_providers`
  - `mail_provider_configs`
  - `mail_strategy`: `round_robin` / `random` / `failover`

完整示例见 `config/sync_config.example.json`。

## CPA 与本地 JSON 同步删除

当 CPA 维护判定账号无效并删除时，系统会同步删除本地 `data/tokens/` 对应 JSON 文件。

## API 一览

主要接口：

- 注册控制
  - `POST /api/start`
  - `POST /api/stop`
  - `GET /api/status`
  - `GET /api/logs`
- 代理
  - `GET /api/proxy`
  - `POST /api/proxy/save`
  - `POST /api/check-proxy`
- 代理池
  - `GET /api/proxy-pool/config`
  - `POST /api/proxy-pool/config`
  - `POST /api/proxy-pool/test`
- Token
  - `GET /api/tokens`
  - `DELETE /api/tokens/{filename}`
- 同步
  - `GET /api/sync-config`
  - `POST /api/sync-config`
  - `POST /api/sync-now`
  - `POST /api/sync-batch`
  - `POST /api/upload-mode`
- 邮箱
  - `GET /api/mail/config`
  - `POST /api/mail/config`
  - `POST /api/mail/test`
- CPA 池
  - `GET /api/pool/config`
  - `POST /api/pool/config`
  - `GET /api/pool/status`
  - `POST /api/pool/check`
  - `POST /api/pool/maintain`
  - `POST /api/pool/auto`
- Sub2Api 池
  - `GET /api/sub2api/pool/status`
  - `POST /api/sub2api/pool/check`
  - `POST /api/sub2api/pool/maintain`

## 常见问题

- 启动后不注册
  - 正常，默认空闲。需要在页面点“启动”。
- 代理可用但仍失败
  - 检查出口地区是否为 CN/HK；更换代理重试。
- 收不到验证码
  - 切换邮箱提供商，或增加等待重试。
- Docker 场景 CPA 连不上
  - `cpa_base_url` 必须用 `http://cliproxyapi:8317`。

## License

[MIT](LICENSE)
