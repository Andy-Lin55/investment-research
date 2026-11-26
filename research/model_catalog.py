"""Adaptive Opus-first model resolution for CLI and API model rosters."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

TIER_RANK: dict[str, int] = {'low': 1, 'medium': 2, 'high': 3, 'xhigh': 4, 'max': 5}
_NUMERIC = re.compile(r'^\d+(?:\.\d+)?$')
_TOKEN_SPLIT = re.compile(r'[-_\s/]+')
_DEFAULT_EXCLUDES = ('orchestrator', 'embedding', 'tts', 'whisper', 'dall-e', 'imagen', 'moderation', 'aqa')


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    family: str
    generation: tuple[int, ...] | None
    codename: str
    tier: str | None
    is_opus: bool


def _parse_generation(token: str) -> tuple[int, ...] | None:
    if not _NUMERIC.match(token):
        return None
    parts = [int(part) for part in token.split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def parse_model_id(model_id: str) -> ModelSpec | None:
    """Split `<family>-<generation?>-<codename...>-<tier?>` into comparable parts."""
    raw = model_id.strip()
    if not raw:
        return None
    cleaned = raw[7:] if raw.lower().startswith('models/') else raw
    tokens = [token for token in _TOKEN_SPLIT.split(cleaned.lower()) if token]
    if len(tokens) < 2:
        return None
    family = tokens[0]
    tier = tokens[-1] if tokens[-1] in TIER_RANK else None
    body = tokens[1:-1] if tier else tokens[1:]
    generation_tokens: list[str] = []
    for token in body:
        if _NUMERIC.match(token):
            generation_tokens.append(token)
        elif generation_tokens:
            break
    generation = _parse_generation('.'.join(generation_tokens)) if generation_tokens else None
    codename_tokens = [token for token in body if not _NUMERIC.match(token)]
    codename = '-'.join(codename_tokens)
    is_opus = 'opus' in codename_tokens or 'opus' in tokens
    return ModelSpec(
        model_id=cleaned,
        family=family,
        generation=generation,
        codename=codename,
        tier=tier,
        is_opus=is_opus,
    )


def resolve_adaptive_model(
    model_ids: Iterable[str],
    *,
    prefer_opus: bool = True,
    preferred_tier: str = 'high',
    family_prefixes: Sequence[str] = (),
    exclude_patterns: Sequence[str] = _DEFAULT_EXCLUDES,
) -> str | None:
    """Select the best model id: Opus-level first, then highest generation and tier."""
    specs = [spec for spec in (parse_model_id(item) for item in model_ids if isinstance(item, str) and item.strip()) if spec]
    if not specs:
        return None
    prefixes = tuple(prefix.lower() for prefix in family_prefixes if prefix)
    excluded = tuple(pattern.lower() for pattern in exclude_patterns if pattern)

    def usable(spec: ModelSpec) -> bool:
        if prefixes and not spec.family.startswith(prefixes):
            return False
        lowered = spec.model_id.lower()
        return not any(pattern in lowered for pattern in excluded)

    candidates = [spec for spec in specs if usable(spec)]
    if not candidates:
        return None

    breadth: dict[tuple[str, tuple[int, ...] | None, str], int] = {}
    for spec in specs:
        key = (spec.family, spec.generation, spec.codename)
        breadth[key] = max(breadth.get(key, 0), TIER_RANK.get(spec.tier or '', 0))

    requested = preferred_tier.strip().lower()

    def order(spec: ModelSpec) -> tuple[int, tuple[int, ...], int, int, int, str]:
        return (
            1 if (prefer_opus and spec.is_opus) else 0,
            spec.generation if spec.generation is not None else (-1,),
            1 if spec.tier == requested else 0,
            breadth.get((spec.family, spec.generation, spec.codename), 0),
            TIER_RANK.get(spec.tier or '', 0),
            spec.model_id,
        )

    return sorted(candidates, key=order)[-1].model_id
