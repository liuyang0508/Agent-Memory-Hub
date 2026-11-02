"""Standalone native organization-memory CLI and terminal installer."""

from __future__ import annotations

from typing import Any, Callable, Iterator, cast

from contextlib import contextmanager

import json
import os
import platform
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .client import NativeClient
from . import source_policy
from .common import PRODUCTS, PAIRING_MAX_TTL, atomic_json, display_name
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


@contextmanager
def operation_progress() -> Iterator[Callable[[str, int, int | None], None]]:
    """Render counts on stderr; callbacks contain no source paths, bodies or credentials."""
    output = Console(stderr=True)
    labels = {
        "prepare": "准备历史文件（本地）",
        "check": "检查所选文件（本地，尚未上传）",
        "scan": "扫描待同步内容",
        "connect": "连接组织服务",
        "upload": "上传到组织（云端已确认）",
        "refresh": "更新同步状态",
    }
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.fields[count]}"),
        TimeElapsedColumn(),
        console=output,
        disable=not output.is_terminal,
    ) as ui:
        task = ui.add_task("", total=None, count="")
        previous = ""

        def report(stage: str, completed: int, total: int | None) -> None:
            nonlocal previous
            count = f"{completed}/{total}" if total is not None else ""
            if not output.is_terminal and (
                stage != previous or total is not None and completed == total
            ):
                output.print(labels[stage], count, markup=False)
            if stage != previous:
                ui.reset(
                    task, description=labels[stage], completed=completed, total=total, count=count
                )
            else:
                ui.update(task, completed=completed, total=total, count=count)
            previous = stage

        yield report


def sync_with_progress(client: NativeClient) -> dict[str, Any]:
    with operation_progress() as report:
        return client.sync(progress=report)


def initial_sync(client: NativeClient) -> bool:
    """Show the first upload before starting the service; never discard failed work."""
    console.print("\n首次同步：上传进度按云端确认的内容条目计数。")
    try:
        result = sync_with_progress(client)
    except (OSError, ValueError, sqlite3.Error) as exc:
        console.print(
            f"首次同步尚未完成（{type(exc).__name__}）。选择和待上传内容已保留；"
            "恢复连接或处理错误后，可运行 amh-native sync 重试。",
            markup=False,
        )
        return False
    if result.get("busy"):
        console.print("后台正在同步，未启动第二次上传；用 amh-native status 查看结果。")
        return False
    total_pending = client.pending_count()
    paused = client.source_scope()["paused_pending"]
    pending = total_pending - paused
    if paused:
        console.print(
            f"另有 {paused} 项因同步范围暂停，队列已保留；用 amh-native scope 查看或恢复。"
        )
    if pending or result.get("errors"):
        console.print(
            f"本轮仍有 {pending} 项待上传，{len(result.get('errors', []))} 项需检查；"
            "用 amh-native status 查看详情。",
            markup=False,
        )
        return False
    console.print("首次同步完成：当前启用范围待上传 0 项。")
    return True


def load(state: Path) -> NativeClient:
    if not (state / "config.json").exists():
        raise typer.BadParameter("尚未加入组织，请先运行 setup")
    return NativeClient(state)


def require_research_service(hub: str) -> None:
    """Check capabilities before enrollment or installing tools against an older Hub."""
    from .transport import Transport

    try:
        response = Transport(hub, timeout=5).call("health")
    except (OSError, ValueError):
        raise typer.BadParameter(
            "暂时无法验证组织服务能力；尚未更改客户端配置，请稍后重试"
        ) from None
    capabilities = response.get("capabilities") if isinstance(response, dict) else None
    if (
        not isinstance(response, dict)
        or response.get("service") != "amh-native"
        or not isinstance(capabilities, list)
        or "org-research-v1" not in capabilities
    ):
        raise typer.BadParameter("组织服务尚未支持按需检索工具，请管理员先升级 Hub，再继续安装")


def all_selectable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every row except blocked ones: a blocked file can never be uploaded."""
    picked = [row for row in rows if row["risk"] != "blocked"]
    dropped = len(rows) - len(picked)
    if dropped:
        console.print(f"含密钥的 {dropped} 个文件一律不上传，已从 all 中排除。", markup=False)
    return picked


@app.command()
def admin(db: Path = typer.Option(...), label: str = typer.Option(...)) -> None:
    """在 Hub 主机签发一个管理令牌，用于 /native/v1/admin 页面；只显示这一次。"""
    from .hub import Hub

    emit({"admin": Hub(db).admin_create(label), "label": label, "note": "只显示一次"})


@app.command("scope-feishu-identities")
def scope_feishu_identities(
    db: Path = typer.Option(...),
    app_id: str = typer.Option(...),
    tenant_key: str = typer.Option(...),
) -> None:
    """升级1.3.1：管理员核对旧应用和企业标识后，将已有飞书身份迁移到明确作用域。"""
    from .hub import Hub

    emit({"migrated": Hub(db).scope_legacy_feishu(app_id, tenant_key)})


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
def serve(
    db: Path = typer.Option(...),
    host: str = "127.0.0.1",
    port: int = 8787,
    semantic_model: str | None = typer.Option(None, help="服务端已缓存的语义模型，不自动下载"),
    semantic_min_score: float = typer.Option(0.45, min=0, max=1),
    mmr_lambda: float | None = typer.Option(None, min=0, max=1),
    decay: bool = typer.Option(True, "--decay/--no-decay", help="按知识性质、可信时间和反馈信号做 ±5% 重排"),
    hopfield: bool = typer.Option(False, "--hopfield/--no-hopfield", help="启用 Hopfield 联想扩召（需语义索引，全量扫描）"),
    hopfield_beta: float = typer.Option(8.0, min=0.1, max=64),
    hopfield_min_score: float = typer.Option(0.35, min=0, max=1, help="联想候选的相似度下限"),
) -> None:
    """运行独立组织 Hub；公网部署请在前面配置 HTTPS。"""
    import uvicorn
    from .api import create_app
    from .hub import Hub

    hub = Hub(
        db,
        semantic_model=semantic_model,
        semantic_min_score=semantic_min_score,
        mmr_lambda=mmr_lambda,
        decay=decay,
        hopfield=hopfield,
        hopfield_beta=hopfield_beta,
        hopfield_min_score=hopfield_min_score,
    )
    if hub.semantic_model:
        # Load before accepting requests: a cold model must not consume the
        # short Hook/tool request deadline. No source content is encoded here.
        output = Console(stderr=True)
        output.print("正在加载服务端本地语义模型；此阶段尚未接受检索请求。")
        backend, error = hub._semantic_backend()
        if backend is None:
            output.print(f"语义模型未就绪（{error}）；检索将明确报告词法降级。", markup=False)
    uvicorn.run(create_app(hub), host=host, port=port, log_level="warning", access_log=False)


@app.command("semantic-index")
def semantic_index(
    db: Path = typer.Option(..., help="已有 Hub 数据库，仅在服务端维护"),
    model: str = typer.Option(..., help="已缓存的模型名称或目录；不会下载"),
    state: Path | None = typer.Option(None, help="已授权组织的私有客户端状态目录"),
    kind: list[str] | None = typer.Option(None, help="索引类型，可重复；默认仅记忆"),
    include_evidence: bool = typer.Option(False, help="明确包含会话摘要和原始提取材料"),
    watch: bool = typer.Option(False, help="周期检查新增、更新和删除，仅编码变化内容"),
    interval: float = typer.Option(60, min=5, max=86400),
) -> None:
    """构建或增量刷新派生语义索引；凭证从 --state 或 AMH_NATIVE_INDEX_TOKEN 读取。"""
    from .hub import Hub

    if not db.is_file() or db.is_symlink():
        raise typer.BadParameter("--db 必须指向已有的普通 Hub 数据库文件")
    token = os.environ.get("AMH_NATIVE_INDEX_TOKEN", "")
    if state is not None:
        config_path = state / "config.json"
        try:
            if state.is_symlink() or config_path.is_symlink() or config_path.stat().st_size > 16384:
                raise ValueError("invalid profile")
            profile = json.loads(config_path.read_text())
            token = profile["identity"]["token"]
        except (OSError, ValueError, TypeError, KeyError):
            raise typer.BadParameter("无法读取已授权的私有状态目录") from None
    if not isinstance(token, str) or not token:
        raise typer.BadParameter(
            "请指定 --state 或设置 AMH_NATIVE_INDEX_TOKEN；不要把凭证放进命令参数"
        )
    hub = Hub(db, semantic_model=model)
    try:
        while True:
            # The Hub revalidates this device before inference and before commit.
            result = hub.rebuild_semantic(token, kinds=kind, include_evidence=include_evidence)
            emit({"semantic_index": result, "watch": watch})
            if not watch:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        Console(stderr=True).print("索引维护已停止；已提交的索引保留。")
    except PermissionError:
        raise typer.BadParameter("组织凭证无效或已撤销，索引维护已停止") from None
    except (OSError, ValueError, sqlite3.Error):
        raise typer.BadParameter(
            "索引构建失败；检查已缓存模型、服务端依赖和数据库。旧索引及原始内容保留。"
        ) from None


@app.command()
def discover(home: Path = typer.Option(Path.home())) -> None:
    emit(discover_products(home))


def browser_mode(ssh: bool | None, web_port: int) -> tuple[bool, int]:
    """One loopback port and one tunnel for sequential login and selection pages."""
    remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")) if ssh is None else ssh
    port = web_port or (8765 if remote else 0)
    if remote:
        console.print(
            f"SSH 安装：在你本机另开终端，保持端口转发运行：\n"
            f"  ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:{port}:127.0.0.1:{port} <你的 SSH 别名>\n"
            f"使用 remote-claude 时也可运行：remote-claude forward {port}\n"
            f"多连接配置请加远端别名。本机和远端端口必须相同；授权和历史选择共用此端口。\n"
            f"稍后在本机浏览器打开 http://127.0.0.1:{port}/，飞书无需在远端登录。",
            markup=False,
        )
        if not typer.confirm("端口转发已启动，继续？", default=True):
            raise typer.Exit()
    return remote, port


def feishu_login(
    hub: str, state: Path, device_name: str, timeout: float = 300,
    *, port: int = 0, open_browser: bool = True,
) -> dict[str, Any]:
    """Authorize in the browser, then write the profile; shared by setup and login.

    The browser only ever receives a one-use ticket addressed to this machine's
    loopback port. The reusable device credential is fetched by this process
    from the Hub over HTTPS, so no browser URL or history entry can carry it.
    """
    import http.server
    import threading
    import webbrowser

    from .transport import Transport, validate_url

    url = validate_url(hub)
    transport = Transport(url)
    available = transport.call("auth/feishu")
    if not available.get("available"):
        raise typer.BadParameter("该 Hub 未启用飞书登录；请让管理员在管理页完成飞书应用配置后重试")

    holder: dict[str, str] = {}
    ready = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(self.path).query)
            if not query and self.path == "/" and holder.get("authorize_url"):
                self.send_response(302)
                self.send_header("Location", holder["authorize_url"])
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if "ticket" in query:
                holder["ticket"] = query["ticket"][0]
                body = "登录完成，可以关闭此页面，回到终端。".encode()
                ready.set()
            else:
                body = "缺少登录票据，请回到终端重试。".encode()
            self.send_response(200 if "ticket" in query else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise typer.BadParameter(
            f"无法监听本机端口 {port}：{exc}。请使用 --web-port 更换端口，并同步调整本机转发。"
        ) from None
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = transport.call("auth/feishu/start", {"port": port})
        holder["authorize_url"] = started["authorize_url"]
        if open_browser:
            console.print("在浏览器完成飞书授权：" + started["authorize_url"], markup=False)
            webbrowser.open(started["authorize_url"])
        else:
            console.print(f"在本机浏览器完成飞书授权：http://127.0.0.1:{port}/", markup=False)
        if not ready.wait(timeout):
            raise typer.BadParameter("等待授权超时，请检查端口转发后重新运行；不要复用旧授权页面")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    identity = transport.call(
        "auth/feishu/redeem", {"ticket": holder["ticket"], "device_name": device_name}
    )
    NativeClient.adopt(state, url, identity, device_name)
    return cast(dict[str, Any], identity)


@app.command()
def login(
    hub: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
    device_name: str = socket.gethostname(),
    timeout: float = 300,
    ssh: bool | None = typer.Option(None, "--ssh/--no-ssh", help="本机浏览器经端口转发授权；默认检测 SSH"),
    web_port: int = typer.Option(0, min=0, max=65535, help="浏览器端口；SSH 默认 8765，本机默认随机"),
) -> None:
    """飞书登录加入组织：浏览器授权后自动登记设备。"""
    remote, port = browser_mode(ssh, web_port)
    identity = feishu_login(hub, state, device_name, timeout, port=port, open_browser=not remote)
    emit({k: v for k, v in identity.items() if k != "token"})


def show_source_scope(c: NativeClient) -> None:
    scope = c.source_scope()
    console.print(
        "当前自动同步类型："
        + ("、".join(display_name(k) for k in scope["enabled_kinds"]) or "全部暂停"),
        markup=False,
    )
    console.print(scope["note"], markup=False)
    console.print(scope["preservation"], markup=False)


def confirm_history_scope(c: NativeClient, picked: list[dict[str, Any]]) -> bool:
    current = source_policy.enabled(c.config)
    added = source_policy.normalize({r["kind"] for r in picked} - set(current))
    if added:
        console.print(
            "所选文件还需启用以下类型的后续自动同步：" + "、".join(display_name(k) for k in added),
            markup=False,
        )
        if not typer.confirm("确认启用这些类型并导入所选历史？", default=False):
            return False
        c.set_source_scope(current + added)
    return True


@app.command("scope")
def configure_scope(
    state: Path = DEFAULT_STATE,
    kind: list[str] = typer.Option(None, help="新的完整同步范围，可重复；省略则只预览"),
    none: bool = typer.Option(False, help="暂停全部类型；保留所有选择和队列"),
) -> None:
    """查看或确认调整未来同步范围，不删除本地或云端内容。"""
    c = load(state)
    if kind is None and not none:
        emit(c.source_scope())
        return
    if kind and none:
        raise typer.BadParameter("--kind 和 --none 不能同时使用")
    try:
        desired = source_policy.normalize(kind or [])
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    show_source_scope(c)
    console.print(
        "调整后：" + ("、".join(display_name(k) for k in desired) or "全部暂停"), markup=False
    )
    if not typer.confirm("确认调整未来同步范围？", default=False):
        emit({"changed": False, "scope": c.source_scope()})
        return
    emit({"changed": True, "scope": c.set_source_scope(desired)})


@app.command()
def bind(
    root: Path = typer.Option(...),
    product: str = typer.Option(...),
    project: str = "",
    kind: str = "memory",
    state: Path = DEFAULT_STATE,
    native_target: bool = False,
) -> None:
    """绑定真实源目录。已有内容默认不上传，请 preview/select。"""
    c = load(state)
    sid = c.register(root, product, project, kind)
    if not c.source_enabled(kind):
        if not typer.confirm(
            "确认启用" + display_name(kind) + "类型的后续自动同步？", default=False
        ):
            emit({"source_id": sid, "enabled": False, "history": "excluded until selected"})
            return
        c.set_source_scope(source_policy.enabled(c.config) + [kind])
    if kind == "memory" and native_target:
        add_target(c, root, product)
    emit({"source_id": sid, "history": "excluded until selected"})


@app.command("import")
def import_history(
    state: Path = DEFAULT_STATE,
    home: Path = typer.Option(Path.home()),
    product: list[str] = typer.Option(None, help="只处理指定产品，可重复"),
    kind: list[str] = typer.Option(None, help="只处理指定类型，可重复"),
    since: str = typer.Option(
        "", help="只看该时间之后修改的文件，如 2026-10-01 或 2026-10-01 09:30"
    ),
    web: bool = typer.Option(True, help="在浏览器里勾选；--no-web 回到终端逐条"),
    yes: bool = False,
    dry_run: bool = False,
    ssh: bool | None = typer.Option(None, "--ssh/--no-ssh", help="通过本机浏览器选择；默认检测 SSH"),
    web_port: int = typer.Option(0, min=0, max=65535, help="浏览器端口；SSH 默认 8765，本机默认随机"),
) -> None:
    """导入安装前的历史记忆：浏览器里勾选（--no-web 回终端），此后新增自动同步。"""
    from . import history

    current = load(state) if (state / "config.json").exists() else None
    if current:
        show_source_scope(current)
    # Listing is purely local; requiring an enrolled profile would block the
    # review a member wants before deciding whether to join at all.
    chosen_products = product or list(PRODUCTS)
    try:
        moment = history.parse_moment(since) if since else None
    except ValueError:
        raise typer.BadParameter("--since 请用 2026-10-01 或 2026-10-01 09:30:00") from None
    with Console(stderr=True).status("正在查找历史文件并检查标记（本地）…"):
        rows = history.items(home, chosen_products, since=moment)
    if kind:
        wanted = set(kind)
        rows = [row for row in rows if row["kind"] in wanted]
    if current:
        rows = history.annotate_selection(current, rows)
    if not rows:
        emit({"imported": 0, "note": "未发现可导入的本地历史"})
        return
    if dry_run:
        emit(rows)
        return
    if web:
        from . import picker

        remote, port = browser_mode(ssh, web_port)
        try:
            picked = picker.serve(
                rows,
                port=port,
                open_browser=not remote,
                empty_products=_empty_products(home, rows),
                scope=current.source_scope() if current else None,
            )
        except (TimeoutError, OSError) as exc:
            raise typer.BadParameter(f"勾选页面未能完成（{exc}）；可加 --no-web 改用终端") from None
        if not picked:
            emit({"imported": 0, "note": "未新增选择；已有选择和同步范围保持不变"})
            return
        risky = sum(1 for row in picked if row["risk"] == "sensitive")
        console.print(
            f"将上传 {len(picked)} 个文件到组织云端，其中 {risky} 个带隐私标记。"
            "上传后其他成员可检索正文。",
            markup=False,
        )
        if not typer.confirm("确认同步这些记忆？", default=False):
            emit({"imported": 0, "note": "已取消"})
            return
        current = load(state)
        if not confirm_history_scope(current, picked):
            emit({"imported": 0, "note": "未确认新类型，未新增选择"})
            return
        with operation_progress() as report:
            result = history.import_paths(current, home, picked, progress=report)
        emit({"import": result, "sync": sync_with_progress(current)})
        return
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
            f"  {index:3d}. {row['name']} — {row['title']} ({row['bytes'] / 1024:.0f} KB){marks}",
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
    # --yes must never mean "including the ones flagged as private": the whole
    # point of the marker is that a person reads it before that content leaves
    # the machine.
    answer = "clean" if yes else typer.prompt("要同步哪些？", default="")
    answer = answer.strip().lower()
    if answer in ("", "none"):
        emit({"imported": 0, "note": "未新增选择；已有选择和同步范围保持不变"})
        return
    if answer == "all":
        picked = all_selectable(rows)
    elif answer == "clean":
        picked = [
            row
            for row in rows
            if row["risk"] == "clean"
            and (
                not yes
                or kind
                or row["kind"]
                in (
                    source_policy.enabled(current.config)
                    if current
                    else source_policy.DEFAULT_KINDS
                )
            )
        ]
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
    if not confirm_history_scope(current, picked):
        emit({"imported": 0, "note": "未确认新类型，未新增选择"})
        return
    with operation_progress() as report:
        result = history.import_paths(current, home, picked, progress=report)
    pushed = sync_with_progress(current)
    emit({"import": result, "sync": pushed})


def _empty_products(home: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Detected products that contributed no importable file.

    Cowork keeps one MEMORY.md per workspace and has none until a task actually
    produces memory, so listing it as "found" but absent from the checklist
    filters looks like a bug rather than "nothing here yet".
    """
    present = {row["product"] for row in rows}
    names: list[str] = []
    for entry in discover_products(home):
        if not entry["detected"]:
            continue
        source = (
            "codex-shared"
            if entry["product"] in ("codex-cli", "codex-desktop", "chatgpt-desktop")
            else entry["product"]
        )
        if source not in present and entry["name"] not in names:
            names.append(entry["name"])
    return names


def add_target(c: NativeClient, root: Path, product: str) -> None:
    """Record a projection target; a no-op unless the member opted into projection."""
    if not c.projection_enabled():
        return
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
    emit(sync_with_progress(load(state)))


@app.command()
def search(query: str, state: Path = DEFAULT_STATE, project: str | None = None) -> None:
    """调试统一云检索，默认全组织；--project 为显式过滤。"""
    emit(load(state).search(query, project_filter=project))


STATE_LABELS = {
    "candidate": "候选未入选",
    "selected": "入选未打包",
    "packed": "已打包",
    "emitted": "已注入上下文",
    "confirmed": "模型已收到",
    "cited": "回答已采用",
    "dropped:limit": "超出条数",
    "dropped:dedup": "重复去重",
    "dropped:budget": "注入预算不足",
    "dropped:covered": "本会话已送过",
    "dropped:superseded": "已被替代",
    "dropped:expired": "已失效",
    "dropped:revalidation": "版本已变",
}


def _route_text(routes: dict[str, Any]) -> str:
    parts = []
    for name, rank in routes.items():
        if rank is None:
            continue
        parts.append(f"{name}·{rank}" if isinstance(rank, int) else f"{name}·{rank}")
    return " ".join(parts) or "—"


def _time_text(times: dict[str, Any]) -> str:
    for key in ("confirmed", "content_modified", "content_created"):
        entry = times.get(key)
        if entry:
            stamp = time.strftime("%Y-%m-%d", time.localtime(entry["value"]))
            return f"{stamp}（{key}·{entry['source']}）"
    return "未知"


@app.command("recall-trace")
def recall_trace(
    state: Path = DEFAULT_STATE,
    trace: str | None = typer.Option(None, help="展开某一次召回；省略则列出本设备最近记录"),
    limit: int = typer.Option(20, min=1, max=100),
    as_json: bool = typer.Option(False, "--json", help="输出原始 JSON"),
) -> None:
    """召回明细：每条候选的召回方式、排名变化、本轮状态和时间，不含正文。"""
    c = load(state)
    traces = c.transport.recalls(trace_id=trace, limit=limit)
    if as_json:
        emit(traces)
        return
    if not traces:
        console.print("没有召回记录。")
        return
    if trace is None:
        table = Table(title="最近召回（本设备）")
        for column in ("时间", "通道", "问题", "候选", "入选", "送达", "阶段", "trace"):
            table.add_column(column)
        for row in traces:
            counts = row["counts"]
            delivered = sum(counts.get(k, 0) for k in ("emitted", "confirmed", "cited"))
            selected = delivered + counts.get("selected", 0) + counts.get("packed", 0)
            table.add_row(
                time.strftime("%m-%d %H:%M", time.localtime(row["created"])),
                row["channel"],
                row.get("query_preview") or "（未保存）",
                str(counts["candidates"]),
                str(selected),
                str(delivered),
                " ".join(f"{k}:{v}" for k, v in row.get("stages", {}).items()),
                row["trace_id"],
            )
        console.print(table)
        return
    detail = traces[0]
    console.print(
        f"trace {detail['trace_id']} · {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(detail['created']))}"
        f" · 通道 {detail['channel']} · 问题 {detail.get('query_preview') or '（未保存）'}"
    )
    console.print("阶段：" + " ".join(f"{k}:{v}" for k, v in detail.get("stages", {}).items()))
    reasons = detail.get("retrieval", {}).get("reasons") or []
    console.print("降级原因：" + ("、".join(reasons) if reasons else "无"))
    table = Table()
    for column in ("排名", "记忆标题", "内容形态", "来源产品", "组织/项目", "原生产者", "召回方式", "本轮状态", "时间"):
        table.add_column(column)
    for item in detail["candidates"]:
        producer = item.get("producer") or {}
        nature = item.get("nature") or {}
        table.add_row(
            f"{item.get('final_rank') or '—'}/{item.get('rank') or '—'}",
            item.get("title") or item.get("path") or "",
            f"{item.get('role')}·{nature.get('value', '未知')}",
            str(item.get("product")),
            str(item.get("project") or ""),
            "未知" if producer.get("unknown") else str(producer.get("name")),
            _route_text(item.get("routes", {})),
            STATE_LABELS.get(item.get("state", ""), item.get("state", "")),
            _time_text(item.get("times") or {}),
        )
    console.print(table)
    for item in detail["candidates"]:
        decay = item.get("decay")
        console.print(
            f"[bold]{item.get('title') or item.get('path')}[/bold] {item['asset_id']} r{item['revision']}"
            + (f" · 衰减 半衰期{decay['half_life_days']}天 年龄{decay['age_days'] if decay['age_days'] is not None else '未知'} 权重{decay['weight']} {decay['reason']}" if decay else " · 衰减 未启用")
            + (f" · 信号 {item['signals']}" if item.get("signals") else "")
            + (f" · {item['state_detail']}" if item.get("state_detail") else "")
        )
        for stage in item.get("stages", []):
            console.print(
                f"    {stage['stage']:>18} {stage['before_rank'] or '—'} → {stage['after_rank'] or '—'}"
                f"  {stage['before_score'] if stage['before_score'] is not None else '—'} → {stage['after_score'] if stage['after_score'] is not None else '—'}"
                f"  {stage.get('effect', '')}",
                markup=False,
            )


@app.command("recall-feedback")
def recall_feedback(
    trace: str = typer.Option(..., help="召回明细里的 trace_id"),
    asset: str = typer.Option(..., help="候选的 asset_id"),
    revision: int = typer.Option(..., min=1),
    event: str = typer.Option(..., help="cite（回答采用）/ support / contradict / confirm（人工确认）"),
    state: Path = DEFAULT_STATE,
) -> None:
    """对某次召回的候选记录反馈；之后的召回会据此调整排序。"""
    if event not in ("cite", "support", "contradict", "confirm"):
        raise typer.BadParameter("event 只能是 cite / support / contradict / confirm")
    c = load(state)
    emit(c.transport.recall_feedback(trace, [{"asset_id": asset, "revision": revision, "event": event}]))


@app.command()
def status(state: Path = DEFAULT_STATE) -> None:
    c = load(state)
    summary = {
        "identity": {k: v for k, v in c.identity.items() if k != "token"},
        "hub": c.config.get("hub"),
        "sources": c.sources(),
        "targets": c.targets(),
        "pending": c.pending_count(),
        "scope": c.source_scope(),
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
            "expires_in_seconds": PAIRING_MAX_TTL,
            "status": "pairing prepared; actual Cowork runtime must load the plugin and access the connection file",
        }
    )


@app.command()
def uninstall(state: Path = DEFAULT_STATE, home: Path = typer.Option(Path.home())) -> None:
    """恢复仍未被用户修改的配置；保留同步数据和原生记忆。"""
    if platform.system() == "Darwin":
        p = launch_agent(state, home, install=False)
        if p.exists():
            for domain in ("gui", "user"):
                subprocess.run(
                    ["launchctl", "bootout", f"{domain}/{os.getuid()}", str(p)], capture_output=True
                )
            p.unlink()
    emit({"conflicts": uninstall_hooks(home, state), "data": "preserved"})


@app.command()
def setup(
    hub: str = typer.Option(...),
    state: Path = DEFAULT_STATE,
    home: Path = typer.Option(Path.home()),
    service: bool = True,
    ssh: bool | None = typer.Option(None, "--ssh/--no-ssh", help="使用本机浏览器安装远端设备；默认检测 SSH"),
    web_port: int = typer.Option(0, min=0, max=65535, help="浏览器端口；SSH 默认 8765，本机默认随机"),
    projection: bool = typer.Option(
        False,
        help="把组织记忆也存成各产品本地的原生文件；默认关闭，召回走云端实时检索",
    ),
) -> None:
    """终端分步安装：检查产品、飞书登录、挑选历史、确认配置。"""
    from . import history

    console.print("[bold]AMH · 组织共享第二大脑[/bold]")
    require_research_service(hub)
    remote, port = browser_mode(ssh, web_port)
    # A failed 1.3.1 --projection setup could leave only this preference behind.
    # It is not an enrolled identity. Keep it until login succeeds; adopt() accepts
    # only this exact harmless partial shape, never an existing identity/profile.
    profile = state / "config.json"
    partial = json.loads(profile.read_text()) if profile.exists() else None
    joined = partial is not None and set(partial) != {"projection"}
    found = discover_products(home)
    table = Table("产品", "检测结果", "原生记忆目录")
    for p in found:
        table.add_row(
            p["name"],
            "已发现" if p["detected"] else "未发现",
            "\n".join(p["roots"]) or p.get("note") or "运行环境未暴露本地目录",
        )
    console.print(table)
    console.print("组织服务地址：" + hub)
    if not joined:
        console.print("\n第 1 步：用飞书账号登录")
        identity = feishu_login(hub, state, socket.gethostname(), port=port, open_browser=not remote)
        c = load(state)
        console.print(f"登录成功：{identity['member_name']}")
    else:
        c = load(state)
        if c.config["hub"] != hub.rstrip("/"):
            raise typer.BadParameter("不同 Hub 必须使用独立 --state；不能把旧队列发给新组织")
        console.print("已使用现有身份；如需换账号请指定新的 --state")
    c.config["projection"] = bool(projection)
    atomic_json(c.root / "config.json", c.config)
    c.configure_storage()
    console.print(
        f"成员：{c.identity['member_name']} ({c.identity['member_id']})；组织：{c.identity['tenant_id']}"
    )

    show_source_scope(c)
    console.print("范围可用 amh-native scope 预览；加 --kind memory 等完整类型列表可确认调整。")
    console.print("\n第 2 步：新增历史选择（跳过不撤销已有选择；默认不新增上传任何历史）")
    with Console(stderr=True).status("正在查找历史文件并检查标记（本地）…"):
        rows = history.annotate_selection(c, history.items(home, list(PRODUCTS)))
    picked: list[dict[str, Any]] = []
    if rows:
        # A real machine lists a thousand-odd files; ticking them by number in a
        # terminal is not a decision anyone can make, so the checklist is a page.
        # Without a terminal there is nobody to click it, so the page would block
        # forever: fall back to skipping rather than hanging.
        import sys as _sys

        if not _sys.stdin.isatty():
            console.print("非交互环境，跳过历史勾选（可稍后运行 import）。", markup=False)
            picked = []
        else:
            from . import picker

            console.print(
                f"本机 {len(rows)} 个可共享文件，将在浏览器里逐项勾选。",
                markup=False,
            )
            try:
                picked = picker.serve(
                    rows,
                    port=port,
                    open_browser=not remote and bool(
                        __import__("os").environ.get("DISPLAY") or _sys.platform == "darwin"
                    ),
                    empty_products=_empty_products(home, rows),
                    scope=c.source_scope(),
                )
            except (TimeoutError, OSError) as exc:
                console.print(f"勾选页面未能完成（{exc}）；本次跳过历史导入。", markup=False)
                picked = []
        if picked and not confirm_history_scope(c, picked):
            picked = []
        if picked:
            with operation_progress() as report:
                imported = history.import_paths(c, home, picked, progress=report)
            console.print(
                f"已选择 {imported['selected']} 个文件，尚未上传；确认启用后开始同步。",
                markup=False,
            )
            if imported["late_sensitive"]:
                console.print(
                    f"另有 {len(imported['late_sensitive'])} 个文件在全文检查中发现隐私内容，已跳过。",
                    markup=False,
                )
            if imported["rejected"]:
                console.print(
                    f"{len(imported['rejected'])} 个文件含密钥或已变更，已跳过。", markup=False
                )
        else:
            console.print("未新增选择；已有选择和同步范围保持不变。", markup=False)
    else:
        console.print("没有发现本机历史记忆，跳过。")

    console.print("\n第 3 步：接入产品")
    chosen = []
    processed_sources: set[str] = set()
    for product in found:
        if not product["detected"]:
            continue
        if product["product"] != "cowork" and not typer.confirm(
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
            label = "新内容同步来源" if c.source_enabled(kind) else "未启用的来源（保留历史基线）"
            console.print(f"{label}：{kind} · {root}", markup=False)
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
            f"Cowork 插件：{plugin}.zip；一次性连接文件：{connection}（7天内单次使用，不能放进插件包）。"
        )
        console.print(
            "在 Cowork 导入插件。macOS 宿主机 Hook 已固定持久状态和连接文件路径；其他运行环境需让首次任务访问连接文件所在目录。运行时会独立登记设备、发现原生记忆目录；实际接入状态仍需核验。"
        )
        # Reveal the supported import artifact; never write Claude's private
        # plugin state or pretend the Claude Code CLI installs into Cowork.
        if platform.system() == "Darwin" and not remote:
            subprocess.run(["open", "-R", str(plugin) + ".zip"], check=False, capture_output=True)
    if c.sources():
        initial_sync(c)
    if service and platform.system() == "Darwin":
        start_background_service(state, home, remote=remote)
    console.print(
        "本地配置完成。请新建会话进入组织模式，已打开会话仍可能保留原上下文；用 status 查看同步，产品实际读取须通过独立验收。"
    )


def start_background_service(state: Path, home: Path, *, remote: bool) -> None:
    """SSH users have a user domain even when no GUI login domain exists."""
    path = launch_agent(state, home, install=False)
    label = path.stem
    domains = [f"{kind}/{os.getuid()}" for kind in ("user", "gui")]
    # Re-running setup must not load the same worker into two domains.
    for domain in domains:
        existing = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True)
        if existing.returncode == 0:
            console.print("后台同步服务已登记：" + domain, markup=False)
            return
    domain = domains[0 if remote else 1]
    path = launch_agent(state, home, background=remote)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
    if result.returncode:
        console.print(
            "后台同步服务未启动；本次配置已保存，但安装尚未全部完成。"
            "可重新运行 setup，或暂时运行 amh-native worker --state " + str(state),
            markup=False,
        )
        raise typer.Exit(1)
    console.print("后台同步服务已登记：" + domain, markup=False)


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
