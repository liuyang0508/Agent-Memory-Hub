#!/usr/bin/env python3
"""Render README.zh.md into a local-preview HTML with correct relative links."""

from __future__ import annotations

import base64
import mimetypes
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
VISUALS = ROOT / "docs" / "visuals"
PREVIEW_OUTPUT = VISUALS / "readme-zh-preview.html"
ARTICLE_OUTPUT = VISUALS / "readme-zh-article-preview.html"
PREVIEW_TXT_OUTPUT = VISUALS / "readme-zh-preview.self-contained.html.txt"
ARTICLE_TXT_OUTPUT = VISUALS / "readme-zh-article-preview.self-contained.html.txt"


def _rewrite_local_preview_links(html: str) -> str:
    replacements = {
        'href="docs/visuals/readme-preview.css"': 'href="readme-preview.css"',
        'href="./docs/visuals/': 'href="./',
        'src="./docs/visuals/': 'src="./',
        'href="./docs/': 'href="../',
        'src="./docs/': 'src="../',
        'href="./README.md"': 'href="../../README.md"',
        'href="./README.zh.md"': 'href="../../README.zh.md"',
        'href="./STRATEGY.md"': 'href="../../STRATEGY.md"',
        'href="./ROADMAP.md"': 'href="../../ROADMAP.md"',
        'href="./LICENSE"': 'href="../../LICENSE"',
        'href="./pyproject.toml"': 'href="../../pyproject.toml"',
        'href="./benchmarks/': 'href="../../benchmarks/',
        'src="./benchmarks/': 'src="../../benchmarks/',
        'href="./agent_runtime_kit/': 'href="../../agent_runtime_kit/',
        'href="./agent_brain/': 'href="../../agent_brain/',
        'href="./web/': 'href="../../web/',
        'src="./web/': 'src="../../web/',
        'href="./tests/': 'href="../../tests/',
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


def _remove_redundant_readme_h1(html: str) -> str:
    return re.sub(r'\n?<h1 id="agent-memory-hub">Agent Memory Hub</h1>\n?', "\n", html, count=1)


def _render(output: Path, *, title: str, toc: bool) -> None:
    command = [
        "pandoc",
        "README.zh.md",
        "-s",
        "--metadata",
        f"title={title}",
        "--css",
        "readme-preview.css",
        "-o",
        str(output.relative_to(ROOT)),
    ]
    if toc:
        command[3:3] = ["--toc", "--toc-depth=2"]
    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )
    html = output.read_text(encoding="utf-8")
    html = _rewrite_local_preview_links(html)
    html = _remove_redundant_readme_h1(html)
    output.write_text(html, encoding="utf-8")


def _asset_as_data_uri(html_path: Path, ref: str) -> str | None:
    clean_ref = unquote(ref.split("#", 1)[0])
    if not clean_ref or clean_ref.startswith(("http://", "https://", "data:", "mailto:")):
        return None
    asset = (html_path.parent / clean_ref).resolve()
    try:
        asset.relative_to(ROOT)
    except ValueError:
        return None
    if not asset.exists() or not asset.is_file():
        return None
    mime, _ = mimetypes.guess_type(asset)
    if asset.suffix == ".svg":
        mime = "image/svg+xml"
    mime = mime or "application/octet-stream"
    data = base64.b64encode(asset.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _write_self_contained(html_path: Path, output: Path) -> None:
    html = html_path.read_text(encoding="utf-8")
    css = (VISUALS / "readme-preview.css").read_text(encoding="utf-8")
    html = re.sub(
        r'<link rel="stylesheet" href="readme-preview\.css" */?>|<link href="readme-preview\.css" rel="stylesheet" */?>',
        f"<style>\n{css}\n</style>",
        html,
    )

    def replace_img(match: re.Match[str]) -> str:
        prefix, ref, suffix = match.groups()
        data_uri = _asset_as_data_uri(html_path, ref)
        if data_uri is None:
            return match.group(0)
        return f'{prefix}{data_uri}{suffix}'

    html = re.sub(r'(<img\b[^>]*\bsrc=")([^"]+)("[^>]*>)', replace_img, html)
    output.write_text(html, encoding="utf-8")


def main() -> int:
    _render(PREVIEW_OUTPUT, title="Agent Memory Hub README", toc=True)
    _render(ARTICLE_OUTPUT, title="Agent Memory Hub README", toc=False)
    _write_self_contained(PREVIEW_OUTPUT, PREVIEW_TXT_OUTPUT)
    _write_self_contained(ARTICLE_OUTPUT, ARTICLE_TXT_OUTPUT)
    for output in [PREVIEW_OUTPUT, ARTICLE_OUTPUT, PREVIEW_TXT_OUTPUT, ARTICLE_TXT_OUTPUT]:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
