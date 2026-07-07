from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path


def test_memorydata_amh_materializer_routes_agent_wrapper(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.evaluation.memorydata_amh import materialize_memorydata_amh_adapter

    memorydata_repo = tmp_path / "MemoryData"
    _write_fixture_memorydata_agent(memorydata_repo)

    manifest = materialize_memorydata_amh_adapter(memorydata_repo)

    assert manifest["agent_config"] == "config/hybrid_amh.yaml"
    assert (memorydata_repo / "config" / "hybrid_amh.yaml").is_file()
    assert (memorydata_repo / "methods" / "amh" / "amh_adapter.py").is_file()

    agent_config_source = (memorydata_repo / "config" / "hybrid_amh.yaml").read_text(encoding="utf-8")
    assert "input_length_limit: 12000" in agent_config_source
    assert "retrieve_num: 5" in agent_config_source
    assert "agent_chunk_size: 1024" in agent_config_source

    agent_source = (memorydata_repo / "utils" / "agent.py").read_text(encoding="utf-8")
    assert "BEGIN AMH MemoryData adapter patch" in agent_source
    assert "AgentWrapper._initialize_agent_by_type" in agent_source
    assert "self.client = self._create_oai_client()" in agent_source
    assert "_fit_memories_for_answer" in agent_source
    assert "AgentWrapper.send_message" in agent_source
    assert "AgentWrapper.save_agent" in agent_source
    assert "AgentWrapper.load_agent" in agent_source

    main_source = (memorydata_repo / "main.py").read_text(encoding="utf-8")
    assert "BEGIN AMH MemoryData query-start patch" in main_source
    assert "--query_start_index" in main_source

    conversation_source = (memorydata_repo / "utils" / "conversation_creator.py").read_text(encoding="utf-8")
    assert "BEGIN AMH MemoryData conversation patch" in conversation_source
    assert '"amh"' in conversation_source

    artifact_paths_source = (memorydata_repo / "utils" / "artifact_paths.py").read_text(encoding="utf-8")
    assert "BEGIN AMH MemoryData artifact-path patch" in artifact_paths_source
    assert '"amh"' in artifact_paths_source

    prompt_source = (
        memorydata_repo
        / "benchmark"
        / "memoryagentbench"
        / "prompts"
        / "benchmark_templates.py"
    ).read_text(encoding="utf-8")
    assert "BEGIN AMH MemoryData prompt-template patch" in prompt_source
    assert '("amh", "rag_agent")' in prompt_source

    initialization_source = (memorydata_repo / "utils" / "initialization.py").read_text(encoding="utf-8")
    assert "BEGIN AMH MemoryData initialization patch" in initialization_source
    assert "amh_ready.txt" in initialization_source

    monkeypatch.syspath_prepend(str(memorydata_repo))
    module = _load_module(memorydata_repo / "utils" / "agent.py")
    agent = module.AgentWrapper(
        {
            "agent_name": "hybrid_amh",
            "retrieve_num": 2,
            "amh_repo_root": str(Path.cwd()),
            "amh_vector_weight": 0.0,
            "amh_bm25_weight": 1.0,
        },
        {"sub_dataset": "fixture", "chunk_size": 128},
        str(tmp_path / "agent-state"),
    )

    assert agent.client == "fixture-openai-compatible-client"

    agent.send_message("alpha project plan lives here", memorizing=True, context_id=0)
    agent.amh_adapter._body_by_id.clear()
    agent.send_message("alpha project plan lives here", memorizing=True, context_id=0)
    agent.send_message("beta unrelated note", memorizing=True, context_id=1)

    output = agent.send_message("alpha plan", memorizing=False, query_id=7, context_id=0)

    assert agent.fit_memories_for_answer_called is True
    assert output["output"].startswith("answered:")
    assert output["retrieval_context"] == ["alpha project plan lives here"]
    assert output["retrieved_source_id_groups"] == [["source-alpha"]]

    agent.save_agent()
    assert (tmp_path / "agent-state" / "amh_ready.txt").read_text(encoding="utf-8") == "ready"

    reloaded = module.AgentWrapper(
        {
            "agent_name": "hybrid_amh",
            "retrieve_num": 2,
            "amh_repo_root": str(Path.cwd()),
            "amh_vector_weight": 0.0,
            "amh_bm25_weight": 1.0,
        },
        {"sub_dataset": "fixture", "chunk_size": 128},
        str(tmp_path / "agent-state"),
    )
    reloaded.load_agent()
    reloaded_output = reloaded.send_message("alpha plan", memorizing=False, query_id=8, context_id=0)
    assert reloaded_output["retrieval_context"] == ["alpha project plan lives here"]


def test_memorydata_amh_materializer_refreshes_existing_agent_patch(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_amh import materialize_memorydata_amh_adapter

    memorydata_repo = tmp_path / "MemoryData"
    _write_fixture_memorydata_agent(memorydata_repo)
    agent_path = memorydata_repo / "utils" / "agent.py"
    agent_path.write_text(
        agent_path.read_text(encoding="utf-8").rstrip()
        + "\n\n# BEGIN AMH MemoryData adapter patch\nstale_patch = True\n# END AMH MemoryData adapter patch\n",
        encoding="utf-8",
    )

    materialize_memorydata_amh_adapter(memorydata_repo)

    agent_source = agent_path.read_text(encoding="utf-8")
    assert "stale_patch = True" not in agent_source
    assert "self.client = self._create_oai_client()" in agent_source
    assert agent_source.count("# BEGIN AMH MemoryData adapter patch") == 1


def test_memorydata_runner_materializes_amh_preset_before_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.evaluation import memorydata_runner
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, run_memorydata

    memorydata_repo = tmp_path / "MemoryData"
    memorydata_repo.mkdir()
    (memorydata_repo / "main.py").write_text("print('fixture')\n", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"
    materialized: dict[str, Path] = {}

    def fake_materialize(repo: Path) -> dict[str, str]:
        materialized["repo"] = Path(repo)
        return {"status": "materialized"}

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(memorydata_runner, "materialize_memorydata_amh_adapter", fake_materialize)
    monkeypatch.setattr(memorydata_runner.subprocess, "run", fake_run)

    run = run_memorydata(
        MemoryDataRunOptions(
            memorydata_repo=memorydata_repo,
            agent_config="config/hybrid_amh.yaml",
            artifact_root=artifact_root,
        ),
        prereqs={
            "dependencies_ready": True,
            "datasets_ready": True,
            "endpoint_ready": True,
        },
    )

    assert run["status"] == "passed"
    assert materialized["repo"] == memorydata_repo


def test_memorydata_runner_command_supports_query_start_shard(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, memorydata_command

    command = memorydata_command(
        MemoryDataRunOptions(
            memorydata_repo=tmp_path / "MemoryData",
            agent_config="config/hybrid_amh.yaml",
            artifact_root=tmp_path / "artifacts",
            max_test_queries=100,
            query_start_index=50,
        )
    )

    assert "--max_test_queries_ablation" in command
    assert "100" in command
    assert "--query_start_index" in command
    assert "50" in command


def test_memorydata_runner_command_supports_named_matrix_family_configs(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_runner import MemoryDataRunOptions, memorydata_command

    ttl_command = memorydata_command(
        MemoryDataRunOptions(
            memorydata_repo=tmp_path / "MemoryData",
            family="MemoryAgentBenchTTL",
            artifact_root=tmp_path / "artifacts",
        )
    )
    noisy_command = memorydata_command(
        MemoryDataRunOptions(
            memorydata_repo=tmp_path / "MemoryData",
            family="MemBenchNoisy",
            artifact_root=tmp_path / "artifacts",
        )
    )

    assert "benchmark/memoryagentbench/Test_Time_Learning/config/ICL/ICL_banking77.yaml" in ttl_command
    assert "benchmark/membench/config/MemBench_noisy.yaml" in noisy_command


def _load_module(path: Path):
    module_name = f"fixture_memorydata_agent_{path.parent.parent.name}"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture_memorydata_agent(memorydata_repo: Path) -> None:
    memorydata_repo.mkdir(parents=True)
    (memorydata_repo / "main.py").write_text(
        """
from argparse import ArgumentParser


def parse_command_line_arguments():
    parser = ArgumentParser()
    parser.add_argument('--max_test_queries_ablation', type=int, default=0,
                       help='Limit maximum test queries for ablation studies (0 = no limit)')
    return parser.parse_args()


def process_queries_for_context(agent, query_answer_pairs, dataset_config, metrics, results,
                               query_index, context_index, skipped_query_ids, max_queries,
                               agent_config, output_path, time_cost_list, start_time,
                               context_native_timing=None):
    for query_data in query_answer_pairs:
        if should_skip_query(query_index, skipped_query_ids):
            query_index += 1
            continue
        # Check if we've reached the query limit for ablation studies
        if has_reached_query_limit(max_queries, query_index):
            break
        query_index += 1
    return metrics, results, query_index


def process_context(context_index, context_chunks, query_answer_pairs, agent_config, dataset_config,
                   metrics, results, query_index, completed_context_ids, skipped_query_ids,
                   max_queries, output_path, time_cost_list, start_time, force_rerun, total_contexts):
    if should_skip_context(force_rerun, context_index, completed_context_ids):
        return metrics, results, query_index + len(query_answer_pairs), False
    # Break early if we've reached the query limit
    if has_reached_query_limit(max_queries, query_index):
        return metrics, results, query_index, True
    metrics, results, query_index = process_queries_for_context(
        None, query_answer_pairs, dataset_config, metrics, results,
        query_index, context_index, skipped_query_ids, max_queries,
        agent_config, output_path, time_cost_list, start_time,
        context_native_timing=None,
    )
    return metrics, results, query_index, False


def main(args, all_context_chunks, all_query_answer_pairs):
    query_index = 0
    output_path = 'output.json'
    time_cost_list = []
    start_time = 0
    total_contexts = len(all_context_chunks)
    for context_index, (context_chunks, query_answer_pairs) in enumerate(
        zip(all_context_chunks, all_query_answer_pairs)
    ):
        metrics, results, query_index, should_break = process_context(
            context_index, context_chunks, query_answer_pairs, {}, {},
            {}, [], query_index, set(), set(),
            args.max_test_queries_ablation, output_path, time_cost_list, start_time,
            args.force, total_contexts
        )
        if should_break:
            break
""".lstrip(),
        encoding="utf-8",
    )
    (memorydata_repo / "config").mkdir(parents=True)
    (memorydata_repo / "methods").mkdir()
    (memorydata_repo / "methods" / "__init__.py").write_text("", encoding="utf-8")
    (memorydata_repo / "utils").mkdir()
    (memorydata_repo / "utils" / "__init__.py").write_text("", encoding="utf-8")
    (memorydata_repo / "utils" / "agent.py").write_text(
        """
import os
import time


class AgentWrapper:
    def __init__(self, agent_config, dataset_config, load_agent_from):
        self.agent_config = agent_config
        self.dataset_config = dataset_config
        self.agent_name = agent_config["agent_name"]
        self.sub_dataset = dataset_config["sub_dataset"]
        self.retrieve_num = agent_config.get("retrieve_num", 2)
        self.chunk_size = dataset_config.get("chunk_size", 128)
        self.agent_save_to_folder = load_agent_from
        self.agent_start_time = time.time()
        self._current_eval_metadata = None
        self.fit_memories_for_answer_called = False
        self._initialize_agent_by_type(agent_config, dataset_config)

    def _is_agent_type(self, agent_type):
        return agent_type in self.agent_name

    def _initialize_agent_by_type(self, agent_config, dataset_config):
        self.original_initialized = True

    def _create_oai_client(self):
        return "fixture-openai-compatible-client"

    def send_message(self, message, memorizing=False, query_id=None, context_id=None, eval_metadata=None):
        return {"original": True}

    def save_agent(self):
        self.original_saved = True

    def load_agent(self):
        self.original_loaded = True

    def _prepare_memory_chunk_for_storage(self, text):
        return str(text).strip()

    def _clean_retrieved_memory_contexts(self, contexts):
        return [str(context).strip() for context in contexts if str(context).strip()]

    def _extract_retrieval_query(self, message):
        return message

    def _fit_retrieved_contexts_to_token_limit(self, contexts, message, tokenizer=None, system_message=""):
        return list(contexts)

    def _fit_memories_for_answer(self, question, contexts, prompt_override=None, label_prefix="Memory"):
        self.fit_memories_for_answer_called = True
        return list(contexts[:1])

    def _generate_answer_from_memories(self, question, memories_text, prompt_override=None):
        return "answered:" + memories_text, 11, 3

    def _create_standard_response(self, output, input_tokens, output_tokens, memory_time, query_time):
        return {
            "output": output,
            "input_len": input_tokens,
            "output_len": output_tokens,
            "memory_construction_time": memory_time,
            "query_time_len": query_time,
        }

    def _extract_locomo_source_id_groups_from_texts(self, texts):
        groups = []
        for text in texts:
            if "alpha" in text:
                groups.append(["source-alpha"])
        return groups

    def _attach_locomo_recall_metadata(self, output, retrieved_source_id_groups):
        output["retrieved_source_id_groups"] = retrieved_source_id_groups
        return output

    def _save_retrieval_debug_payload(self, payload, query_id, context_id, extra_fields=None):
        self.last_retrieval_debug = {
            "payload": payload,
            "query_id": query_id,
            "context_id": context_id,
            "extra_fields": extra_fields,
        }
""".lstrip(),
        encoding="utf-8",
    )
    (memorydata_repo / "utils" / "conversation_creator.py").write_text(
        '''
MEMORY_AGENT_NAME_HINTS = (
    "mem0",
    "simplemem",
)
'''.lstrip(),
        encoding="utf-8",
    )
    (memorydata_repo / "utils" / "artifact_paths.py").write_text(
        '''
_MEMORY_AGENT_HINTS = (
    "mem0",
    "simplemem",
)
'''.lstrip(),
        encoding="utf-8",
    )
    prompt_dir = memorydata_repo / "benchmark" / "memoryagentbench" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "__init__.py").write_text("", encoding="utf-8")
    (prompt_dir / "benchmark_templates.py").write_text(
        '''
AGENT_TYPE_MAPPING = [
    ("rag", "rag_agent"),
]
'''.lstrip(),
        encoding="utf-8",
    )
    (memorydata_repo / "utils" / "initialization.py").write_text(
        '''
def initialize_and_memorize_agent(agent_config):
    agent_name = agent_config.get("agent_name", "")
    should_load_existing_agent = False
    a_mem_checkpoint = None
'''.lstrip(),
        encoding="utf-8",
    )
