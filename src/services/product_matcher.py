"""Product name matching engine for OCR results → registered product mapping.

Supports exact match (normalized), fuzzy token match, and AI-assisted matching.
"""

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class MatchResult:
    ocr_name: str
    matched_name: str | None = None
    confidence: float = 0.0
    status: str = "none"          # exact / fuzzy / none
    candidates: list[dict] = field(default_factory=list)  # [{name, score}]


def normalize(name: str) -> str:
    """Normalize product name for comparison: uppercase, strip special chars."""
    s = unicodedata.normalize("NFC", name.strip().upper())
    s = re.sub(r"[_\-\s/\\()（）\[\]【】·・.·,，]+", "", s)
    s = re.sub(r"[^\w가-힣%]", "", s)
    return s


def tokenize(name: str) -> set[str]:
    """Extract meaningful tokens from a product name."""
    s = name.strip().upper()
    tokens = re.findall(r"[A-Z]+|[가-힣]+|\d+(?:\.\d+)?", s)
    return {t for t in tokens if len(t) >= 2 or t.isdigit()}


def _token_overlap_score(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def match_single(ocr_name: str, registered_names: list[str], threshold: float = 0.25) -> MatchResult:
    """Match a single OCR product name against registered product list."""
    result = MatchResult(ocr_name=ocr_name)
    norm_ocr = normalize(ocr_name)

    if not norm_ocr:
        return result

    # Pass 1: Exact normalized match
    for reg in registered_names:
        if normalize(reg) == norm_ocr:
            result.matched_name = reg
            result.confidence = 1.0
            result.status = "exact"
            return result

    # Pass 2: Token-based fuzzy match
    ocr_tokens = tokenize(ocr_name)
    scored: list[tuple[str, float]] = []

    for reg in registered_names:
        reg_tokens = tokenize(reg)
        score = _token_overlap_score(ocr_tokens, reg_tokens)
        if score >= threshold:
            scored.append((reg, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    if scored:
        best_name, best_score = scored[0]
        result.matched_name = best_name
        result.confidence = round(best_score, 3)
        result.status = "fuzzy"
        result.candidates = [{"name": n, "score": round(s, 3)} for n, s in scored[:5]]

    return result


def match_all(ocr_names: list[str], registered_names: list[str]) -> list[MatchResult]:
    """Match a list of OCR product names against registered products."""
    # Skip TEST entries
    results = []
    for name in ocr_names:
        if normalize(name) in ("TEST", ""):
            continue
        results.append(match_single(name, registered_names))
    return results


def match_summary(results: list[MatchResult]) -> dict:
    """Summarize match results."""
    exact = sum(1 for r in results if r.status == "exact")
    fuzzy = sum(1 for r in results if r.status == "fuzzy")
    none_ = sum(1 for r in results if r.status == "none")
    return {
        "total": len(results),
        "exact": exact,
        "fuzzy": fuzzy,
        "unmatched": none_,
        "auto_rate": round(exact / len(results) * 100, 1) if results else 0,
    }
