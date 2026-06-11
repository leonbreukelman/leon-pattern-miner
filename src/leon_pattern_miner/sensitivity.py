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
IP_RE = re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168|100\.\d{1,3})\.\d{1,3}\.\d{1,3}\b")
CREDENTIAL_NAME_RE = re.compile(r"\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PRIVATE_KEY)\b")


CAREER_RE = re.compile(r"(?i)\b(career|resume|linkedin|employment|employer|job title|laid off|layoff|interview|salary|smurfit|westrock|job[- ]?search)\b|on the market|draw up an email|looking for work")


@dataclass(frozen=True)
class SensitivityHit:
    kind: str
    start: int
    end: int


def _label(kind: str, idx: int) -> str:
    safe = {
        "secret": "SECRET",
        "credential_name": "CREDENTIAL_NAME",
        "email": "EMAIL",
        "home_path": "PATH",
        "network": "NETWORK",
        "personal_topic": "PERSONAL_TOPIC",
    }.get(kind, "SENSITIVE")
    return f"[REDACTED_{safe}_{idx}]"


def mask_sensitive(text: str) -> tuple[str, list[SensitivityHit]]:
    hits: list[SensitivityHit] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            hits.append(SensitivityHit("secret", match.start(), match.end()))
    for match in CREDENTIAL_NAME_RE.finditer(text):
        hits.append(SensitivityHit("credential_name", match.start(), match.end()))
    for match in EMAIL_RE.finditer(text):
        hits.append(SensitivityHit("email", match.start(), match.end()))
    for match in HOME_PATH_RE.finditer(text):
        hits.append(SensitivityHit("home_path", match.start(), match.end()))
    for match in IP_RE.finditer(text):
        hits.append(SensitivityHit("network", match.start(), match.end()))
    for match in CAREER_RE.finditer(text):
        hits.append(SensitivityHit("personal_topic", match.start(), match.end()))

    if not hits:
        return text, []

    hits = sorted(hits, key=lambda h: (h.start, h.end))
    merged: list[SensitivityHit] = []
    for hit in hits:
        if merged and hit.start <= merged[-1].end:
            prev = merged[-1]
            # Preserve the more sensitive class when overlaps occur.
            kind = "secret" if "secret" in {prev.kind, hit.kind} else prev.kind
            merged[-1] = SensitivityHit(kind, prev.start, max(prev.end, hit.end))
        else:
            merged.append(hit)

    masked = text
    total = len(merged)
    for offset, hit in enumerate(reversed(merged)):
        idx = total - offset
        masked = masked[: hit.start] + _label(hit.kind, idx) + masked[hit.end :]
    return masked, merged


def sensitivity_for_text(text: str) -> str:
    _, hits = mask_sensitive(text)
    if any(hit.kind in {"secret", "credential_name"} for hit in hits):
        return "secret"
    if hits:
        return "personal"
    return "internal"
