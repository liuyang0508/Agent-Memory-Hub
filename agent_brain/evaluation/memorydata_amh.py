"""Materialize the AMH method preset into an upstream MemoryData checkout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AMH_PATCH_MARKER = "# BEGIN AMH MemoryData adapter patch"
AMH_PATCH_END_MARKER = "# END AMH MemoryData adapter patch"
AMH_CONVERSATION_PATCH_MARKER = "# BEGIN AMH MemoryData conversation patch"
AMH_ARTIFACT_PATH_PATCH_MARKER = "# BEGIN AMH MemoryData artifact-path patch"
AMH_PROMPT_TEMPLATE_PATCH_MARKER = "# BEGIN AMH MemoryData prompt-template patch"
AMH_INITIALIZATION_PATCH_MARKER = "# BEGIN AMH MemoryData initialization patch"
AMH_MAIN_QUERY_START_PATCH_MARKER = "# BEGIN AMH MemoryData query-start patch"


def materialize_memorydata_amh_adapter(
    memorydata_repo: Path,
    *,
    amh_repo_root: Path | None = None,
) -> dict[str, Any]:
    """Install the AMH config, adapter shim, and AgentWrapper patch.

    MemoryData is kept as an external checkout under ``.cache/external``.  This
    function makes AMH reproducible after that checkout is refreshed by
    re-materializing the small integration surface instead of relying on manual
    edits inside the vendor tree.
    """

    memorydata_repo = Path(memorydata_repo)
    amh_repo_root = Path(amh_repo_root or _default_amh_repo_root()).resolve()

    config_path = memorydata_repo / "config" / "hybrid_amh.yaml"
    adapter_path = memorydata_repo / "methods" / "amh" / "amh_adapter.py"
    init_path = memorydata_repo / "methods" / "amh" / "__init__.py"
    agent_path = memorydata_repo / "utils" / "agent.py"
    main_path = memorydata_repo / "main.py"
    conversation_path = memorydata_repo / "utils" / "conversation_creator.py"
    artifact_paths_path = memorydata_repo / "utils" / "artifact_paths.py"
    initialization_path = memorydata_repo / "utils" / "initialization.py"
    prompt_template_path = (
        memorydata_repo
        / "benchmark"
        / "memoryagentbench"
        / "prompts"
        / "benchmark_templates.py"
    )

    if not agent_path.is_file():
        raise FileNotFoundError(f"MemoryData AgentWrapper file not found: {agent_path}")

    _write_if_changed(config_path, _hybrid_amh_config(amh_repo_root))
    _write_if_changed(adapter_path, AMH_ADAPTER_SOURCE)
    _write_if_changed(init_path, '"""AMH MemoryData adapter package."""\n')
    _patch_agent_wrapper(agent_path)
    _patch_main_query_start(main_path)
    _patch_memory_agent_hint_tuple(
        conversation_path,
        marker=AMH_CONVERSATION_PATCH_MARKER,
        variable_name="MEMORY_AGENT_NAME_HINTS",
    )
    _patch_memory_agent_hint_tuple(
        artifact_paths_path,
        marker=AMH_ARTIFACT_PATH_PATCH_MARKER,
        variable_name="_MEMORY_AGENT_HINTS",
    )
    _patch_prompt_templates(prompt_template_path)
    _patch_initialization(initialization_path)

    return {
        "status": "materialized",
        "agent_config": "config/hybrid_amh.yaml",
        "adapter": str(adapter_path),
        "agent_patch": str(agent_path),
        "main_query_start_patch": str(main_path),
        "conversation_patch": str(conversation_path),
        "artifact_paths_patch": str(artifact_paths_path),
        "prompt_template_patch": str(prompt_template_path),
        "initialization_patch": str(initialization_path),
        "amh_repo_root": str(amh_repo_root),
    }


def _default_amh_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_if_changed(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _patch_agent_wrapper(agent_path: Path) -> None:
    source = agent_path.read_text(encoding="utf-8")
    patched = _replace_or_append_marked_block(
        source,
        marker=AMH_PATCH_MARKER,
        end_marker=AMH_PATCH_END_MARKER,
        block=AMH_AGENT_PATCH,
    )
    if patched != source:
        agent_path.write_text(patched, encoding="utf-8")


def _replace_or_append_marked_block(
    source: str,
    *,
    marker: str,
    end_marker: str,
    block: str,
) -> str:
    start = source.find(marker)
    normalized_block = block.strip() + "\n"
    if start == -1:
        return source.rstrip() + "\n\n" + normalized_block

    end = source.find(end_marker, start)
    if end == -1:
        raise RuntimeError(f"Cannot refresh AMH patch because end marker is missing: {end_marker}")
    end += len(end_marker)
    while end < len(source) and source[end] in "\r\n":
        end += 1

    prefix = source[:start].rstrip()
    suffix = source[end:].lstrip("\r\n")
    if suffix:
        return prefix + "\n\n" + normalized_block + "\n" + suffix
    return prefix + "\n\n" + normalized_block


def _patch_memory_agent_hint_tuple(path: Path, *, marker: str, variable_name: str) -> None:
    if not path.is_file():
        return
    source = path.read_text(encoding="utf-8")
    if marker in source:
        return
    patched = source.rstrip() + "\n\n" + f"""{marker}
if "amh" not in {variable_name}:
    {variable_name} = {variable_name} + ("amh",)
# END AMH MemoryData hint patch
"""  # noqa: ISC003
    path.write_text(patched, encoding="utf-8")


def _patch_prompt_templates(path: Path) -> None:
    if not path.is_file():
        return
    source = path.read_text(encoding="utf-8")
    if AMH_PROMPT_TEMPLATE_PATCH_MARKER in source:
        return
    patched = source.rstrip() + "\n\n" + f"""{AMH_PROMPT_TEMPLATE_PATCH_MARKER}
if ("amh", "rag_agent") not in AGENT_TYPE_MAPPING:
    AGENT_TYPE_MAPPING.insert(0, ("amh", "rag_agent"))
# END AMH MemoryData prompt-template patch
"""
    path.write_text(patched, encoding="utf-8")


def _patch_initialization(path: Path) -> None:
    if not path.is_file():
        return
    source = path.read_text(encoding="utf-8")
    if AMH_INITIALIZATION_PATCH_MARKER in source:
        return
    anchor = "    a_mem_checkpoint = None\n"
    if anchor not in source:
        raise RuntimeError(f"Cannot patch MemoryData initialization AMH marker logic: {path}")
    patch = f"""    {AMH_INITIALIZATION_PATCH_MARKER}
    if "amh" in agent_name:
        amh_marker_path = os.path.join(agent_save_folder, "amh_ready.txt")
        should_load_existing_agent = os.path.exists(amh_marker_path)
    # END AMH MemoryData initialization patch
"""
    path.write_text(source.replace(anchor, patch + anchor), encoding="utf-8")


def _patch_main_query_start(path: Path) -> None:
    if not path.is_file():
        return
    source = path.read_text(encoding="utf-8")
    if (
        AMH_MAIN_QUERY_START_PATCH_MARKER in source
        and "--query_start_index" in source
        and "query_start_index, agent_config" in source
        and "args.query_start_index" in source
    ):
        return

    max_queries_arg = """    parser.add_argument('--max_test_queries_ablation', type=int, default=0,
                       help='Limit maximum test queries for ablation studies (0 = no limit)')
"""
    query_start_arg = max_queries_arg + f"""    {AMH_MAIN_QUERY_START_PATCH_MARKER}
    parser.add_argument('--query_start_index', type=int, default=0,
                       help='Skip queries before this global query index for sharded runs')
    # END AMH MemoryData query-start patch
"""
    source = _replace_required(
        source,
        max_queries_arg,
        query_start_arg,
        description="MemoryData query_start_index CLI argument",
    )

    source = _replace_required(
        source,
        """def process_queries_for_context(agent, query_answer_pairs, dataset_config, metrics, results,
                               query_index, context_index, skipped_query_ids, max_queries,
                               agent_config, output_path, time_cost_list, start_time,
                               context_native_timing=None):
""",
        """def process_queries_for_context(agent, query_answer_pairs, dataset_config, metrics, results,
                               query_index, context_index, skipped_query_ids, max_queries,
                               query_start_index, agent_config, output_path, time_cost_list, start_time,
                               context_native_timing=None):
""",
        description="MemoryData process_queries_for_context query_start_index parameter",
    )
    source = _replace_required(
        source,
        """        # Check if we've reached the query limit for ablation studies
        if has_reached_query_limit(max_queries, query_index):
""",
        """        if query_index < query_start_index:
            query_index += 1
            continue

        # Check if we've reached the query limit for ablation studies
        if has_reached_query_limit(max_queries, query_index):
""",
        description="MemoryData per-query shard skip",
    )
    source = _replace_required(
        source,
        """def process_context(context_index, context_chunks, query_answer_pairs, agent_config, dataset_config,
                   metrics, results, query_index, completed_context_ids, skipped_query_ids,
                   max_queries, output_path, time_cost_list, start_time, force_rerun, total_contexts):
""",
        """def process_context(context_index, context_chunks, query_answer_pairs, agent_config, dataset_config,
                   metrics, results, query_index, completed_context_ids, skipped_query_ids,
                   max_queries, query_start_index, output_path, time_cost_list, start_time, force_rerun, total_contexts):
""",
        description="MemoryData process_context query_start_index parameter",
    )
    source = _replace_required(
        source,
        """    # Break early if we've reached the query limit
    if has_reached_query_limit(max_queries, query_index):
""",
        """    if query_index + len(query_answer_pairs) <= query_start_index:
        return metrics, results, query_index + len(query_answer_pairs), False

    # Break early if we've reached the query limit
    if has_reached_query_limit(max_queries, query_index):
""",
        description="MemoryData pre-context shard skip",
    )
    source = _replace_required(
        source,
        """        query_index, context_index, skipped_query_ids, max_queries,
        agent_config, output_path, time_cost_list, start_time,
""",
        """        query_index, context_index, skipped_query_ids, max_queries,
        query_start_index, agent_config, output_path, time_cost_list, start_time,
""",
        description="MemoryData process_queries_for_context query_start_index call",
    )
    source = _replace_required(
        source,
        """            args.max_test_queries_ablation, output_path, time_cost_list, start_time,
            args.force, total_contexts
""",
        """            args.max_test_queries_ablation, args.query_start_index, output_path, time_cost_list, start_time,
            args.force, total_contexts
""",
        description="MemoryData main process_context query_start_index call",
    )
    path.write_text(source, encoding="utf-8")


def _replace_required(source: str, old: str, new: str, *, description: str) -> str:
    if old not in source:
        raise RuntimeError(f"Cannot patch {description}: expected anchor not found")
    return source.replace(old, new, 1)


def _hybrid_amh_config(amh_repo_root: Path) -> str:
    root = json.dumps(str(amh_repo_root))
    return f"""# ── required ──────────────────────────────────────────────────────────────────
agent_name: hybrid_amh
model: gui-owl-1.5:latest
temperature: 0.7
input_length_limit: 12000
buffer_length: 4000
output_dir: ./results/outputs/gui-owl-amh
retrieve_num: 5
agent_chunk_size: 1024
agent_variant_tag: bm25-rrf

# ── optional (LLM answer generation) ──────────────────────────────────────────
provider: openai_compatible
api_key_env: OPENAI_API_KEY
base_url: http://127.0.0.1:11434/v1
base_url_env:
tokenizer_encoding: cl100k_base

# ── optional (AMH retrieval backend) ──────────────────────────────────────────
amh_repo_root: {root}
amh_bm25_weight: 1.0
amh_vector_weight: 0.0
amh_query_expansion: true
amh_apply_decay: false
amh_record_access: false
amh_bm25_top: 50
amh_vector_top: 50
"""


AMH_AGENT_PATCH = r'''
# BEGIN AMH MemoryData adapter patch
def _amh_memorydata_initialize_agent(self, agent_config, dataset_config):
    from methods.amh.amh_adapter import AMHMemoryDataAdapter

    self.retrieve_num = int(agent_config.get("retrieve_num", getattr(self, "retrieve_num", 10)))
    self.chunk_size = int(dataset_config.get("chunk_size", getattr(self, "chunk_size", 0) or 0))
    self.context_id = -1
    self.amh_marker_path = os.path.join(self.agent_save_to_folder, "amh_ready.txt")
    self.amh_adapter = AMHMemoryDataAdapter(
        agent_config=agent_config,
        dataset_config=dataset_config,
        agent_save_to_folder=self.agent_save_to_folder,
    )
    self.client = self._create_oai_client()
    self.agent_start_time = time.time()


_amh_original_initialize_agent_by_type = AgentWrapper._initialize_agent_by_type


def _amh_patched_initialize_agent_by_type(self, agent_config, dataset_config):
    if self._is_agent_type("amh"):
        return _amh_memorydata_initialize_agent(self, agent_config, dataset_config)
    return _amh_original_initialize_agent_by_type(self, agent_config, dataset_config)


AgentWrapper._initialize_agent_by_type = _amh_patched_initialize_agent_by_type


def _amh_memorydata_handle_agent(self, message, memorizing, query_id, context_id):
    if memorizing:
        self.amh_adapter.memorize(message, context_id=context_id)
        return ""

    start_time = time.time()
    retrieval_query = self._extract_retrieval_query(message)
    retrieved = self.amh_adapter.retrieve(
        retrieval_query,
        context_id=context_id,
        top_k=int(getattr(self, "retrieve_num", 10) or 10),
    )
    raw_contexts = [entry["text"] for entry in retrieved]
    cleaned_contexts = self._clean_retrieved_memory_contexts(raw_contexts) or raw_contexts

    try:
        system_message = get_template(self.sub_dataset, "system", self.agent_name)
    except Exception:
        system_message = ""

    if hasattr(self, "_fit_memories_for_answer"):
        fitted_contexts = self._fit_memories_for_answer(
            message,
            cleaned_contexts,
            label_prefix="Memory",
        )
    else:
        fitted_contexts = self._fit_retrieved_contexts_to_token_limit(
            cleaned_contexts,
            message,
            getattr(self, "tokenizer", None),
            system_message=system_message,
        )
    memory_construction_time = time.time() - start_time
    memories_text = "\n".join(
        f"Memory {index}:\n{text}"
        for index, text in enumerate(fitted_contexts, start=1)
    )
    answer, prompt_tokens, completion_tokens = self._generate_answer_from_memories(
        message,
        memories_text,
    )
    query_time_len = time.time() - start_time - memory_construction_time
    output = self._create_standard_response(
        answer,
        prompt_tokens,
        completion_tokens,
        memory_construction_time,
        query_time_len,
    )
    output["retrieval_context"] = fitted_contexts
    output["retrieved_memory_ids"] = [entry["id"] for entry in retrieved]

    retrieved_source_id_groups = self._extract_locomo_source_id_groups_from_texts(raw_contexts)
    output = self._attach_locomo_recall_metadata(output, retrieved_source_id_groups)

    if fitted_contexts:
        self._save_retrieval_debug_payload(
            fitted_contexts,
            query_id,
            context_id,
            extra_fields={
                "query": message,
                "retrieval_query": retrieval_query,
                "response": output.get("output"),
                "retrieved_memory_ids": output.get("retrieved_memory_ids"),
                "retrieved_source_id_groups": output.get("retrieved_source_id_groups"),
            },
        )
    self.context_id = context_id
    self.agent_start_time = time.time()
    return output


_amh_original_send_message = AgentWrapper.send_message


def _amh_patched_send_message(self, message, memorizing=False, query_id=None, context_id=None, eval_metadata=None):
    if self._is_agent_type("amh"):
        if memorizing:
            self._current_query_id = None
            self._current_context_id = context_id
            self._current_eval_metadata = None
            self._last_llm_trace = None
        else:
            self._current_query_id = query_id
            self._current_context_id = context_id
            self._current_eval_metadata = eval_metadata
            self._last_llm_trace = None
        message = self._normalize_message_payload(message, memorizing=memorizing) if hasattr(self, "_normalize_message_payload") else message
        return _amh_memorydata_handle_agent(self, message, memorizing, query_id, context_id)
    return _amh_original_send_message(self, message, memorizing, query_id, context_id, eval_metadata)


AgentWrapper.send_message = _amh_patched_send_message


_amh_original_save_agent = AgentWrapper.save_agent


def _amh_patched_save_agent(self):
    if self._is_agent_type("amh"):
        os.makedirs(self.agent_save_to_folder, exist_ok=True)
        self.amh_adapter.save()
        with open(self.amh_marker_path, "w", encoding="utf-8") as file:
            file.write("ready")
        print("\n\n Agent saved...\n\n")
        return
    return _amh_original_save_agent(self)


AgentWrapper.save_agent = _amh_patched_save_agent


_amh_original_load_agent = AgentWrapper.load_agent


def _amh_patched_load_agent(self):
    if self._is_agent_type("amh"):
        if not os.path.exists(self.amh_marker_path):
            raise FileNotFoundError(f"AMH marker not found at {self.amh_marker_path}")
        self.amh_adapter.load()
        print("\n\n Agent loaded successfully...\n\n")
        return
    return _amh_original_load_agent(self)


AgentWrapper.load_agent = _amh_patched_load_agent
# END AMH MemoryData adapter patch
'''


AMH_ADAPTER_SOURCE = r'''from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AMHMemoryDataAdapter:
    """Thin MemoryData method adapter backed by AMH HubIndex + Retriever."""

    def __init__(self, agent_config: dict[str, Any], dataset_config: dict[str, Any], agent_save_to_folder: str) -> None:
        self.agent_config = dict(agent_config)
        self.dataset_config = dict(dataset_config)
        self.agent_save_to_folder = Path(agent_save_to_folder)
        self.repo_root = _resolve_amh_repo_root(self.agent_config, Path(__file__))
        if str(self.repo_root) not in sys.path:
            sys.path.insert(0, str(self.repo_root))

        from agent_brain.memory.recall.embedding_text import embedding_text_for_item
        from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
        from agent_brain.memory.store.items_store import ItemsStore
        from agent_brain.platform.embedding import HashingEmbedder
        from agent_brain.platform.indexing.index import HubIndex

        self._embedding_text_for_item = embedding_text_for_item
        self._SearchFilter = SearchFilter
        self.brain_dir = Path(
            self.agent_config.get("amh_brain_dir")
            or self.agent_save_to_folder / "amh_brain"
        )
        self.items_store = ItemsStore(self.brain_dir / "items")
        self.embedder = HashingEmbedder()
        self.index = HubIndex(self.brain_dir / "index.db", embedding_dim=self.embedder.dim)
        self.retriever = Retriever(
            index=self.index,
            embedder=self.embedder,
            bm25_weight=float(self.agent_config.get("amh_bm25_weight", 1.0)),
            vector_weight=float(self.agent_config.get("amh_vector_weight", 0.0)),
            query_expansion=bool(self.agent_config.get("amh_query_expansion", True)),
            apply_decay=bool(self.agent_config.get("amh_apply_decay", False)),
            record_access=bool(self.agent_config.get("amh_record_access", False)),
            bm25_top=int(self.agent_config.get("amh_bm25_top", 50)),
            vector_top=int(self.agent_config.get("amh_vector_top", 50)),
        )
        self._body_by_id: dict[str, str] = {}
        self._load_bodies()

    def memorize(self, text: str, *, context_id: int | None) -> str:
        body = str(text or "").strip()
        if not body:
            return ""

        item = self._memory_item(body, context_id=context_id)
        existing_body = self._body_by_id.get(item.id)
        if existing_body == body:
            return item.id
        if (self.items_store.items_dir / f"{item.id}.md").exists():
            self._body_by_id[item.id] = body
            return item.id
        self.items_store.write(item, body)
        self.index.upsert(
            item,
            body,
            self.embedder.embed(self._embedding_text_for_item(item)),
        )
        self._body_by_id[item.id] = body
        return item.id

    def retrieve(self, query: str, *, context_id: int | None, top_k: int) -> list[dict[str, Any]]:
        tags = [self._context_tag(context_id)] if context_id is not None else []
        hits = self.retriever.search(
            str(query or ""),
            top_k=top_k,
            filters=self._SearchFilter(
                project="memorydata",
                tags=tags,
                include_superseded=True,
                include_stale_state=True,
            ),
        )
        if not hits and tags:
            hits = self.retriever.search(
                str(query or ""),
                top_k=top_k,
                filters=self._SearchFilter(
                    project="memorydata",
                    include_superseded=True,
                    include_stale_state=True,
                ),
            )

        texts = self.index.get_texts([hit.id for hit in hits])
        rows = []
        for rank, hit in enumerate(hits, start=1):
            text = self._body_by_id.get(hit.id) or _body_from_index_text(texts.get(hit.id, ""))
            rows.append(
                {
                    "id": hit.id,
                    "rank": rank,
                    "score": hit.score,
                    "bm25_rank": hit.bm25_rank,
                    "vector_rank": hit.vector_rank,
                    "text": text,
                }
            )
        return rows

    def save(self) -> None:
        self.agent_save_to_folder.mkdir(parents=True, exist_ok=True)
        manifest = {
            "adapter": "AMHMemoryDataAdapter",
            "backend": "AMH HubIndex + Retriever BM25/RRF pipeline",
            "brain_dir": str(self.brain_dir),
            "repo_root": str(self.repo_root),
            "retriever_config": {
                "bm25_weight": float(self.agent_config.get("amh_bm25_weight", 1.0)),
                "vector_weight": float(self.agent_config.get("amh_vector_weight", 0.0)),
                "query_expansion": bool(self.agent_config.get("amh_query_expansion", True)),
                "apply_decay": bool(self.agent_config.get("amh_apply_decay", False)),
                "record_access": bool(self.agent_config.get("amh_record_access", False)),
            },
        }
        (self.agent_save_to_folder / "amh_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load(self) -> None:
        self._load_bodies()

    def close(self) -> None:
        self.index.close()

    def _load_bodies(self) -> None:
        self._body_by_id.clear()
        for item, body in self.items_store.iter_all():
            self._body_by_id[item.id] = body

    def _memory_item(self, body: str, *, context_id: int | None):
        from agent_brain.contracts.memory_item import MemoryItem

        digest = hashlib.sha1(
            f"{self.dataset_config.get('sub_dataset')}|{context_id}|{body}".encode("utf-8")
        ).hexdigest()[:16]
        compact = _compact(body, limit=320)
        sub_dataset = str(self.dataset_config.get("sub_dataset") or "memorydata")
        context_tag = self._context_tag(context_id)
        return MemoryItem.model_validate(
            {
                "id": f"mem-20260702-000000-memorydata-amh-{digest}",
                "type": "fact",
                "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc).isoformat(),
                "agent": "benchmark",
                "session": str(context_id) if context_id is not None else None,
                "project": "memorydata",
                "tags": ["memorydata", "amh", _tag_slug(sub_dataset), context_tag],
                "sensitivity": "public",
                "title": f"MemoryData context {context_id} chunk",
                "summary": compact,
                "refs": {"urls": ["https://github.com/OpenDataBox/MemoryData"]},
                "confidence": 0.9,
                "abstraction": "L0",
                "maturity": "raw",
                "context_views": {
                    "locator": compact,
                    "overview": body,
                    "detail_uri": f"memorydata://{sub_dataset}/context/{context_id}/{digest}",
                },
                "source": {"kind": "benchmark", "extractor": "memorydata-amh"},
            }
        )

    @staticmethod
    def _context_tag(context_id: int | None) -> str:
        return f"context-{context_id}" if context_id is not None else "context-none"


def _resolve_amh_repo_root(agent_config: dict[str, Any], adapter_file: Path) -> Path:
    configured = str(agent_config.get("amh_repo_root") or os.environ.get("AMH_REPO_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for parent in adapter_file.resolve().parents:
        if (parent / "agent_brain" / "__init__.py").is_file():
            return parent
    raise RuntimeError(
        "Cannot locate AMH repository root. Set 'amh_repo_root' in config/hybrid_amh.yaml "
        "or export AMH_REPO_ROOT."
    )


def _compact(text: str, *, limit: int) -> str:
    one_line = " ".join(str(text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3].rstrip() + "..."


def _tag_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.").lower()
    return slug or "memorydata"


def _body_from_index_text(text: str) -> str:
    return str(text or "").strip()


__all__ = ["AMHMemoryDataAdapter"]
'''


__all__ = ["AMH_PATCH_MARKER", "materialize_memorydata_amh_adapter"]
