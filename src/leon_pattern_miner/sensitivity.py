from __future__ import annotations

import re
from dataclasses import dataclass

SECRET_PATTERNS = [
    re.compile(r"gh[opsu]_[A-Za-z0-9_]{6,}"),
    re.compile(r"sk-ant[-A-Za-z0-9_\.]{6,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]{6,}"),
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOME_PATH_RE = re.compile(r"/home/[A-Za-z0-9._-]+/[^\s)\]]+")


@dataclass(frozen=True)
class SensitivityHit:
    kind: str
    start: int
    end: int


def mask_sensitive(text: str) -> tuple[str, list[SensitivityHit]]:
    hits: list[SensitivityHit] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            hits.append(SensitivityHit("secret", match.start(), match.end()))
    for match in EMAIL_RE.finditer(text):
        hits.append(SensitivityHit("email", match.start(), match.end()))
    for match in HOME_PATH_RE.finditer(text):
        hits.append(SensitivityHit("home_path", match.start(), match.end()))

    if not hits:
        return text, []

    # Merge overlaps and replace from the end so offsets remain valid.
    hits = sorted(hits, key=lambda h: (h.start, h.end))
    merged: list[SensitivityHit] = []
    for hit in hits:
        if merged and hit.start <= merged[-1].end:
            prev = merged[-1]
            merged[-1] = SensitivityHit(prev.kind, prev.start, max(prev.end, hit.end))
        else:
            merged.append(hit)

    masked = text
    for i, hit in enumerate(reversed(merged), start=1):
        masked = masked[: hit.start] + f"[REDACTED_SECRET_{i}]" + masked[hit.end :]
    return masked, merged


def sensitivity_for_text(text: str) -> str:
    _, hits = mask_sensitive(text)
    if any(hit.kind == "secret" for hit in hits):
        return "secret"
    if hits:
        return "personal"
    return "internal"
