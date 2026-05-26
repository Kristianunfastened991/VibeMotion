from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer_motion import _fallback_recipe
from app.services.motion_effects import (
    SUPPORTED_FRAME_PRESETS,
    SUPPORTED_LAYER_PRESETS,
    all_effects,
    primary_supported_effect,
    registry_stats,
    resolve_effects,
)


SAMPLE_LAYER = {
    "id": "qa-layer",
    "name": "QA layer",
    "kind": "image",
    "node_type": "RECTANGLE",
    "x": 0,
    "y": 0,
    "width": 320,
    "height": 180,
}

SAMPLE_PROMPTS = [
    ("iris reveal the image for 1 second", "image", "wipe-reveal", "iris-reveal"),
    ("make the card shimmer with a light sweep", "shape", "pulse-glow", "shimmer"),
    ("spin in from the left", "image", "custom-dsl", "rotate-in"),
    ("add a typewriter text animation", "text", "fade-in", "type-on"),
    ("button rises from below on position Y", "shape", "soft-slide", "button-y-rise"),
    ("make it float with premium drift", "image", "premium-float", "premium-float"),
    ("use a mask reveal from left to right", "image", "wipe-reveal", "mask-reveal"),
    ("wiggle the badge for emphasis", "shape", "custom-dsl", "wiggle"),
    ("use blur fade in softly", "image", "blur-fade", "blur-fade"),
    ("make a pop spring bounce", "shape", "pop-in", "elastic-pop"),
    ("use kinetic typography for this text", "text", "custom-dsl", "kinetic-type"),
    ("flip card in from the center", "image", "custom-dsl", "flip-card"),
    ("cascade this text from top to bottom", "text", "custom-dsl", "cascade"),
    ("draw underline from left to right", "shape", "wipe-reveal", "underline-draw"),
    ("draw arrow from left to right", "shape", "wipe-reveal", "arrow-draw"),
    ("add handheld camera shake", "image", "custom-dsl", "handheld"),
]


def main() -> int:
    issues: list[str] = []
    alias_collisions: list[str] = []
    effects = all_effects()
    stats = registry_stats()
    supported = SUPPORTED_LAYER_PRESETS | SUPPORTED_FRAME_PRESETS
    if len(effects) < 50:
        issues.append(f"registry too small: {len(effects)} effects")
    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}
    for effect in effects:
        if effect.id in seen_ids:
            issues.append(f"duplicate effect id: {effect.id}")
        seen_ids.add(effect.id)
        if not effect.aliases:
            issues.append(f"{effect.id}: no aliases")
        if not effect.qa_checks:
            issues.append(f"{effect.id}: no qa checks")
        if not effect.fallback_chain:
            issues.append(f"{effect.id}: no fallback chain")
        if not any(item in supported for item in (effect.preset, *effect.fallback_chain)):
            issues.append(f"{effect.id}: no supported preset/fallback")
        for alias in effect.aliases:
            key = alias.casefold().strip()
            if key in seen_aliases and seen_aliases[key] != effect.id:
                alias_collisions.append(f"alias collision `{alias}`: {seen_aliases[key]} vs {effect.id}")
            seen_aliases[key] = effect.id

    samples = []
    for prompt, target, expected_preset, expected_id in SAMPLE_PROMPTS:
        effect = primary_supported_effect(prompt, scope="selected-layer", target=target)
        recipe = _fallback_recipe(prompt, {**SAMPLE_LAYER, "kind": target})
        resolved_ids = [item.id for item in resolve_effects(prompt, scope="selected-layer", target=target, limit=3)]
        samples.append(
            {
                "prompt": prompt,
                "target": target,
                "resolved": effect.id if effect else None,
                "resolved_ids": resolved_ids,
                "recipe_preset": recipe.get("preset"),
                "tags": recipe.get("tags"),
            }
        )
        if not effect:
            issues.append(f"{prompt}: did not resolve")
            continue
        if effect.id != expected_id:
            issues.append(f"{prompt}: effect {effect.id} != {expected_id}")
        if recipe.get("preset") != expected_preset:
            issues.append(f"{prompt}: recipe preset {recipe.get('preset')} != {expected_preset}")
        if f"effect:{expected_id}" not in list(recipe.get("tags") or []):
            issues.append(f"{prompt}: recipe tags missing effect:{expected_id}")

    summary = {"status": "pass" if not issues else "fail", "stats": stats, "samples": samples, "alias_collisions": alias_collisions, "issues": issues}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
