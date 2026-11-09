"""Learned relevance for prompt-time recall: should this memory be injected?

Prompt-time recall is unasked: the member did not request it, so a wrong
memory costs attention and can steer the model the wrong way, while a missed
one can still be found by an explicit organization search. The hook therefore
injects a memory only when the evidence says it is probably relevant, and
says "nothing" otherwise.

The evidence combines independent signals, none of which is enough alone:

* ``sim``: best-window cosine between prompt and memory (semantic match);
* ``gap``: distance below the best match for this prompt (relative strength);
* ``doc_bg``: how similar the memory is to everyday prompts that carry no
  organization knowledge. Embedding spaces have "hubs", documents close to
  everything; this is the local-scaling correction of CSLS (Conneau et al.,
  2018) measured against a fixed bank of generic prompts;
* ``query_bg``: how similar the prompt is to the whole store (a generic prompt
  is close to everything and earns less trust), the query side of CSLS;
* ``lex_inv``: keyword search rank, as 1/rank (lexical corroboration);
* ``nav``: the memory is an index or summary section;
* ``body_cov``: share of the prompt's content bigrams found in the memory;
* ``anchor``: a literal identifier from the prompt (error code, symbol)
  occurs in the memory.

A logistic model turns them into a probability, fitted per embedding model on
an organization's gold set (``amh-native recall-calibrate``) and shipped in
the model manifest. Injection follows a decision rule: inject when the
probability clears the threshold chosen on held-out folds, keep at most a few,
and fold near-duplicates (the same note uploaded by several products or
devices) into one.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

FEATURES = ("sim", "gap", "doc_bg", "query_bg", "lex_inv", "nav", "body_cov", "anchor")
POOL_SEMANTIC = 10  # candidates by similarity the model judges
POOL_LEXICAL = 3  # plus the keyword search head
BACKGROUND_K = 10
SECTION_CHARS = 1400  # Longer memories are injected as the section the prompt matched.
LINE_REACH = 120  # How far back a section may start to open on a whole line.

# Everyday prompts with no organization knowledge: the background a memory's
# similarity is measured against. Fixed and versioned with the gate.
BANK = (
    "帮我优化一下这段代码的性能",
    "这个函数为什么返回 None",
    "给我写个单元测试",
    "帮我重构一下这个类",
    "这个报错是什么意思",
    "帮我写一个 shell 脚本批量重命名文件",
    "怎么在 Python 里读 Excel",
    "JavaScript 的闭包是什么",
    "帮我写个二分查找",
    "解释一下这个算法的时间复杂度",
    "怎么配置 nginx 反向代理",
    "Linux 怎么查看端口占用",
    "帮我写一份会议纪要",
    "把这段话润色一下",
    "帮我总结一下这篇文章",
    "写一个产品需求文档的大纲",
    "帮我起个项目名字",
    "这个接口返回 500 了",
    "TypeError: cannot read properties of undefined",
    "帮我写个正则匹配手机号",
    "怎么用 pandas 去重",
    "Go 的 goroutine 怎么用",
    "帮我设计一个数据库表结构",
    "MySQL 索引怎么建",
    "Redis 缓存击穿怎么办",
    "帮我画个流程图",
    "用 Vue 写一个表单",
    "CSS 怎么让元素居中",
    "怎么写一个好的 commit message",
    "帮我review一下这个改动",
    "这个变量命名合适吗",
    "继续往下写",
    "不对，重新来",
    "再详细一点",
    "换个思路试试",
    "你确定吗",
    "有没有更简单的写法",
    "帮我检查下拼写错误",
    "把注释改成英文",
    "这个依赖版本冲突了",
    "npm install 失败了",
    "pip 安装太慢怎么办",
    "怎么用 git 撤销上一次提交",
    "写个爬虫抓取网页标题",
    "帮我写一个 Dockerfile",
    "k8s 的 pod 一直重启",
    "帮我算一下这个月的预算",
    "给客户回一封邮件",
    "帮我准备面试问题",
    "这个设计有什么风险",
    "How do I sort a dict by value in Python",
    "Write a function to reverse a linked list",
    "What is the difference between TCP and UDP",
    "Fix this failing test",
    "Explain this stack trace",
    "Summarize the following text",
    "Make this code more readable",
    "Add type hints to this function",
    "How do I center a div",
    "Generate a random password",
)

_STOP = frozenset(
    "一下 这个 那个 怎么 什么 帮我 我们 之前 是不 不是 有没 没有 可以 一个 还有 然后 就是 现在 时候".split()
)
_WORD = re.compile(r"[a-z0-9_.\-/]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


_HEADING = re.compile(r"^#{1,2} .*$", re.M)


def section_span(text: str, start: int, end: int, limit: int = SECTION_CHARS) -> tuple[int, int]:
    """The heading-delimited part of ``text`` around a matched window, at most ``limit`` chars.

    Aggregated notes run for thousands of characters across unrelated topics;
    their opening says nothing about the part a prompt matched.
    """
    low, high = 0, len(text)
    for heading in _HEADING.finditer(text):
        if heading.start() <= start:
            low = heading.start()
        elif heading.start() >= end:
            high = heading.start()
            break
    if high - low > limit:
        floor = low
        low = max(low, start - max(0, limit - (end - start)) // 2)
        # Open on a whole line: the next one if it comes before the match, else
        # this one's beginning when it is near.
        line = text.find("\n", low, start)
        if line >= 0:
            low = line + 1
        else:
            line = text.rfind("\n", max(floor, low - LINE_REACH), low)
            low = line + 1 if line >= 0 else low
        high = min(high, low + limit)
    return low, high


def terms(text: str) -> set[str]:
    """ASCII words and CJK character bigrams, case-folded."""
    folded = text.casefold()
    found = set(_WORD.findall(folded))
    for run in _CJK.findall(folded):
        found.update(run[i : i + 2] for i in range(len(run) - 1))
    return found


def query_terms(query: str) -> set[str]:
    return {term for term in terms(query) if term not in _STOP}


def coverage(wanted: set[str], text: str) -> float:
    """Share of ``wanted`` terms present in ``text``."""
    return len(wanted & terms(text)) / len(wanted) if wanted else 0.0


def top_mean(values: Sequence[float], k: int = BACKGROUND_K) -> float:
    best = sorted(values, reverse=True)[:k]
    return sum(best) / len(best) if best else 0.0


def shingles(text: str, width: int = 8) -> set[str]:
    """Every ``width``-character window: copies cut at other offsets still match."""
    return {text[i : i + width] for i in range(max(len(text) - width + 1, 1))}


def containment(a: set[str], b: set[str]) -> float:
    """Overlap relative to the smaller text: 1.0 when one contains the other."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


@dataclass
class GateModel:
    """A calibrated logistic relevance model and its injection rule."""

    mean: list[float]
    scale: list[float]
    weights: list[float]
    bias: float
    threshold: float = 0.3
    max_items: int = 3
    dedup: float = 0.5
    # A follower (2nd, 3rd memory) must reach this share of the leader's
    # probability: a strong match should not bring weak ones along.
    relative: float = 0.0
    # Absolute floor: a memory is injected in full only if it is at least this
    # much closer to the prompt than to everyday generic prompts (sim - doc_bg).
    # Rank features alone let a small store push its best match through any
    # prompt. A literal identifier from the prompt is exempt.
    lift: float = 0.0
    # Above 0, an index or summary file is never injected on its score (admitted).
    nav_threshold: float = 0.0
    version: str = "learned-v1"
    calibration: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GateModel:
        if raw.get("features") != list(FEATURES):
            raise ValueError("gate features do not match this version")
        model = cls(
            mean=[float(v) for v in raw["mean"]],
            scale=[float(v) for v in raw["scale"]],
            weights=[float(v) for v in raw["weights"]],
            bias=float(raw["bias"]),
            threshold=float(raw.get("threshold", 0.3)),
            max_items=int(raw.get("max_items", 3)),
            dedup=float(raw.get("dedup", 0.5)),
            relative=float(raw.get("relative", 0.0)),
            lift=float(raw.get("lift", 0.0)),
            nav_threshold=float(raw.get("nav_threshold", 0.0)),
            version=str(raw.get("version", "learned-v1")),
            calibration=dict(raw.get("calibration") or {}),
        )
        numbers = [
            *model.mean,
            *model.scale,
            *model.weights,
            model.bias,
            model.threshold,
            model.lift,
            model.nav_threshold,
        ]
        if (
            not all(len(v) == len(FEATURES) for v in (model.mean, model.scale, model.weights))
            or not all(math.isfinite(v) for v in numbers)
            or not all(v > 0 for v in model.scale)
            or not 0 < model.threshold < 1
            or not 1 <= model.max_items <= 8
            or not 0 < model.dedup <= 1
            or not 0 <= model.relative < 1
            or not -1 <= model.lift <= 1
            or not 0 <= model.nav_threshold <= 1
        ):
            raise ValueError("invalid gate model")
        return model

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "features": list(FEATURES),
            "mean": [round(v, 6) for v in self.mean],
            "scale": [round(v, 6) for v in self.scale],
            "weights": [round(v, 6) for v in self.weights],
            "bias": round(self.bias, 6),
            "threshold": self.threshold,
            "max_items": self.max_items,
            "dedup": self.dedup,
            "relative": self.relative,
            "lift": self.lift,
            "nav_threshold": self.nav_threshold,
            "calibration": self.calibration,
        }

    def probability(self, values: Sequence[float]) -> float:
        z = self.bias + sum(
            w * (v - m) / s for w, v, m, s in zip(self.weights, values, self.mean, self.scale)
        )
        return 1 / (1 + math.exp(-max(-60.0, min(60.0, z))))


def lift_of(values: Sequence[float]) -> float:
    """How much closer to this prompt than to everyday generic prompts."""
    return float(values[0] - values[FEATURES.index("doc_bg")])


def contains_identifier(text: str, anchors: Sequence[str]) -> bool:
    """A whole token of the folded text: "E500" is not found inside "E5001"."""
    for anchor in anchors:
        start = text.find(anchor)
        while start >= 0:
            end = start + len(anchor)
            before = text[start - 1] if start else " "
            after = text[end] if end < len(text) else " "
            if not (before.isascii() and before.isalnum()) and not (
                after.isascii() and after.isalnum()
            ):
                return True
            start = text.find(anchor, start + 1)
    return False


def _joins_ascii(text: str, index: int, step: int) -> bool:
    """A separator joins two parts of one identifier only when its other side is ASCII
    alphanumeric too: "澄海交付验收-3d7ae9" is a Chinese word and a code, not a longer code."""
    other = index + step
    return 0 <= other < len(text) and text[other].isascii() and text[other].isalnum()


def whole_name(text: str, name: str) -> bool:
    """The name itself, not part of a longer one: "terminal-bench" is not in
    "agentos-terminal-bench-local". A sentence may still end right after it, and a
    Chinese word joined to it by a hyphen is a prefix in another script, not a longer name."""
    start = text.find(name)
    while start >= 0:
        end = start + len(name)
        before = text[start - 1] if start else " "
        after = text[end] if end < len(text) else " "
        longer_before = before.isascii() and (
            before.isalnum() or (before in "_-./" and _joins_ascii(text, start - 1, -1))
        )
        longer_after = after.isascii() and (
            after.isalnum() or (after in "_-" and _joins_ascii(text, end, 1))
        )
        if not longer_before and not longer_after:
            return True
        start = text.find(name, start + 1)
    return False


def admitted(
    model: GateModel, probability: float, values: Sequence[float], threshold: float | None = None
) -> bool:
    """Whether a memory goes in front of the model unasked.

    An index or summary file spans many topics: its score says nothing about the part a
    reader would see, and on real prompts every one injected was off topic. It can still be
    read, and a prompt naming something in it finds it (retrieval.py).
    """
    if values[FEATURES.index("nav")] > 0 and model.nav_threshold > 0:
        return False
    bar = model.threshold if threshold is None else threshold
    return probability >= bar and lift_of(values) >= model.lift


def adopt_rule(target: GateModel, source: GateModel) -> GateModel:
    """Carry the injection rule (not the fitted weights) from one gate to another."""
    for name in (
        "max_items",
        "dedup",
        "relative",
        "lift",
        "nav_threshold",
    ):
        setattr(target, name, getattr(source, name))
    return target


def features(
    *,
    sim: float,
    top: float,
    doc_bg: float,
    query_bg: float,
    lexical_rank: int | None,
    navigation: bool,
    query_terms_: set[str],
    text: str,
    anchors: Sequence[str],
) -> list[float]:
    """One candidate's evidence, in FEATURES order."""
    folded = text.casefold()
    return [
        sim,
        sim - top,
        doc_bg,
        query_bg,
        1.0 / lexical_rank if lexical_rank else 0.0,
        1.0 if navigation else 0.0,
        coverage(query_terms_, text),
        1.0 if anchors and contains_identifier(folded, anchors) else 0.0,
    ]


def fit(
    rows: Sequence[Sequence[float]],
    labels: Sequence[float],
    *,
    l2: float = 1.0,
    weights: Sequence[float] | None = None,
) -> GateModel:
    """L2-regularized logistic regression on standardized features (Newton steps).

    ``weights`` are per-row sample weights (default 1): usage feedback enters
    calibration with less weight than a judged gold case.
    """
    import numpy as np

    x = np.asarray(rows, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    if x.ndim != 2 or len(x) < 10 or len(set(labels)) < 2:
        raise ValueError("calibration needs at least ten candidates of both classes")
    sample = np.ones(len(x)) if weights is None else np.asarray(weights, dtype=np.float64)
    if sample.shape != (len(x),) or not np.all(np.isfinite(sample)) or np.any(sample < 0):
        raise ValueError("sample weights must be finite, non-negative, one per row")
    mean, scale = x.mean(0), x.std(0)
    scale = np.where(scale < 1e-6, 1.0, scale)
    z = np.hstack([(x - mean) / scale, np.ones((len(x), 1))])
    w = np.zeros(z.shape[1])
    ridge = l2 * np.diag(np.r_[np.ones(len(w) - 1), 0.0])
    for _ in range(100):
        p = 1 / (1 + np.exp(-np.clip(z @ w, -60, 60)))
        gradient = z.T @ (sample * (p - y)) + ridge @ w
        hessian = (z * (sample * p * (1 - p))[:, None]).T @ z + ridge
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if float(np.abs(step).max()) < 1e-9:
            break
    return GateModel(
        mean=[float(v) for v in mean],
        scale=[float(v) for v in scale],
        weights=[float(v) for v in w[:-1]],
        bias=float(w[-1]),
    )


@dataclass
class Candidate:
    """One judged candidate of a calibration case."""

    values: list[float]
    relevant: bool
    shingles: set[str]
    duplicate_key: tuple[str, str]


def select(model: GateModel, candidates: Sequence[Candidate], threshold: float) -> list[Candidate]:
    """The injection rule, as retrieval applies it: probability, cap, near-duplicates."""
    scored = sorted(
        ((model.probability(c.values), c) for c in candidates), key=lambda pair: -pair[0]
    )
    chosen: list[Candidate] = []
    keys: set[tuple[str, str]] = set()
    leader = 1.0
    for probability, candidate in scored:
        if not admitted(model, probability, candidate.values, threshold):
            continue
        if candidate.duplicate_key in keys or any(
            containment(candidate.shingles, other.shingles) >= model.dedup for other in chosen
        ):
            continue
        if chosen and probability < model.relative * leader:
            continue
        keys.add(candidate.duplicate_key)
        if not chosen:
            leader = probability
        chosen.append(candidate)
        if len(chosen) >= model.max_items:
            break
    return chosen


Utility = Callable[[dict[str, Any]], float]
THRESHOLDS = tuple(step / 100 for step in range(15, 65, 5))


def utility(metrics: dict[str, Any]) -> float:
    """What the threshold optimizes: finding the right memory first, and not
    spending the member's attention on wrong ones. Weights are a product
    decision, recorded with every calibration."""
    return float(
        0.3 * metrics["hit@1"]
        + 0.2 * metrics["hit@3"]
        + 0.25 * metrics["precision"]
        + 0.25 * metrics["abstention"]
    )


def cross_validate(
    cases: Sequence[tuple[dict[str, Any], list[Candidate]]],
    *,
    thresholds: Sequence[float] = THRESHOLDS,
    folds: int = 5,
    seeds: int = 5,
    score: Utility = utility,
    extra: tuple[Sequence[Sequence[float]], Sequence[float], Sequence[float]] | None = None,
    relative: float = 0.0,
    rule: GateModel | None = None,
) -> dict[str, Any]:
    """Grouped k-fold by case: thresholds are judged only on cases the model never saw.

    ``extra`` (rows, labels, weights) joins every fold's training set and is
    never scored: usage feedback can teach the model, only gold cases judge it.
    """
    from .recall_eval import summarize

    by_threshold: dict[float, list[dict[str, Any]]] = {t: [] for t in thresholds}
    pooled: dict[float, list[dict[str, Any]]] = {t: [] for t in thresholds}
    for seed in range(seeds):
        order = list(range(len(cases)))
        random.Random(seed).shuffle(order)
        outcomes: dict[float, list[dict[str, Any]]] = {t: [] for t in thresholds}
        for fold in range(folds):
            held = set(order[fold::folds])
            rows: list[Sequence[float]] = [
                c.values for i in order if i not in held for c in cases[i][1]
            ]
            labels: list[float] = [
                1.0 if c.relevant else 0.0 for i in order if i not in held for c in cases[i][1]
            ]
            weights = [1.0] * len(rows)
            if extra is not None:
                rows = [*rows, *extra[0]]
                labels = [*labels, *extra[1]]
                weights = [*weights, *extra[2]]
            model = fit(rows, labels, weights=weights)
            if rule is not None:
                adopt_rule(model, rule)
            else:
                model.relative = relative
            for i in held:
                case, candidates = cases[i]
                for t in thresholds:
                    chosen = select(model, candidates, t)
                    outcomes[t].append(_judged(case, candidates, chosen))
        for t in thresholds:
            by_threshold[t].append(summarize(outcomes[t]))
            pooled[t].extend(outcomes[t])
    table = []
    for t in thresholds:
        runs = by_threshold[t]
        averaged = {
            key: round(sum(r[key] for r in runs) / len(runs), 3)
            for key in ("hit@1", "hit@3", "mrr", "precision", "abstention", "mean_returned")
        }
        table.append({"threshold": t, **averaged, "utility": round(score(averaged), 4)})
    top = max(table, key=lambda row: row["utility"])
    # One-standard-error rule (Breiman et al.): thresholds whose utility is
    # within one bootstrap standard error of the best are statistically tied;
    # among them keep the lowest, so a tie never costs the member a memory.
    error = _bootstrap_error(pooled[top["threshold"]], score, len(cases))
    best = min(
        (row for row in table if row["utility"] >= top["utility"] - error),
        key=lambda row: row["threshold"],
    )
    return {
        "folds": folds,
        "seeds": seeds,
        "standard_error": round(error, 4),
        "table": table,
        "best": best,
    }


def _bootstrap_error(
    outcomes: Sequence[dict[str, Any]], score: Utility, cases: int, draws: int = 300
) -> float:
    """Standard error of the utility, resampling cases (each case's outcomes together)."""
    from .recall_eval import summarize

    grouped: dict[str, list[dict[str, Any]]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome["id"], []).append(outcome)
    keys = sorted(grouped)
    if len(keys) < 2:
        return 0.0
    rng = random.Random(0)
    values = []
    for _ in range(draws):
        sample = [o for _ in keys for o in grouped[keys[rng.randrange(len(keys))]]]
        values.append(score(summarize(sample)))
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _judged(
    case: dict[str, Any], candidates: Sequence[Candidate], chosen: Sequence[Candidate]
) -> dict[str, Any]:
    first = next((rank for rank, c in enumerate(chosen, 1) if c.relevant), None)
    return {
        "id": case["id"],
        "split": case["split"],
        "tags": case["tags"],
        "answerable": bool(case["expect"]),
        "returned": len(chosen),
        "relevant_returned": sum(1 for c in chosen if c.relevant),
        "first_relevant": first if case["expect"] else None,
    }


def ranking_quality(
    model: GateModel, rows: Sequence[Sequence[float]], labels: Sequence[float]
) -> dict[str, Any]:
    """How well a model orders labeled rows: AUC (ties count half) and mean log loss."""
    scored = [(model.probability(r), float(y)) for r, y in zip(rows, labels)]
    positives = [p for p, y in scored if y >= 0.5]
    negatives = [p for p, y in scored if y < 0.5]
    if not positives or not negatives:
        return {"auc": None, "log_loss": None, "rows": len(scored)}
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    loss = -sum(
        math.log(max(p, 1e-9)) if y >= 0.5 else math.log(max(1 - p, 1e-9)) for p, y in scored
    ) / len(scored)
    return {
        "auc": round(wins / (len(positives) * len(negatives)), 4),
        "log_loss": round(loss, 4),
        "rows": len(scored),
    }


def auc_difference(
    new: GateModel,
    old: GateModel,
    rows: Sequence[Sequence[float]],
    labels: Sequence[float],
    draws: int = 200,
) -> dict[str, Any]:
    """AUC(new) - AUC(old) on the same rows, with a paired bootstrap standard error.

    Resampling keeps each class's size, so every draw can still be ranked.
    """
    pairs = list(zip(rows, labels))
    positives = [p for p in pairs if p[1] >= 0.5]
    negatives = [p for p in pairs if p[1] < 0.5]
    if not positives or not negatives:
        return {"difference": None, "standard_error": None}

    def gap(sample: list[tuple[Sequence[float], float]]) -> float:
        a = ranking_quality(new, [r for r, _ in sample], [y for _, y in sample])["auc"]
        b = ranking_quality(old, [r for r, _ in sample], [y for _, y in sample])["auc"]
        return float(a) - float(b)

    observed = gap(pairs)
    rng = random.Random(0)
    values = [
        gap(
            [positives[rng.randrange(len(positives))] for _ in positives]
            + [negatives[rng.randrange(len(negatives))] for _ in negatives]
        )
        for _ in range(draws)
    ]
    mean = sum(values) / len(values)
    error = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    return {"difference": round(observed, 4), "standard_error": round(error, 4)}
