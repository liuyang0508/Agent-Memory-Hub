"""Recover bounded language-neutral technical anchors from user queries."""

from __future__ import annotations

from functools import lru_cache
import unicodedata


_DEVANAGARI_START = ord("ऀ")
_DEVANAGARI_END = ord("ॿ")
_MIN_ACRONYM_LENGTH = 2
_MAX_ACRONYM_LENGTH = 8
_MAX_ANCHORS = 6
TECHNICAL_ALIAS_SET_ID = "devanagari-exact-v1"

# Frozen, exact technical loanword aliases. This deliberately stays small and
# conservative: aliases are accepted only for a whole isolated Devanagari run,
# never by fuzzy matching or generic transliteration.
_DEVANAGARI_TECHNICAL_LOANWORDS = {
    "कैश": "cache",
    "क्लाइंट": "client",
    "कुकी": "cookie",
    "डेटाबेस": "database",
    "गेटवे": "gateway",
    "टाइमआउट": "timeout",
    "टोकन": "token",
    "प्रॉक्सी": "proxy",
    "ब्राउज़र": "browser",
    "रनटाइम": "runtime",
    "सर्वर": "server",
    "सेशन": "session",
}

# Hindi commonly writes Latin initialisms by spelling each Latin letter name.
# A fully consumed run can therefore recover the same exact technical anchor
# that appears in source code or memory metadata without translating prose.
_DEVANAGARI_LATIN_LETTER_NAMES = {
    "ए": "a",
    "बी": "b",
    "सी": "c",
    "डी": "d",
    "ई": "e",
    "एफ": "f",
    "जी": "g",
    "एच": "h",
    "आई": "i",
    "जे": "j",
    "के": "k",
    "एल": "l",
    "एम": "m",
    "एन": "n",
    "ओ": "o",
    "पी": "p",
    "क्यू": "q",
    "आर": "r",
    "एस": "s",
    "टी": "t",
    "यू": "u",
    "वी": "v",
    "डब्ल्यू": "w",
    "डबल्यू": "w",
    "एक्स": "x",
    "वाई": "y",
    "ज़ेड": "z",
    "जेड": "z",
}
_LETTER_NAME_ROWS = tuple(
    sorted(
        _DEVANAGARI_LATIN_LETTER_NAMES.items(),
        key=lambda row: (-len(row[0]), row[0]),
    )
)


def technical_acronym_anchors(text: str) -> tuple[str, ...]:
    """Return deduplicated Latin acronyms encoded as Devanagari letter names.

    Only isolated Devanagari runs that can be consumed completely as two to
    eight letter names are accepted. Partial transliteration and ordinary words
    fail closed, keeping the result suitable for exact lexical retrieval.
    """

    normalized = unicodedata.normalize("NFC", text)
    anchors: list[str] = []
    for start, end in _devanagari_runs(normalized):
        if _touches_word_character(normalized, start, end):
            continue
        decoded = _decode_devanagari_letter_names(normalized[start:end])
        if decoded is None or decoded in anchors:
            continue
        anchors.append(decoded)
        if len(anchors) >= _MAX_ANCHORS:
            break
    return tuple(anchors)


def technical_query_anchors(text: str) -> tuple[str, ...]:
    """Return frozen exact loanword aliases and spelled-acronym anchors."""

    normalized = unicodedata.normalize("NFC", text)
    anchors: list[str] = []
    for start, end in _devanagari_runs(normalized):
        if _touches_word_character(normalized, start, end):
            continue
        run = normalized[start:end]
        decoded = _DEVANAGARI_TECHNICAL_LOANWORDS.get(run)
        if decoded is None:
            decoded = _decode_devanagari_letter_names(run)
        if decoded is None or decoded in anchors:
            continue
        anchors.append(decoded)
        if len(anchors) >= _MAX_ANCHORS:
            break
    return tuple(anchors)


def _devanagari_runs(text: str) -> tuple[tuple[int, int], ...]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, character in enumerate(text):
        if _is_devanagari(character):
            if start is None:
                start = index
            continue
        if start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    return tuple(runs)


def _decode_devanagari_letter_names(run: str) -> str | None:
    @lru_cache(maxsize=None)
    def decode(offset: int) -> tuple[str, ...]:
        if offset == len(run):
            return ("",)
        results: set[str] = set()
        for spelling, letter in _LETTER_NAME_ROWS:
            if not run.startswith(spelling, offset):
                continue
            for suffix in decode(offset + len(spelling)):
                candidate = letter + suffix
                if len(candidate) <= _MAX_ACRONYM_LENGTH:
                    results.add(candidate)
        return tuple(sorted(results))

    candidates = tuple(
        candidate
        for candidate in decode(0)
        if _MIN_ACRONYM_LENGTH <= len(candidate) <= _MAX_ACRONYM_LENGTH
    )
    if len(candidates) != 1:
        return None
    return candidates[0]


def _touches_word_character(text: str, start: int, end: int) -> bool:
    return (
        start > 0
        and _is_word_character(text[start - 1])
        or end < len(text)
        and _is_word_character(text[end])
    )


def _is_word_character(character: str) -> bool:
    return character == "_" or unicodedata.category(character).startswith(("L", "M", "N"))


def _is_devanagari(character: str) -> bool:
    return _DEVANAGARI_START <= ord(character) <= _DEVANAGARI_END


__all__ = [
    "TECHNICAL_ALIAS_SET_ID",
    "technical_acronym_anchors",
    "technical_query_anchors",
]
