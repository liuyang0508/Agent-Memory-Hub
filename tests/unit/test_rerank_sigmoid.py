"""P1-5: cross-encoder logits must be squashed to (0,1) before decay.

Raw logits are often negative; the decay step multiplies score by confidence ×
retention_factor (both in [0,1]). With negative scores this *raises* worse docs
(magnitude shrinks toward 0), inverting the ranking. Sigmoid keeps it monotonic.
"""
import math

from agent_brain.memory.recall.retrieval import _sigmoid


def test_sigmoid_range_in_unit_interval():
    # At float precision the tails saturate to exactly 0.0 / 1.0; the contract
    # that matters for decay composition is 0 <= y <= 1 (never negative).
    for x in (-50.0, -8.0, -1.0, 0.0, 1.0, 8.0, 50.0):
        y = _sigmoid(x)
        assert 0.0 <= y <= 1.0
    # Mid-range values stay strictly interior.
    assert 0.0 < _sigmoid(-1.0) < _sigmoid(1.0) < 1.0


def test_sigmoid_monotonic_increasing():
    xs = [-10.0, -3.0, -0.5, 0.0, 0.5, 3.0, 10.0]
    ys = [_sigmoid(x) for x in xs]
    assert ys == sorted(ys)
    assert all(b > a for a, b in zip(ys, ys[1:]))


def test_sigmoid_zero_is_half():
    assert _sigmoid(0.0) == 0.5


def test_negative_logit_order_preserved_after_decay():
    """A better doc (higher logit) must keep a higher score after ×decay∈(0,1]."""
    better, worse = _sigmoid(-1.0), _sigmoid(-4.0)
    decay = 0.6  # any positive multiplier
    assert better * decay > worse * decay


def test_no_overflow_on_large_negative():
    # math.exp(-(-1000)) would overflow; the branch must guard it.
    assert _sigmoid(-1000.0) >= 0.0
