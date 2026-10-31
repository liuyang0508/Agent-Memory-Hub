# 组织记忆 Hub 部署清单（交运维执行）

给准备部署本组织 Hub 的运维人员。按顺序做，每一步都有可验证的结果。
本文描述的是**单租户自建**：一家企业一台机器、一个域名、一套飞书应用。

## 0. 需要运维准备的四样东西

| 项 | 要求 | 为什么 |
|---|---|---|
| 云主机 | Linux，2 核 4G 起，磁盘 50G 起 | 单节点 SQLite，不需要数据库集群 |
| 域名 | 一个 HTTPS 域名，如 `amh.企业域名.com` | 飞书回调必须是 HTTPS，且必须归企业自己 |
| 证书 | 该域名的有效证书（Let's Encrypt 即可） | 同上 |
| 出网 | 能访问 `open.feishu.cn` 和 `accounts.feishu.cn` | 服务端要换 token、查用户信息 |

**不需要**：容器编排、外部数据库、消息队列、Redis。

> 域名归企业自己这一点是硬要求。飞书后台登记的回调地址会和该企业自己的飞书应用绑定；
> 用别人的域名登记，等于把登录能力挂在别人手上。

## 1. 装服务

```bash
# 专用账号与目录
sudo useradd --system --home /var/lib/amh-native --shell /usr/sbin/nologin amh-native
sudo install -d -m 700 -o amh-native -g amh-native /var/lib/amh-native

# 代码与虚拟环境
sudo git clone <仓库地址> /opt/agent-memory-hub
cd /opt/agent-memory-hub
sudo python3.11 -m venv .native-venv
sudo .native-venv/bin/pip install -e '.[web]'

# 服务单元
sudo install -m 644 deploy/native-memory/amh-native.service /etc/systemd/system/amh-native.service
sudo systemctl daemon-reload
sudo systemctl enable --now amh-native
```

**验证**：

```bash
curl -fsS http://127.0.0.1:8787/native/v1/health
# 期望：{"protocol":1,"service":"amh-native"}
curl -fsS http://127.0.0.1:8787/native/v1/ready
# 期望：{"ready":true}
```

## 2. 配 nginx 与证书

把 `deploy/native-memory/nginx-location.conf` 的 `location /native/v1/` 段落，
**加进该域名已有的 HTTPS server 块里**（不要替换原有站点配置）。

**验证**：

```bash
curl -fsS https://amh.企业域名.com/native/v1/health
# 期望：{"protocol":1,"service":"amh-native"}
```

这一步通了，说明「域名 → 证书 → 反代 → 服务」整条链路成立。

## 3. 签发管理令牌

管理令牌只在主机上生成，**从不过 HTTP 传输**，且只显示一次。

```bash
sudo -u amh-native /opt/agent-memory-hub/.native-venv/bin/amh-native admin \
  --db /var/lib/amh-native/hub.sqlite \
  --label "运维姓名"
```

把输出的 `admin` 值交给管理者本人保存（建议进密码管理器）。
丢失后重新签发即可，旧令牌可通过数据库置 `revoked=1` 停用。

## 4. 创建飞书应用（企业自己的飞书租户）

在 `https://open.feishu.cn/app` 建**企业自建应用**，名称建议「多智能体共享组织第二大脑」。

| 位置 | 填什么 |
|---|---|
| 应用能力 | 添加 **网页应用** |
| 桌面端主页 | `https://amh.企业域名.com/native/v1/admin` |
| 打开方式 | 选 **在浏览器中打开**（不是飞书内嵌标签） |
| 安全设置 → 重定向 URL | `https://amh.企业域名.com/native/v1/auth/feishu/callback` |
| 权限管理 | **不需要额外权限**（姓名、open_id、tenant_key 由 user_info 默认返回） |
| 可用范围 | 设为本企业全体成员（或指定部门） |

然后 **创建版本 → 申请发布 → 管理员审核**。
注意：**不发布，回调地址和可用范围都不生效**。

## 5. 填入登录配置

两种方式，**环境变量优先**。推荐用文件，便于审计。

```bash
sudo install -d -m 750 -o root -g amh-native /etc/amh-native
sudo install -m 640 -o root -g amh-native \
  deploy/native-memory/feishu.env.example /etc/amh-native/feishu.env
sudo editor /etc/amh-native/feishu.env     # 填 App ID / App Secret / 公网地址 / 组织标识
sudo systemctl restart amh-native
```

`feishu.env` 的关键三项：

```
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
AMH_HUB_PUBLIC_URL=https://amh.企业域名.com
```

也可以登录管理页
`https://amh.企业域名.com/native/v1/admin` 粘贴令牌后在页面里填，
适合不想上 SSH 的管理者。App Secret 保存后不会再回显。

**验证**：

```bash
curl -fsS https://amh.企业域名.com/native/v1/auth/feishu
# 期望：{"available":true}
```

## 6. 第一个成员

两种方式，任选：

- **飞书登录**：成员在终端执行
  `amh-native login --hub https://amh.企业域名.com`，浏览器授权后自动登记设备。
- **邀请码**：管理页点「生成一次性邀请」，把结果单独发给本人；
  成员执行 `amh-native join --hub https://amh.企业域名.com`。

**验证**：管理页的成员表里出现该成员，设备表里有一条「正常」设备。

## 7. 回滚

```bash
sudo systemctl disable --now amh-native
sudo rm /etc/nginx/conf.d/<新增的 include 或还原备份>
sudo nginx -t && sudo systemctl reload nginx
```

数据留在 `/var/lib/amh-native`，不随服务停用删除。
飞书应用直接在后台停用版本即可，不影响其它系统。

## 8. 日常运维要点

| 事项 | 做法 |
|---|---|
| 备份 | 用 SQLite `Connection.backup()` 做一致快照；只复制主文件会漏 WAL |
| 恢复 | 见 `operations.zh.md`：恢复前要对账设备撤销，并处理客户端游标代次 |
| 监控 | `ready` 持续失败、待传队列增长、磁盘、证书到期 |
| 令牌 | 设备令牌、邀请码、管理令牌、App Secret 都不得写进日志或工单 |
| 升级 | 停服务 → 换代码 → 启服务；管理页配置和数据库不受影响 |

## 9. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `/auth/feishu` 返回 `available:false` | 三项配置缺一 | 检查 `feishu.env` 是否被读到：`systemctl show amh-native -p EnvironmentFiles` |
| 授权后报 `redirect_uri` 不合法 | 后台登记的地址与配置不一致 | 两边逐字符比对，注意结尾斜杠 |
| 授权后报无权限（20010） | 成员不在应用可用范围 | 飞书后台扩大可用范围并重新发布 |
| 管理页一直要求令牌 | 令牌输错或已撤销 | 重新执行第 3 步签发 |
| 登录页打不开 | 域名/证书/反代任一环节 | 先跑第 2 步的 health 验证 |
