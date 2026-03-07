# CPA 独立部署（CLIProxyAPI + Watchtower）

本目录用于把 CPA（`CLIProxyAPI`）从注册机项目中独立出来运行，并通过 Watchtower 自动更新 CPA 镜像。

## 目录结构

```text
deploy/cpa/
├── docker-compose.yml
├── config.example.yaml
└── README.md
```

## 快速开始

1. 准备配置

```bash
cd deploy/cpa
cp config.example.yaml config.yaml
```

2. 编辑 `config.yaml`

- `remote-management.secret-key`: 改成你自己的强随机字符串
- `port`: 默认 `8317`
- `auth-dir`: 默认 `/root/.cli-proxy-api`

3. 启动（仅 CPA 栈）

```bash
docker compose up -d
```

如果你要启动“注册机 + CPA + Watchtower”整套服务，请在项目根目录执行：

```bash
docker compose up -d --build
```

## 自动更新策略

`docker-compose.yml` 已包含 Watchtower：

- 只更新打了 `com.centurylinklabs.watchtower.enable=true` 标签的容器
- 轮询间隔：300 秒
- 更新后清理旧镜像（`--cleanup`）

如果你不需要自动更新，可停掉 `watchtower` 服务：

```bash
docker compose stop watchtower
```

## 注册机侧配置

在注册机 Web 配置页填写：

- `CPA Base URL`: `http://<CPA主机IP>:8317`
- `CPA Token`: 对应 `config.yaml` 的 `remote-management.secret-key`
- 建议开启 `CPA 自动维护`

这样注册机注册成功后会自动上传账号到 CPA。

## 本地 JSON 同步删除

当前注册机已支持：

- CPA 维护时如果判定账号过期并在 CPA 删除
- 会根据 CPA 返回的删除文件名，同步删除本地 `data/tokens/*.json`

即 CPA 删号和本地 JSON 删号保持一致。
