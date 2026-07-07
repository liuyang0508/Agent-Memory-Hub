"""CLI commands for productized memory capabilities."""

from __future__ import annotations

import json
from pathlib import Path

from agent_brain.interfaces.cli._app import benchmark_app, headroom_app, profile_app, govern_app
from agent_brain.interfaces.cli._shared import _brain_dir, _open_components, typer


@profile_app.command(name="export")
def profile_export(
    target: str = typer.Option("codex", "--target", help="claude-code, codex, cursor, generic"),
    output_root: Path | None = typer.Option(None, "--output-root", help="Where to write the profile"),
    project: str | None = typer.Option(None, "--project", help="Filter by project slug"),
    max_items: int = typer.Option(24, "--max-items", help="Max memory items to include"),
    apply: bool = typer.Option(False, "--apply", help="Write managed block to target file"),
    output_format: str = typer.Option("markdown", "--format", help="markdown or json"),
) -> None:
    """Render or apply an agent-facing Memory Profile."""
    from agent_brain.product.memory_profiles import export_memory_profile

    result = export_memory_profile(
        _brain_dir(),
        target=target,
        output_root=output_root,
        project=project,
        max_items=max_items,
        apply=apply,
    )
    if output_format == "json":
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(result.text)
    if apply:
        typer.echo(f"wrote profile: {result.path}")


@govern_app.command(name="hierarchy")
def hierarchy_build(
    apply: bool = typer.Option(False, "--apply", help="Write derived/hierarchical-memory.json"),
    output_format: str = typer.Option("json", "--format", help="json or summary"),
) -> None:
    """Build deterministic L2/L3 hierarchical memory sidecar."""
    from agent_brain.product.hierarchical_memory import build_hierarchical_memory

    report = build_hierarchical_memory(_brain_dir(), apply=apply)
    if output_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return
    payload = report.payload
    typer.echo(
        f"L2 topics={len(payload['l2_topics'])} "
        f"L3 projects={len(payload['l3_projects'])} path={report.path}"
    )


@benchmark_app.command(name="retrieval")
def benchmark_retrieval(
    cases: Path = typer.Option(..., "--cases", help="JSON file with retrieval benchmark cases"),
    top_k: int = typer.Option(10, "--top-k", help="Search depth"),
    min_recall_at_1: float = typer.Option(0.6, "--min-recall-at-1", help="Gate threshold"),
    min_mrr: float = typer.Option(0.6, "--min-mrr", help="Gate threshold"),
    output: Path | None = typer.Option(None, "--output", help="Optional report path"),
) -> None:
    """Run a retrieval benchmark gate against the current brain index."""
    from agent_brain.evaluation.retrieval_gate import evaluate_rankings, load_cases, write_report

    loaded_cases = load_cases(cases)
    _store, _idx, retriever = _open_components()

    def search(query: str, depth: int) -> list[str]:
        return [hit.id for hit in retriever.search(query, top_k=depth)]

    report = evaluate_rankings(
        loaded_cases,
        search,
        top_k=top_k,
        min_recall_at_1=min_recall_at_1,
        min_mrr=min_mrr,
    )
    if output is not None:
        write_report(output, report)
    typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if not report.passed:
        raise typer.Exit(1)


@benchmark_app.command(name="compression")
def benchmark_compression(
    cases: Path | None = typer.Option(None, "--cases", help="Optional JSON file with compression cases"),
    min_pass_rate: float = typer.Option(1.0, "--min-pass-rate", help="Gate threshold"),
    max_mean_compression_ratio: float = typer.Option(
        0.8,
        "--max-mean-compression-ratio",
        help="Gate threshold",
    ),
    min_mean_tokens_saved: float = typer.Option(
        1.0,
        "--min-mean-tokens-saved",
        help="Gate threshold",
    ),
    output: Path | None = typer.Option(None, "--output", help="Optional report path"),
    output_format: str = typer.Option("json", "--format", help="json or summary"),
) -> None:
    """Run the few-shot compression quality gate."""
    from agent_brain.evaluation.compression_gate import (
        evaluate_compression_cases,
        load_builtin_compression_cases,
        load_cases,
        write_report,
    )

    loaded_cases = load_cases(cases) if cases is not None else load_builtin_compression_cases()
    report = evaluate_compression_cases(
        loaded_cases,
        min_pass_rate=min_pass_rate,
        max_mean_compression_ratio=max_mean_compression_ratio,
        min_mean_tokens_saved=min_mean_tokens_saved,
    )
    if output is not None:
        write_report(output, report)
    if output_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if report.passed else "FAIL"
        typer.echo(
            f"Compression gate: {status} "
            f"pass_rate={report.metrics['pass_rate']:.3f} "
            f"mean_ratio={report.metrics['mean_compression_ratio']:.3f} "
            f"mean_saved={report.metrics['mean_tokens_saved']:.1f}"
        )
        for failure in report.failures:
            typer.echo(f"- {failure}")
    if not report.passed:
        raise typer.Exit(1)


@benchmark_app.command(name="ml-advisory")
def benchmark_ml_advisory(
    cases: Path | None = typer.Option(None, "--cases", help="Optional JSON file with ML/DL cases"),
    min_pass_rate: float = typer.Option(1.0, "--min-pass-rate", help="Gate threshold"),
    max_unsafe_promotions: int = typer.Option(0, "--max-unsafe-promotions", help="Gate threshold"),
    output: Path | None = typer.Option(None, "--output", help="Optional report path"),
    output_format: str = typer.Option("json", "--format", help="json or summary"),
) -> None:
    """Run the few-shot ML/DL advisory quality gate."""
    from agent_brain.evaluation.ml_advisory_gate import (
        evaluate_ml_advisory_cases,
        load_builtin_ml_advisory_cases,
        load_cases,
        write_report,
    )

    loaded_cases = load_cases(cases) if cases is not None else load_builtin_ml_advisory_cases()
    report = evaluate_ml_advisory_cases(
        loaded_cases,
        min_pass_rate=min_pass_rate,
        max_unsafe_promotions=max_unsafe_promotions,
    )
    if output is not None:
        write_report(output, report)
    if output_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if report.passed else "FAIL"
        typer.echo(
            f"ML/DL advisory gate: {status} "
            f"pass_rate={report.metrics['pass_rate']:.3f} "
            f"mean_delta={report.metrics['mean_delta']:.3f} "
            f"unsafe_promotions={report.metrics['unsafe_promotion_count']}"
        )
        for failure in report.failures:
            typer.echo(f"- {failure}")
    if not report.passed:
        raise typer.Exit(1)


@benchmark_app.command(name="recall-hallucination")
def benchmark_recall_hallucination(
    top_k: int = typer.Option(8, "--top-k", help="Retrieval depth"),
    max_false_injection_rate: float = typer.Option(
        0.0,
        "--max-false-injection-rate",
        help="Gate threshold",
    ),
    min_positive_recall_rate: float = typer.Option(
        1.0,
        "--min-positive-recall-rate",
        help="Gate threshold",
    ),
    output: Path | None = typer.Option(None, "--output", help="Optional report path"),
    output_format: str = typer.Option("json", "--format", help="json or summary"),
) -> None:
    """Run public-safe recall hallucination gate for pre-injection context."""
    from agent_brain.evaluation.recall_hallucination import (
        run_recall_hallucination_gate,
        write_report,
    )

    report = run_recall_hallucination_gate(
        top_k=top_k,
        max_false_injection_rate=max_false_injection_rate,
        min_positive_recall_rate=min_positive_recall_rate,
    )
    if output is not None:
        write_report(output, report)
    if output_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if report.passed else "FAIL"
        metrics = report.metrics
        typer.echo(
            f"Recall hallucination gate: {status} "
            f"cases={metrics['case_count']} "
            f"false_injection={metrics['false_injection_rate']:.3f} "
            f"positive_recall={metrics['positive_recall_rate']:.3f} "
            f"negative_clean={metrics['negative_clean_rate']:.3f}"
        )
        for failure in report.failures[:20]:
            typer.echo(f"- {failure}")
    if not report.passed:
        raise typer.Exit(1)


@benchmark_app.command(name="system")
def benchmark_system(
    cases: Path | None = typer.Option(None, "--cases", help="Optional JSON file with system benchmark cases"),
    max_items: int | None = typer.Option(None, "--max-items", help="Max real memory items to index"),
    max_cases: int = typer.Option(100, "--max-cases", help="Max generated cases when --cases is omitted"),
    top_k: int = typer.Option(10, "--top-k", help="Retrieval depth"),
    min_block_accuracy: float = typer.Option(0.98, "--min-block-accuracy", help="Gate threshold"),
    min_inject_accuracy: float = typer.Option(0.95, "--min-inject-accuracy", help="Gate threshold"),
    min_recall_at_k: float = typer.Option(0.85, "--min-recall-at-k", help="Gate threshold"),
    min_firewall_include_rate: float = typer.Option(0.85, "--min-firewall-include-rate", help="Gate threshold"),
    min_pack_reversible_rate: float = typer.Option(1.0, "--min-pack-reversible-rate", help="Gate threshold"),
    output: Path | None = typer.Option(None, "--output", help="Optional report path"),
    output_format: str = typer.Option("json", "--format", help="json or summary"),
) -> None:
    """Run system few-shot gate across query, retrieval, firewall, and context pack."""
    from agent_brain.evaluation.system_benchmark import (
        build_synthetic_system_cases,
        load_cases,
        load_items,
        run_system_benchmark_on_items,
        write_report,
    )

    brain_dir = _brain_dir()
    items = load_items(brain_dir, max_items=max_items)
    loaded_cases = load_cases(cases) if cases is not None else build_synthetic_system_cases(
        items,
        max_cases=max_cases,
    )
    report = run_system_benchmark_on_items(
        brain_dir,
        items,
        loaded_cases,
        top_k=top_k,
        min_block_accuracy=min_block_accuracy,
        min_inject_accuracy=min_inject_accuracy,
        min_recall_at_k=min_recall_at_k,
        min_firewall_include_rate=min_firewall_include_rate,
        min_pack_reversible_rate=min_pack_reversible_rate,
    )
    if output is not None:
        write_report(output, report)
    if output_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if report.passed else "FAIL"
        metrics = report.metrics
        query_gate = metrics["query_gate"]
        retrieval = metrics["retrieval"]
        context = metrics["context"]
        typer.echo(
            f"System benchmark: {status} "
            f"cases={metrics['case_count']} items={metrics['items_indexed']} "
            f"block={query_gate['block_accuracy']:.3f} "
            f"inject={query_gate['inject_accuracy']:.3f} "
            f"recall@{metrics['top_k']}={retrieval['recall_at_k']:.3f} "
            f"mrr={retrieval['mrr']:.3f} "
            f"firewall={context['firewall_include_rate']:.3f} "
            f"pack={context['pack_reversible_rate']:.3f}"
        )
        for failure in report.failures[:20]:
            typer.echo(f"- {failure}")
    if not report.passed:
        raise typer.Exit(1)


@benchmark_app.command(name="report")
def benchmark_report(
    cases: Path | None = typer.Option(None, "--cases", help="Optional JSON file with system benchmark cases"),
    max_items: int | None = typer.Option(None, "--max-items", help="Max real memory items to index"),
    max_cases: int = typer.Option(100, "--max-cases", help="Max generated cases when --cases is omitted"),
    top_k: int = typer.Option(10, "--top-k", help="Retrieval depth"),
    min_block_accuracy: float = typer.Option(0.98, "--min-block-accuracy", help="Gate threshold"),
    min_inject_accuracy: float = typer.Option(0.95, "--min-inject-accuracy", help="Gate threshold"),
    min_recall_at_k: float = typer.Option(0.85, "--min-recall-at-k", help="Gate threshold"),
    min_firewall_include_rate: float = typer.Option(0.85, "--min-firewall-include-rate", help="Gate threshold"),
    min_pack_reversible_rate: float = typer.Option(1.0, "--min-pack-reversible-rate", help="Gate threshold"),
    output_dir: Path = typer.Option(Path("docs/evaluation"), "--output-dir", help="Where to write public report files"),
    output_format: str = typer.Option("summary", "--format", help="summary or json"),
) -> None:
    """Run system benchmark and render public JSON/Markdown/HTML evaluation report."""
    from agent_brain.evaluation.professional_report import (
        load_adapter_capability_records,
        write_professional_evaluation_report,
    )
    from agent_brain.evaluation.system_benchmark import (
        build_synthetic_system_cases,
        load_cases,
        load_items,
        run_system_benchmark_on_items,
    )

    brain_dir = _brain_dir()
    items = load_items(brain_dir, max_items=max_items)
    loaded_cases = load_cases(cases) if cases is not None else build_synthetic_system_cases(
        items,
        max_cases=max_cases,
    )
    system_report = run_system_benchmark_on_items(
        brain_dir,
        items,
        loaded_cases,
        top_k=top_k,
        min_block_accuracy=min_block_accuracy,
        min_inject_accuracy=min_inject_accuracy,
        min_recall_at_k=min_recall_at_k,
        min_firewall_include_rate=min_firewall_include_rate,
        min_pack_reversible_rate=min_pack_reversible_rate,
    )
    adapter_records, adapter_error = load_adapter_capability_records(brain_dir)
    written = write_professional_evaluation_report(
        output_dir,
        system_report,
        adapter_capabilities=adapter_records,
        adapter_error=adapter_error,
    )
    if output_format == "json":
        typer.echo(json.dumps(written.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        metrics = system_report.metrics
        query_gate = metrics["query_gate"]
        retrieval = metrics["retrieval"]
        context = metrics["context"]
        status = "PASS" if system_report.passed else "FAIL"
        typer.echo(
            f"Evaluation report: {status} "
            f"cases={metrics['case_count']} items={metrics['items_indexed']} "
            f"block={query_gate['block_accuracy']:.3f} "
            f"inject={query_gate['inject_accuracy']:.3f} "
            f"recall@{metrics['top_k']}={retrieval['recall_at_k']:.3f} "
            f"mrr={retrieval['mrr']:.3f} "
            f"firewall={context['firewall_include_rate']:.3f} "
            f"pack={context['pack_reversible_rate']:.3f}"
        )
        typer.echo(f"- json: {written.json_path}")
        typer.echo(f"- markdown: {written.markdown_path}")
        typer.echo(f"- html: {written.html_path}")
        if adapter_error:
            typer.echo(f"- adapter capability warning: {adapter_error}")
        for failure in system_report.failures[:20]:
            typer.echo(f"- {failure}")
    if not system_report.passed:
        raise typer.Exit(1)


@headroom_app.command(name="status")
def headroom_status_cmd() -> None:
    """Show optional Headroom bridge availability."""
    from agent_brain.platform.headroom_integration import headroom_status

    typer.echo(json.dumps(headroom_status().to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


@headroom_app.command(name="compress")
def headroom_compress_cmd(
    text: str = typer.Argument(..., help="Text to compress"),
    budget_chars: int = typer.Option(1200, "--budget-chars", help="Fallback character budget"),
    detail_uri: str | None = typer.Option(None, "--detail-uri", help="Canonical detail URI"),
    query: str | None = typer.Option(None, "--query", help="Optional query terms for adaptive ranking"),
) -> None:
    """Compress text via Headroom when available, else AMH-local adaptive compression."""
    from agent_brain.platform.headroom_integration import compress_with_headroom

    result = compress_with_headroom(
        text,
        budget_chars=budget_chars,
        detail_uri=detail_uri,
        query=query,
        brain_dir=_brain_dir(),
    )
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


@headroom_app.command(name="retrieve")
def headroom_retrieve_cmd(
    key: str = typer.Argument(..., help="CCR sidecar key returned by headroom compress"),
) -> None:
    """Retrieve an AMH-local compressed-original sidecar."""
    from agent_brain.platform.headroom_integration import retrieve_compressed_original

    text = retrieve_compressed_original(key, brain_dir=_brain_dir())
    if text is None:
        typer.echo(f"compressed original not found: {key}", err=True)
        raise typer.Exit(1)
    typer.echo(text)


__all__ = [
    "benchmark_compression",
    "benchmark_ml_advisory",
    "benchmark_retrieval",
    "benchmark_report",
    "benchmark_system",
    "headroom_compress_cmd",
    "headroom_retrieve_cmd",
    "headroom_status_cmd",
    "hierarchy_build",
    "profile_export",
]
