"""Decision-pattern heuristics used by drift detection."""
from __future__ import annotations

import re


class DecisionPatternExtractor:
    """Extract and compare decision-pattern heuristics from memory bodies."""

    CAPITALIZED_STOPWORDS = frozenset({
        "The", "This", "That", "These", "Those", "Use", "Used", "Using",
        "Why", "What", "When", "How", "Where", "Which", "Who",
        "Decision", "Reason", "Note", "Notes", "Summary", "Overview",
        "Yes", "No", "True", "False", "None", "Null",
        "I", "We", "You", "He", "She", "They", "It",
        "If", "Then", "Else", "And", "Or", "But", "Not",
        "Memory", "Memories", "Item", "Items",
    })
    _CHOICE_PATTERN = re.compile(
        r"\b(?i:"
        r"use|uses|using|chose|choose|selected|select|adopted|adopt|"
        r"decided\s+(?:to\s+use|on)|switched\s+to"
        r")\s+(?i:a\s+|an\s+|the\s+)?"
        r"([A-Z][A-Za-z0-9_.+/-]{1,63})\b",
    )

    def extract_decision_patterns(self, body: str) -> list[str]:
        """Extract decision patterns like 'use X', 'chose Y', etc."""
        patterns = []
        keywords = ['use', 'chose', 'selected', 'switched to', 'adopted', 'decided on']

        for line in body.split('\n'):
            for keyword in keywords:
                if keyword in line.lower():
                    patterns.append(line.strip())
                    break

        return patterns

    def check_contradiction(self, patterns_a: list[str], patterns_b: list[str]) -> str | None:
        """Return an exclusive-choice conflict, not mere entity-set difference."""
        choices_a = self.extract_selected_choices(" ".join(patterns_a))
        choices_b = self.extract_selected_choices(" ".join(patterns_b))

        if choices_a and choices_b and set(choices_a).isdisjoint(choices_b):
            return (
                f"Decision A selects {', '.join(choices_a)}, "
                f"Decision B selects {', '.join(choices_b)}"
            )

        return None

    def extract_selected_choices(self, text: str) -> list[str]:
        """Extract the asserted choice immediately governed by a decision verb."""

        choices = {
            match.group(1)
            for match in self._CHOICE_PATTERN.finditer(text)
            if match.group(1).title() not in self.CAPITALIZED_STOPWORDS
        }
        return sorted(choices, key=str.casefold)

    def extract_tool_names(self, text: str) -> list[str]:
        """Extract candidate tool/framework names from decision text."""
        candidates: set[str] = set()

        for token in re.findall(r'\b[A-Z][a-zA-Z0-9]{3,}\b', text):
            if token not in self.CAPITALIZED_STOPWORDS:
                candidates.add(token)

        for token in re.findall(r'\b[A-Za-z][A-Za-z0-9]*[.\d/][A-Za-z0-9./]*\b', text):
            if len(token) >= 3:
                candidates.add(token)

        for token in re.findall(r'\b[A-Z]{2,6}\d?\b', text):
            candidates.add(token)

        return sorted(candidates)


__all__ = ["DecisionPatternExtractor"]
