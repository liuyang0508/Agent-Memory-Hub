"""Evaluation helpers for release, retrieval, and memory-quality gates."""

__all__ = [
    "MemoryEvalCaseResult",
    "MemoryEvalHarness",
    "MemoryEvalReport",
    "default_suite",
    "load_suite",
]


def __getattr__(name: str):
    if name in __all__:
        from agent_brain.evaluation import memory_eval

        return getattr(memory_eval, name)
    raise AttributeError(name)
