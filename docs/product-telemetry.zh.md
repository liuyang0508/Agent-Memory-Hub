# 官网匿名安装与活跃看板

官网的“使用数据”看板展示主动加入统计的匿名安装实例，不代表全部用户。
默认不联网；只有用户执行 `memory telemetry enable` 或安装时显式设置
`AMH_TELEMETRY=1` 才会上报。

## 数据边界

客户端只发送：

- 随机生成的 32 位匿名实例 ID；
- Agent Memory Hub 版本；
- 操作系统、CPU 架构；
- 安装渠道和触发心跳的 Agent 类型；
- 事件类型：`install` 或 `active`。

不发送姓名、账号、IP、主机名、目录、session ID、提示词、对话、MemoryItem、
资源内容或任何 brain pool 数据。服务端只在内存中用 IP 做每分钟限流，不写入
数据库或日志。匿名 ID 在官网展示前还会再做一次 SHA-256 摘要。
Nginx 示例也会关闭这两个接口的 access log。

这是公开、无需客户端密钥的匿名趋势数据，不是实名用户清单，也不能作为计费、
审计或合规证据。服务端用严格字段白名单、请求大小限制和每 IP 内存限流降低
误报与滥用，但无法从密码学上证明每个事件都来自真实安装。

所有成功的客户端出站请求都会写入本机
`$BRAIN_DIR/audit-log/`，可以用 `memory audit outbound` 检查。

```bash
memory telemetry status
memory telemetry enable
memory telemetry disable
```

安装时显式加入统计：

```bash
curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh \
  | AMH_TELEMETRY=1 sh
```

## 服务端部署

遥测 API 是纯 Python 标准库服务，不需要额外安装依赖。官网部署工作流会把
`deploy/telemetry_server.py` 上传到 `$AIHUB_WEB_ROOT/.telemetry/`；首次部署
仍需在官网服务器完成一次 systemd 与 Nginx 配置。

官网生产环境使用独立的低权限 systemd 服务：

```ini
[Unit]
Description=Agent Memory Hub anonymous telemetry API
After=network.target

[Service]
Type=simple
User=amh-telemetry
Group=amh-telemetry
ExecStart=/usr/bin/python3.11 /var/www/agent-memory-hub/current/.telemetry/telemetry_server.py --database /var/lib/agent-memory-hub/telemetry.sqlite3
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/agent-memory-hub

[Install]
WantedBy=multi-user.target
```

将它保存到 `/etc/systemd/system/agent-memory-hub-telemetry.service`，创建
`amh-telemetry` 系统用户及其数据目录后执行：

```bash
systemctl daemon-reload
systemctl enable --now agent-memory-hub-telemetry.service
curl -fsS http://127.0.0.1:8790/healthz
```

把 [`deploy/nginx-telemetry-location.conf`](../deploy/nginx-telemetry-location.conf)
中的两个 `location` 放入官网 HTTPS `server` 块，运行 `nginx -t` 后 reload。
最终验证：

```bash
curl -fsS https://aihub0508.com/api/v1/telemetry/summary
```

后续官网工作流检测到 system service（或兼容的用户级 service）正在运行时，
会在上传新服务端代码后自动 restart；未安装 service 时仍只部署静态官网，
不会误启动后台进程。

## 接口

- `POST /api/v1/telemetry/event`：接收严格白名单 JSON，单请求最大 4 KiB；
- `GET /api/v1/telemetry/summary`：只返回聚合值与二次摘要后的最近实例；
- `GET /healthz`：本机健康检查。

看板每 15 秒刷新一次。服务端以 UTC 聚合 14 天趋势，日明细最多保留 180 天。
官网累计安装卡以 `369` 个历史安装实例作为上线基线，再叠加上线后收到的匿名
opt-in 实例；14 天图中的 `07/14`–`07/20` 展示有明确标识的历史安装基线，
每日为 `18 / 24 / 29 / 37 / 41 / 46 / 41`，合计 `236`，其余日期使用实时事件。
服务端已保留匿名 `active` 事件能力，官网暂不展示活跃指标，待真实 opt-in
数据规模足够后再开放。
