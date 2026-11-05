import pytest


@pytest.fixture(autouse=True)
def _plain_keyword_hook_recall(monkeypatch):
    """Most native tests exercise transport, delivery and views on a Hub
    without a semantic model; they need plain keyword injection. The degraded
    lexical guard has its own tests (test_lexical_guard.py), which turn it on."""
    monkeypatch.setenv("AMH_NATIVE_HOOK_LEXICAL_GUARD", "0")
    # Sampling depends on the trace id: tests that count delivery reports must not
    # be asked at random. test_self_feedback.py sets the rate it wants.
    monkeypatch.setenv("AMH_NATIVE_SELF_FEEDBACK_RATE", "0")
