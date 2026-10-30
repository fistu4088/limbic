"""Emotion catalogue.

Each emotion has a position in PAD space (Pleasure, Arousal, Dominance),
mostly taken from the ALMA model (Gebhard 2005) which maps OCC emotions onto
Mehrabian's PAD temperament space. Active emotions pull the mood towards
their PAD vector, weighted by intensity.

half_life_min: base half-life of the emotion at low intensity. Strong peaks
fade faster (see Temperament.peak_fade), the tail lingers.
targeted: whether the emotion is usually *about* someone/something
(anger at X, gratitude towards Y, grief for Z).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmotionSpec:
    name: str
    pad: tuple[float, float, float]
    half_life_min: float
    targeted: bool
    hu: str
    valence: int  # +1 positive, -1 negative


_SPECS = [
    EmotionSpec("joy", (0.40, 0.20, 0.10), 45, False, "öröm", 1),
    EmotionSpec("amusement", (0.45, 0.35, 0.15), 20, False, "derültség", 1),
    EmotionSpec("pride", (0.40, 0.30, 0.30), 90, False, "büszkeség", 1),
    EmotionSpec("gratitude", (0.40, 0.20, -0.30), 120, True, "hála", 1),
    EmotionSpec("hope", (0.20, 0.20, -0.10), 180, False, "remény", 1),
    EmotionSpec("sadness", (-0.40, -0.20, -0.50), 240, True, "szomorúság", -1),
    EmotionSpec("anger", (-0.51, 0.59, 0.25), 60, True, "harag", -1),
    EmotionSpec("frustration", (-0.40, 0.40, -0.20), 45, False, "frusztráció", -1),
    EmotionSpec("fear", (-0.64, 0.60, -0.43), 40, True, "félelem", -1),
    EmotionSpec("shame", (-0.30, 0.10, -0.60), 120, False, "szégyen", -1),
    EmotionSpec("boredom", (-0.20, -0.50, -0.10), 30, False, "unalom", -1),
    EmotionSpec("loneliness", (-0.40, -0.30, -0.40), 60, False, "magány", -1),
    # needs / excitement / desire layer
    EmotionSpec("excitement", (0.30, 0.65, 0.25), 25, False, "lelkesedés", 1),
    EmotionSpec("longing", (-0.25, -0.10, -0.35), 120, True, "hiányérzet", -1),
    EmotionSpec("tenderness", (0.50, 0.00, 0.10), 90, True, "gyengédség", 1),
    EmotionSpec("desire", (0.35, 0.70, 0.20), 30, True, "vágy", 1),
    # pulled towards something you won't do: aroused, unsettled, less in control
    EmotionSpec("temptation", (0.05, 0.60, -0.25), 30, False, "kísértés", -1),
]

EMOTIONS: dict[str, EmotionSpec] = {s.name: s for s in _SPECS}
