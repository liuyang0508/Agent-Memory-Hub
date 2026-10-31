"""Standalone native organization-memory CLI and terminal installer."""

from __future__ import annotations

from typing import Any

import json
import os
import platform
import socket
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .client import NativeClient
from .common import PRODUCTS, atomic_json, display_name
from .storage import database
from .install import (
    discover as discover_products,
    discover_assets,
    install_hooks,
    uninstall_hooks,
    export_cowork_plugin,
    launch_agent,
)

app = typer.Typer(help="AMH 组织原生记忆：独立身份、同步、云端召回和原生读取")
console = Console()
DEFAULT_STATE = Path.home() / ".amh-native-sync"


def emit(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def load(state: Path) -> NativeClient:
    if not (state / "config.json").exists():
        raise typer.BadParameter("尚未加入组织，请先运行 setup 或 join")
    return NativeClient(state)


@app.command()
def invite(
    db: Path = typer.Option(...),
    tenant: str = "linkcrux",
    name: str = typer.Option(...),
    member_id: str | None = None,
    ttl: int = 86400,
) -> None:
    """管理员在 Hub 主机创建一次性成员邀请；不发送给任何人。"""
    from .hub import Hub

    emit(
        {
            "invite": Hub(db).invite(tenant, name, member_id=member_id, ttl=ttl),
            "tenant": tenant,
            "member": name,
        }
    )


@app.command()
def admin(db: Path = typer.Option(...), label: str = typer.Option(...)) -> None:
    """在 Hub 主机签发一个管理令牌，用于 /native/v1/admin 页面；只显示这一次。"""
    from .hub import Hub

    emit({"admin": Hub(db).admin_create(label), "label": label, "note": "只显示一次"})


@app.command()
def members(db: Path = typer.Option(...)) -> None:
    """管理员查看成员稳定 ID，以便为同一成员登记新设备。"""
    from .hub import Hub

    Hub(db)
    with database(db) as connection:
        emit([dict(r) for r in connection.execute("SELECT id,tenant,name FROM members")])


@app.command()
def revoke(db: Path = typer.Option(...), device: str = typer.Option(...)) -> None:
    from .hub import Hub

    Hub(db).revoke(device)
    emit({"revoked": device})


@app.command()
def serve(db: Path = typer.Option(...), host: str = "127.0.0.1", port: int = 8787) -> None:
    """运行独立组织 Hub；公网部署请在前面配置 HTTPS。"""
    import uvicorn
    from .api import create_app
    from .hub import Hub

    uvicorn.run(create_app(Hub(db)), host=host, port=port, log_level="warning", access_log=False)


@app.command()
def discover(home: Path = typer.Option(Path.home())) -> None:
    emit(discover_products(home))


@app.command()
def join(
    hub: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
    device_name: str = socket.gethostname(),
) -> None:
    """使用个人邀请登记设备；邀请不会出现在进程命令参数中。"""
    secret = os.environ.get("AMH_NATIVE_INVITE") or typer.prompt("成员邀请凭证", hide_input=True)
    c = NativeClient.join(state, hub, secret, device_name)
    emit({k: v for k, v in c.identity.items() if k != "token"})


@app.command()
def login(
    hub: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
    device_name: str = socket.gethostname(),
    timeout: float = 300,
) -> None:
    """飞书登录加入组织：浏览器授权后自动登记设备，无需邀请码。"""
    import http.server
    import threading
    import webbrowser

    from .transport import Transport, validate_url

    url = validate_url(hub)
    transport = Transport(url)
    available = transport.call("auth/feishu")
    if not available.get("available"):
        raise typer.BadParameter("该 Hub 未启用飞书登录，请改用 join 邀请凭证")

    holder: dict[str, str] = {}
    ready = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(self.path).query)
            if "ticket" in query:
                holder["ticket"] = query["ticket"][0]
                body = "登录完成，可以关闭此页面，回到终端。".encode()
                ready.set()
            else:
                body = "缺少登录票据，请回到终端重试。".encode()
            self.send_response(200 if "ticket" in query else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = transport.call("auth/feishu/start", {"port": port})
        console.print("在浏览器完成飞书授权：" + started["authorize_url"])
        webbrowser.open(started["authorize_url"])
        if not ready.wait(timeout):
            raise typer.BadParameter("等待授权超时，请重新运行 login")
    finally:
        server.shutdown()

    identity = transport.call(
        "auth/feishu/redeem", {"ticket": holder["ticket"], "device_name": device_name}
    )
    NativeClient.adopt(state, url, identity, device_name)
    emit({k: v for k, v in identity.items() if k != "token"})


@app.command()
def bind(
    root: Path = typer.Option(...),
    product: str = typer.Option(...),
    project: str = "",
    kind: str = "memory",
    state: Path = DEFAULT_STATE,
    native_target: bool = True,
) -> None:
    """绑定真实源目录。已有内容默认不上传，请 preview/select。"""
    c = load(state)
    sid = c.register(root, product, project, kind)
    if kind == "memory" and native_target:
        add_target(c, root, product)
    emit({"source_id": sid, "history": "excluded until selected"})


@app.command("import")
def import_history(
    state: Path = DEFAULT_STATE,
    home: Path = typer.Option(Path.home()),
    product: list[str] = typer.Option(None, help="只处理指定产品，可重复"),
    kind: list[str] = typer.Option(None, help="只处理指定类型，可重复"),
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    """导入安装前的历史记忆：逐条列出可同步内容，由你勾选，此后新增自动同步。"""
    from . import history

    # Listing is purely local; requiring an enrolled profile would block the
    # review a member wants before deciding whether to join at all.
    chosen_products = product or list(PRODUCTS)
    rows = history.items(home, chosen_products)
    if kind:
        wanted = set(kind)
        rows = [row for row in rows if row["kind"] in wanted]
    if not rows:
        emit({"imported": 0, "note": "未发现可导入的本地历史"})
        return
    current = None
    group_label = ""
    for index, row in enumerate(rows, start=1):
        label = f"{row['product_name']} · {display_name(row['kind'])}"
        if label != group_label:
            console.print(f"\n[bold]{label}[/bold]", markup=True)
            group_label = label
        marks = ""
        if row["risk"] == "sensitive":
            marks = "  ⚠ " + "、".join(row["reasons"])
        console.print(
            f"  {index:3d}. {row['name']} — {row['title']} "
            f"({row['bytes'] / 1024:.0f} KB){marks}",
            markup=False,
        )
    console.print(
        "\n⚠ 标记的文件命中了手机号、身份证、银行卡、邮箱或工资/病历等措辞。"
        "勾选它们意味着这些内容会上传并被全组织检索引擎读到。",
        markup=False,
    )
    console.print(
        "输入编号勾选（如 1,3,5-9）；clean 选全部无标记项；all 选全部；直接回车跳过。",
        markup=False,
    )
    if dry_run:
        emit(rows)
        return
    answer = "all" if yes else typer.prompt("要同步哪些？", default="")
    answer = answer.strip().lower()
    if answer in ("", "none"):
        emit({"imported": 0, "note": "未选择任何文件"})
        return
    if answer == "all":
        picked = rows
    elif answer == "clean":
        picked = [row for row in rows if row["risk"] == "clean"]
    else:
        picked_indices: set[int] = set()
        try:
            for part in answer.replace("，", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    start, end = part.split("-", 1)
                    picked_indices.update(range(int(start), int(end) + 1))
                else:
                    picked_indices.add(int(part))
        except ValueError:
            raise typer.BadParameter("编号格式不正确，例如 1,3,5-9") from None
        if any(i < 1 or i > len(rows) for i in picked_indices):
            raise typer.BadParameter("编号超出范围")
        picked = [rows[i - 1] for i in sorted(picked_indices)]
    risky = sum(1 for row in picked if row["risk"] == "sensitive")
    console.print(
        f"将上传 {len(picked)} 个文件到组织云端，其中 {risky} 个带隐私标记。"
        "上传后其他成员可检索正文。",
        markup=False,
    )
    if not typer.confirm("确认同步这些记忆？", default=False):
        emit({"imported": 0, "note": "已取消"})
        return
    current = None if dry_run and not (state / "config.json").exists() else load(state)
    if current is None:
        raise typer.BadParameter("请先完成登录或加入组织，再执行导入")
    result = history.import_paths(current, home, picked)
    pushed = current.sync()
    emit({"import": result, "sync": pushed})


def add_target(c: NativeClient, root: Path, product: str) -> None:
    entry = {"root": str(root.expanduser().resolve()), "product": product}
    targets = c.config.setdefault("targets", [])
    if not any(t["root"] == entry["root"] for t in targets):
        targets.append(entry)
    atomic_json(c.root / "config.json", c.config)


@app.command()
def preview(
    source: str = typer.Option(...), state: Path = DEFAULT_STATE, details: bool = False
) -> None:
    """本地预览；不发网络请求。"""
    rows = load(state).preview(source)
    emit(rows if details else [{k: v for k, v in r.items() if k != "body"} for r in rows])


@app.command()
def select(
    source: str = typer.Option(...), manifest: Path = typer.Option(...), state: Path = DEFAULT_STATE
) -> None:
    """导入预览生成的 path→hash 选择清单，变更后必须重新预览。"""
    load(state).select(source, json.loads(manifest.read_text()))
    emit({"selected": True, "upload": "next sync"})


@app.command()
def sync(state: Path = DEFAULT_STATE) -> None:
    emit(load(state).sync())


@app.command()
def search(query: str, state: Path = DEFAULT_STATE, project: str | None = None) -> None:
    """调试统一云检索，默认全组织；--project 为显式过滤。"""
    emit(load(state).search(query, project_filter=project))


@app.command()
def status(state: Path = DEFAULT_STATE) -> None:
    c = load(state)
    summary = {
        "identity": {k: v for k, v in c.identity.items() if k != "token"},
        "hub": c.config.get("hub"),
        "sources": c.sources(),
        "targets": c.targets(),
        "pending": c.pending_count(),
        "origin_reviews": c.origin_reviews(),
    }
    if (state / "status.json").exists():
        summary["worker"] = json.loads((state / "status.json").read_text())
    emit(summary)


@app.command()
def worker(state: Path = DEFAULT_STATE, interval: float = 10) -> None:
    """持久后台 worker：Hook 唤醒 + 周期补扫原生延迟生成。"""
    from .worker import work

    load(state)
    work(state, interval)


@app.command()
def pause(state: Path = DEFAULT_STATE) -> None:
    load(state)
    (state / "paused").touch()
    emit({"paused": True})


@app.command()
def resume(state: Path = DEFAULT_STATE) -> None:
    load(state)
    (state / "paused").unlink(missing_ok=True)
    (state / "wake").touch()
    emit({"paused": False})


@app.command("cowork-plugin")
def cowork_plugin(output: Path = typer.Option(...)) -> None:
    emit(
        {
            "plugin": str(export_cowork_plugin(output)),
            "archive": str(output) + ".zip",
            "status": "package ready; import and bind state in actual Cowork environment",
        }
    )


@app.command("cowork-connect")
def cowork_connect(
    output: Path = typer.Option(..., help="无凭证的成员绑定插件输出目录"),
    connection_dir: Path = typer.Option(..., help="Cowork任务可访问的私有配对文件目录"),
    state: Path = DEFAULT_STATE,
    runtime_state: Path | None = typer.Option(
        None, help="宿主机 Hook 的持久状态目录；指定后同时固定独立配对文件路径"
    ),
) -> None:
    """生成成员绑定插件和独立的一次性连接文件；不修改日常客户端配置。"""
    from .cowork_runtime import PAIRING_FILENAME, export_connection

    output = output.expanduser().resolve()
    connection_dir = connection_dir.expanduser().resolve()
    if connection_dir == output or connection_dir.is_relative_to(output):
        raise typer.BadParameter("连接文件必须位于插件包目录之外")
    connection_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = connection_dir / PAIRING_FILENAME
    policy = export_connection(state, connection)
    plugin = export_cowork_plugin(
        output,
        policy=policy,
        runtime_state=runtime_state.expanduser().resolve() if runtime_state else None,
        connection_file=connection if runtime_state else None,
    )
    emit(
        {
            "plugin": str(plugin) + ".zip",
            "connection_file": str(connection),
            "member_id": policy["member_id"],
            "expires_in_seconds": 900,
            "status": "pairing prepared; actual Cowork runtime must load the plugin and access the connection file",
        }
    )


@app.command()
def uninstall(state: Path = DEFAULT_STATE, home: Path = typer.Option(Path.home())) -> None:
    """恢复仍未被用户修改的配置；保留同步数据和原生记忆。"""
    if platform.system() == "Darwin":
        p = launch_agent(state, home, install=False)
        if p.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], capture_output=True
            )
            p.unlink()
    emit({"conflicts": uninstall_hooks(home, state), "data": "preserved"})


@app.command()
def setup(
    hub: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
    home: Path = typer.Option(Path.home()),
    service: bool = True,
) -> None:
    """终端分步安装：检查产品、绑定身份、预览历史、确认配置。"""
    console.print("[bold]AMH · 组织共享第二大脑[/bold]")
    found = discover_products(home)
    table = Table("产品", "检测结果", "原生记忆目录")
    for p in found:
        table.add_row(
            p["name"],
            "已发现" if p["detected"] else "未发现",
            "\n".join(p["roots"]) or "需要在产品运行环境绑定",
        )
    console.print(table)
    if not (state / "config.json").exists():
        console.print("组织服务地址：" + hub)
        secret = os.environ.get("AMH_NATIVE_INVITE") or typer.prompt(
            "个人邀请凭证", hide_input=True
        )
        c = NativeClient.join(state, hub, secret, socket.gethostname())
    else:
        c = load(state)
        if c.config["hub"] != hub.rstrip("/"):
            raise typer.BadParameter("不同 Hub 必须使用独立 --state；不能把旧队列发给新组织")
    console.print(
        f"成员：{c.identity['member_name']} ({c.identity['member_id']})；组织：{c.identity['tenant_id']}"
    )
    chosen = []
    processed_sources: set[str] = set()
    for product in found:
        if not product["detected"] or not typer.confirm(
            "接入 " + product["name"] + "？", default=True
        ):
            continue
        chosen.append(product["product"])
        if product["product"] == "claude-code":
            watcher = {"product": "claude-code", "root": str((home / ".claude/projects").resolve())}
            watchers = c.config.setdefault("discovery_roots", [])
            if watcher not in watchers:
                watchers.append(watcher)
                atomic_json(c.root / "config.json", c.config)
        bindings = [{"root": raw, "kind": "memory"} for raw in product["roots"]]
        bindings += discover_assets(home, product["product"])
        for binding in bindings:
            root = Path(binding["root"])
            kind = binding["kind"]
            if kind == "memory":
                root.mkdir(parents=True, exist_ok=True)
            source_product = (
                "codex-shared"
                if product["product"] in ("codex-cli", "codex-desktop", "chatgpt-desktop")
                else product["product"]
            )
            sid = c.register(root, source_product, "", kind)
            if kind == "memory":
                add_target(c, root, product["product"])
            if sid in processed_sources:
                continue
            processed_sources.add(sid)
            console.print(f"新内容同步来源：{kind} · {root}", markup=False)
            rows = c.preview(sid)
            if rows:
                console.print(f"{root}：{len(rows)} 个已有文件。生产者无法证明的历史将标为未知。")
                if typer.confirm("逐项预览并选择导入？", default=False):
                    selection = {}
                    for row in rows:
                        console.print(f"\n{row['path']} · {row['bytes']} bytes", markup=False)
                        if typer.confirm("显示完整正文？", default=False):
                            console.print(row["body"], markup=False)
                        if typer.confirm("共享此项给组织全体成员？", default=False):
                            selection[row["path"]] = row["hash"]
                    if selection and typer.confirm(
                        f"确认导入所选 {len(selection)} 项？", default=False
                    ):
                        c.select(sid, selection)
    console.print("接入产品：" + "、".join(PRODUCTS[p] for p in chosen))
    console.print(
        "将启用：产品原生记忆、后台上传、Hook 组织召回、本地原生组织资源。原生生成由产品执行，可能使用产品额度；已有 AMH 托管自动链路将隔离。"
    )
    if not typer.confirm("确认配置并启用？", default=False):
        console.print("已登记身份与选择；尚未安装 Hook 或启动上传。")
        return
    cowork_artifacts: tuple[Path, Path] | None = None
    if "cowork" in chosen:
        from .cowork_runtime import PAIRING_FILENAME, export_connection

        connection = state / "cowork-connection" / PAIRING_FILENAME
        try:
            policy = export_connection(state, connection)
            host_runtime = platform.system() == "Darwin"
            plugin = export_cowork_plugin(
                state / "cowork-plugin",
                policy=policy,
                runtime_state=(state / "cowork-runtime").resolve() if host_runtime else None,
                connection_file=connection.resolve() if host_runtime else None,
            )
            cowork_artifacts = plugin, connection
        except (OSError, ValueError) as exc:
            console.print(f"Cowork 配对准备失败（{type(exc).__name__}）；尚未修改客户端配置。")
            raise typer.Exit(1) from None
    install_hooks(home, state, products=chosen)
    if cowork_artifacts:
        plugin, connection = cowork_artifacts
        console.print(
            f"Cowork 插件：{plugin}.zip；一次性连接文件：{connection}（15分钟有效，不能放进插件包）。"
        )
        console.print(
            "在 Cowork 导入插件。macOS 宿主机 Hook 已固定持久状态和连接文件路径；其他运行环境需让首次任务访问连接文件所在目录。运行时会独立登记设备、发现原生记忆目录；实际接入状态仍需核验。"
        )
    if service and platform.system() == "Darwin":
        plist_path = launch_agent(state, home)
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)], capture_output=True
        )
        if result.returncode:
            console.print("后台服务启动失败，请运行 worker；详情可用 status 查看。")
    console.print(
        "本地配置完成。请新建会话进入组织模式，已打开会话仍可能保留原上下文；用 status 查看同步，产品实际读取须通过独立验收。"
    )


@app.command("export-assets")
def export_assets(
    destination: Path = typer.Option(...),
    kind: list[str] = typer.Option(...),
    state: Path = DEFAULT_STATE,
) -> None:
    """无损导出共享聊天、规则、skills、可迁移配置，保留来源清单。"""
    from .materialize import materialize

    emit(materialize(destination, load(state).cached(), kinds=set(kind)))


@app.command("activate-skills")
def skills_activate(
    destination: Path = typer.Option(...),
    source_store: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
) -> None:
    """将明确选择的组织技能装入产品原生 skills 目录。"""
    from .assets import activate_skills

    rows = [
        r
        for r in load(state).cached()
        if r["source_store"] == source_store and r["kind"] == "skill"
    ]
    emit(
        [
            {
                "path": r["path"],
                "producer": r["producer_name"],
                "member_id": r["producer_member_id"],
            }
            for r in rows
        ]
    )
    if typer.confirm("确认将所选组织技能应用到目录（空集合会停用已托管技能）？", default=False):
        emit(activate_skills(destination, rows, source_store=source_store))


@app.command("activate-rules")
def rules_activate(
    instructions: Path = typer.Option(...),
    source_store: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
) -> None:
    """把已选择规则写入指定项目 CLAUDE.md / AGENTS.md 托管区块。"""
    from .assets import activate_rules

    rows = [
        r for r in load(state).cached() if r["source_store"] == source_store and r["kind"] == "rule"
    ]
    emit([{"path": r["path"], "producer": r["producer_name"], "body": r["body"]} for r in rows])
    if rows and typer.confirm("确认用所选规则替换指定项目的组织规则托管区块？", default=False):
        emit(activate_rules(instructions, rows))


@app.command("apply-config")
def config_apply(
    asset: str = typer.Option(...),
    destination: Path = typer.Option(...),
    product: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
) -> None:
    """选择配置资产并确认后，映射同产品族已支持字段。其余字段保留在导出文件。"""
    from .assets import apply_config, provenance
    from .materialize import assembled

    rows = [
        r
        for r in assembled(load(state).cached())
        if r["asset_id"] == asset and r["kind"] == "config"
    ]
    if len(rows) != 1:
        raise typer.BadParameter("configuration asset not found or incompletely downloaded")
    console.print(provenance(rows[0]), markup=False)
    console.print(rows[0]["body"], markup=False)
    if typer.confirm("确认将兼容字段应用到指定配置文件？", default=False):
        emit(apply_config(destination, rows[0], product))


def main() -> None:
    try:
        app()
    except (ValueError, PermissionError, OSError) as error:
        console.print(str(error), markup=False, style="red")
        raise SystemExit(1) from None
