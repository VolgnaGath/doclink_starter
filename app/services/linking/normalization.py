"""Versioned, deliberately conservative number/name matching operations."""

import re
import unicodedata
from difflib import SequenceMatcher

OCR = str.maketrans(
    {
        "0": "О",
        "O": "О",
        "K": "К",
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "M": "М",
        "P": "Р",
        "T": "Т",
        "X": "Х",
    }
)


def normalize_number(value: str) -> str:
    # NFKC expands № to 'No'; remove the marker before compatibility conversion.
    value = re.sub(r"^\s*№\s*", "", value)
    value = unicodedata.normalize("NFKC", value).upper()
    value = re.sub(r"[‐‑‒–—−]", "-", value)
    return re.sub(r"\s+", "", value).removeprefix("№").translate(OCR)


def number_score(left: str | None, right: str | None) -> float:
    if not left or not right or not left.strip() or not right.strip():
        return 0
    if left == right:
        return 1
    a, b = normalize_number(left), normalize_number(right)
    if not a or not b:
        return 0
    if a == b:
        return 0.9
    short, long = sorted((a, b), key=len)
    if len(short) >= 3 and long.startswith(short) and long[len(short)] in "/-":
        return 0.8
    if min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.85:
        return 0.7
    return 0


def normalize_name(value: str) -> str:
    tokens = re.findall(
        r"[а-яa-z0-9]+", unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    )
    return " ".join(
        token for token in tokens if token not in {"ооо", "ао", "пао", "оао", "зао", "ип"}
    )


def name_score(left: str | None, right: str | None) -> float:
    a, b = normalize_name(left or ""), normalize_name(right or "")
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0


def valid_inn(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{10}|[0-9]{12}", value) or len(set(value)) == 1:
        return False
    digits = [int(char) for char in value]

    def check(weights: tuple[int, ...], position: int) -> bool:
        return (
            sum(a * b for a, b in zip(digits, weights, strict=False)) % 11 % 10 == digits[position]
        )

    if len(value) == 10:
        return check((2, 4, 10, 3, 5, 9, 4, 6, 8), 9)
    return check((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), 10) and check(
        (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8), 11
    )
