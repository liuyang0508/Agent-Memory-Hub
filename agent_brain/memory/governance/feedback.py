from __future__ import annotations

CAP = 0.95


def _clamp(v):
    return min(1.0, max(0.0, v))


class ConfidenceFeedback:
    def __init__(self, index=None, items_store=None):
        self.index, self.items_store = index, items_store

    def _current(self, i):
        if self.index is not None:
            r = self.index.get_confidence_data([i]).get(i)
            if r is not None:
                return r[0]
        if self.items_store is not None:
            try:
                return self.items_store.get(i)[0].confidence
            except FileNotFoundError:
                return None

    def _apply(self, i, c):
        c = _clamp(c)
        if self.items_store is not None:
            try:
                self.items_store.update_frontmatter(i, confidence=c)
            except FileNotFoundError:
                pass
        if self.index is not None:
            self.index.update_confidence(i, c)
        return c

    def on_access(self, i, reward=0.01):
        c = self._current(i)
        if c is None:
            return None
        return c if c >= CAP else self._apply(i, min(CAP, c + reward))

    def on_contradiction(self, i, penalty=0.15):
        c = self._current(i)
        return None if c is None else self._apply(i, c - penalty)

    def on_supersede(self, i, penalty=0.15):
        return self.on_contradiction(i, penalty)

    def on_reaffirm(self, i, support_delta=1, gain_delta=0.1):
        """Item was retrieved and actually used → boost support + gain."""
        if self.items_store is None:
            return
        try:
            item, _ = self.items_store.get(i)
            new_support = item.support_count + support_delta
            new_gain = item.gain_score + gain_delta
            updated = self.items_store.update_frontmatter(
                i, support_count=new_support, gain_score=round(new_gain, 3)
            )
            if self.index is not None:
                self.index.update_feedback_stats(
                    i,
                    support_count=updated.support_count,
                    contradict_count=updated.contradict_count,
                    gain_score=updated.gain_score,
                )
            self.on_access(i, reward=0.02)
        except FileNotFoundError:
            pass

    def on_reject(self, i, contradict_delta=1, gain_penalty=0.2):
        """Agent explicitly negated/overrode this item → bump contradict, penalize gain."""
        if self.items_store is None:
            return
        try:
            item, _ = self.items_store.get(i)
            new_contradict = item.contradict_count + contradict_delta
            new_gain = item.gain_score - gain_penalty
            updated = self.items_store.update_frontmatter(
                i, contradict_count=new_contradict, gain_score=round(new_gain, 3)
            )
            if self.index is not None:
                self.index.update_feedback_stats(
                    i,
                    support_count=updated.support_count,
                    contradict_count=updated.contradict_count,
                    gain_score=updated.gain_score,
                )
            self.on_contradiction(i)
        except FileNotFoundError:
            pass


def apply_contradiction_feedback(report, index=None, items_store=None, cp=0.15, sp=0.15):
    fb = ConfidenceFeedback(index, items_store)
    for f in report.findings:
        if getattr(f.drift_type, "value", f.drift_type) != "contradiction":
            continue
        for x in f.item_ids:
            fb.on_contradiction(x, cp)
        if len(f.item_ids) == 2 and items_store is not None:
            d = {}
            for x in f.item_ids:
                try:
                    d[x] = items_store.get(x)[0].created_at
                except Exception:
                    pass
            if len(d) == 2:
                fb.on_supersede(min(d, key=lambda k: d[k]), sp)
