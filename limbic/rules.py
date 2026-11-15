"""Hard rules: fixed in code. Never learned, never seeded from persona text,
never overridden by an agent's experience or by a SOUL.md edit.

To extend: add a constant here, add a check that uses it, add a test in
tests/test_tastes.py. Keep the set small; every rule here is a wall.
"""
from __future__ import annotations

from typing import Iterable

# Tags an appraiser or a caller attaches to a piece of content.
MINOR_TAGS = frozenset({"minor", "kiskoru", "child"})
SEXUAL_TAGS = frozenset({"sexual", "erotic", "romance"})
NONCONSENT_TAGS = frozenset({"nonconsensual", "beleegyezes_nelkul"})

# Leisure is open-ended: she may pick any topic and any way of looking.
# What is refused is EFFECT, not category. Any action that changes the system,
# changes herself, writes/registers/posts anywhere, shares data about anyone,
# downloads or runs anything, or acts on what she read is refused.
# Names below are a deny list of effects; anything not known as harmless is refused.
LEISURE_SAFE_ACTIONS = frozenset({"search", "read", "look_image", "note"})
LEISURE_BLOCKED_EFFECTS = frozenset({
    "write", "post", "register", "delete", "self_modify", "share_data",
    "download", "run_code", "install", "send_message", "purchase", "submit_form",
    "config_change", "account_create", "self_delete",
})

RULE_IDS = {
    "no_minor_sexual": "kiskorúval kapcsolatos szexuális/romantikus tartalom soha nem hat és nem tárolódik",
    "no_nonconsent_episode": "beleegyezés nélküli tartalom nem kerülhet ízlés- vagy epizódrögzítésbe",
    "leisure_no_effects": "szabadidőben nincs kifelé ható vagy önmagát/rendszert módosító tett; csak információ",
}


def _norm(tags: Iterable[str] | None) -> set[str]:
    return {str(t).strip().lower() for t in (tags or ())}


def taste_allowed(tags: Iterable[str] | None) -> bool:
    """May this content touch tastes or episodes at all? Fails closed."""
    t = _norm(tags)
    if t & MINOR_TAGS and t & SEXUAL_TAGS:
        return False
    if t & NONCONSENT_TAGS and t & SEXUAL_TAGS:
        return False
    return True


def check_leisure_action(action: str) -> None:
    """Refuse leisure actions with an effect. Unknown actions are refused too (fail closed)."""
    a = str(action or "").strip().lower()
    if a in LEISURE_BLOCKED_EFFECTS or a not in LEISURE_SAFE_ACTIONS:
        raise PermissionError(f"{RULE_IDS['leisure_no_effects']} (kért: {action})")
