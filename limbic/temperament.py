"""Temperament: the fixed, per-agent 'biology'.

Two agents fed the same events end up in different states because their
temperaments differ: how reactive they are, how fast they recover, what
they like, how much they need company, how much they ruminate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields


@dataclass
class Temperament:
    name: str = "agent"
    description: str = ""
    # Persona hooks used by appraisers (what reliably moves this agent).
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    humor: list[str] = field(default_factory=list)

    # Reactivity
    reactivity: float = 1.0            # global gain on every event
    fiction_gain: float = 0.7          # stories move the agent, a bit less than her own life
    positive_sensitivity: float = 1.0
    negative_sensitivity: float = 1.0
    emotion_gain: float = 1.5          # how hard active emotions push mood

    # Time constants
    recovery: float = 1.0              # >1: emotions fade faster
    peak_fade: float = 2.0             # extra decay rate at full intensity
    mood_tau_h: float = 1.5            # mood follows emotions with this lag
    baseline_tau_d: float = 5.0        # background mood follows mood (slow)
    resilience_tau_d: float = 10.0     # background mood returns to set point
    # NB: a persistent emotional offset X settles the baseline at about
    # X * resilience_tau_d / baseline_tau_d. Keep the ratio near 1-2.

    # Background mood
    set_point: dict[str, float] = field(
        default_factory=lambda: {"p": 0.0, "a": 0.0, "d": 0.0})
    baseline_min: float = -0.8
    baseline_max: float = 0.8
    crisis_floor: float = -0.6         # baseline below this raises an alert

    # Cognitive biases
    mood_congruence: float = 0.5       # bad mood amplifies bad news
    habituation_strength: float = 0.35  # repeated stimulus loses effect
    habituation_half_life_h: float = 6.0
    negative_habituation: float = 0.3  # negativity bias: bad things wear off less

    # Small talk only brings mood back towards baseline, never past it.
    mundane_threshold: float = 0.12
    mundane_recovery: float = 0.15

    # Drives (deficit growth per hour, 0..1 scale)
    social_need: float = 0.02
    novelty_need: float = 0.015
    competence_need: float = 0.008
    need_threshold: float = 0.6
    fatigue_per_effort: float = 0.05
    fatigue_recovery_h: float = 3.0
    # exhaustion has sources: work tires fully, pleasant talk and reading less
    social_fatigue: float = 0.6        # share of a pleasant conversation turn's cost
    passive_fatigue: float = 0.1       # share for things that just happen to her (reading, news)
    flow_relief: float = 0.5           # engaging things tire less, dull ones more
    tired_negativity: float = 0.5      # tired: bad things hit harder (and frustrate)
    # sleepiness: a circadian rhythm, separate from exhaustion
    circadian_strength: float = 1.0    # 0 disables sleepiness
    sleep_hours: list[int] = field(default_factory=lambda: [23, 7])   # [bedtime, wake-up], local time
    need_feeling_max: float = 0.5      # cap of loneliness/boredom from needs
    need_adaptation_d: float = 4.0     # chronic unmet needs dull over time
    dormant_after_h: float = 72.0      # no input this long = agent is offline,
                                       # needs stop growing (0 disables)
    jolt: float = 0.6                  # immediate mood kick of a new feeling

    # Consolidation ("sleep"): re-living the day's strong memories
    rumination: float = 0.3
    savoring: float = 0.3
    day_balance_gain: float = 0.025    # max background shift per consolidated day
    day_balance_scale: float = 0.15    # net (eff*valence) that counts as a clearly good/bad day

    # Display rules: how openly feelings show in speech (0 stoic .. 1 open)
    expressiveness: float = 0.6

    # ---- needs layer -------------------------------------------------
    recognition_need: float = 0.01    # wish to be appreciated, per hour
    # attachment: missing a specific person grows with absence * affection
    longing_delay_h: float = 18.0     # a night apart is not longing yet
    longing_rate: float = 0.06        # per hour at affection 1.0
    longing_feel_threshold: float = 0.35
    sulkiness: float = 0.4            # hurt when outreach went unanswered
    reunion_joy: float = 0.6          # gladness when a missed person returns

    # excitement: engagement in a conversation thread
    interest_gain: float = 0.6
    thread_gap_min: float = 45.0      # silence longer than this = new thread
    engagement_half_life_h: float = 2.0

    # desire (off by default; also needs per-person consent, see relationships)
    romance_enabled: bool = False
    adult_enabled: bool = False
    intimacy_need: float = 0.015      # need for closeness/tenderness, per hour
    libido_rate: float = 0.02         # per hour
    desire_gain: float = 0.8
    rebuff_cooldown_h: float = 12.0   # after a "no", no steering for this long
    heat_threshold: float = 0.45      # above this the agent is "heated": bolder, more open with consenting partners
    taboo_thrill: float = 0.8         # how much the forbidden adds to desire (consensual only)
    afterglow_shame: float = 0.6      # embarrassment/regret once cooled down, after going far while heated
    romance_private_only: bool = True # romance steering only in private threads too
    libido_feel_threshold: float = 0.45  # above this, libido turns into a quiet desire towards a consenting partner
    libido_feel_max: float = 0.55        # cap of that self-generated desire
    libido_unmet_after_h: float = 24.0   # wanting it this long without it brings a little restlessness
    # learning to like intimacy (needs adult_enabled + consent, like everything here)
    openness_learn_rate: float = 0.05    # how much one good intimate moment teaches her to like it
    openness_unlearn: float = 2.5        # pressure past her "no" teaches the opposite, harder
    openness_half_life_d: float = 45.0   # unused openness fades slowly
    openness_libido_gain: float = 1.5    # libido refills up to (1 + gain*openness) times faster
    openness_point: float = 0.5          # above this she wants closeness even with low libido
    openness_attraction_gain: float = 2.0  # good intimate moments build attraction this much faster

    # ---- autonomy ----------------------------------------------------
    autonomy_enabled: bool = True     # produce intents (the host decides to act)
    quiet_hours: list[int] = field(default_factory=lambda: [23, 8])
    outreach_threshold: float = 0.6   # longing needed to reach out
    outreach_cooldown_h: float = 12.0
    outreach_backoff: float = 2.0     # cooldown multiplier per unanswered try
    max_unanswered: int = 3           # then it stops trying until contacted
    max_outreach_per_day: int = 3
    check_in_after_min: float = 20.0  # engaging chat went quiet this long
    check_in_window_h: float = 4.0    # ...but not later than this
    check_in_min_engagement: float = 0.5
    steer_threshold: float = 0.45
    intent_ttl_h: float = 2.0         # pending intent expires if nobody acts
    answer_window_h: float = 24.0     # delivered outreach counts as ignored after
    webhook_url: str = ""             # server POSTs new intents here

    # ---- leisure (free time; see leisure.py) ---------------------------
    leisure_enabled: bool = True
    leisure_activities: list[str] = field(default_factory=lambda: ["web", "lector", "journal"])
    leisure_prior: dict[str, float] = field(default_factory=lambda: {"web": 0.15, "lector": 0.2, "journal": 0.1, "code": 0.05})
    leisure_urge_min: float = 0.12    # below this she isn't bored enough to bother
    leisure_eagerness: float = 0.12   # start chance per evaluation at full urge and neutral mood
    leisure_nothing_weight: float = 0.35
    leisure_randomness: float = 0.6   # <1: mostly the favourite; 1: proportional
    leisure_idle_min: float = 20.0    # minutes since the last conversation turn
    leisure_gap_min: float = 30.0     # minutes between two sessions
    leisure_max_per_day: int = 8
    leisure_max_minutes: float = 60.0  # overall cap; each activity also has its own
    leisure_restores: float = 0.015   # per enjoyable step: eases fatigue from work/people
    leisure_satiety: float = 0.6      # doing the same thing a lot makes it less tempting
    leisure_satiety_h: float = 12.0
    leisure_taste_rate: float = 0.3   # how fast one session changes the taste
    leisure_resume_bonus: float = 0.35
    leisure_idle_tau_h: float = 3.0   # empty hours make her restless on this time scale
    journal_relief: float = 0.15      # writing the day down eases negative feelings this much

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Temperament":
        data = data or {}
        known = {f.name for f in fields(cls)}
        t = cls(**{k: v for k, v in data.items() if k in known})
        sp = {"p": 0.0, "a": 0.0, "d": 0.0}
        sp.update(t.set_point or {})
        t.set_point = sp
        return t
