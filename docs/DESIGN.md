# Design

All dynamics are explicit, deterministic functions of `(state, temperament,
appraisal, Δt)`. Time is passed in; nothing reads the wall clock inside the
engine. Integration uses sub-steps of ≤ 5 minutes (capped at 800 steps per
advance).

## 1. Appraisal (OCC-light)

An appraiser (LLM or rules) emits zero or more `Appraisal`s per event:

| field | range | meaning |
|---|---|---|
| `valence` | −1..1 | bad … good *for the agent* |
| `intensity` | 0..1 | 0.05 small talk, 0.3 notable, 0.6 strong, 0.9 life-changing |
| `relevance` | 0..1 | how much it touches goals/values/relationships |
| `agency` | self / other / circumstance | who caused it |
| `target` | str | whom the feeling is about |
| `prospective` | bool | future → hope / fear |
| `loss`, `blocked` | bool | grief, frustration |
| `humor`, `novelty`, `affinity`, `effort`, `competence` | | persona-specific modifiers |

Mapping (`map_emotions`):

- good: prospective → **hope**; else **joy** + (self → **pride**, other → **gratitude**)
- bad: prospective → **fear**; other → **anger** if dominance > −0.25, else **fear + sadness**; self → **shame + sadness**; circumstance → **sadness**
- `blocked` → **frustration**, `loss` → **sadness** (no grudge), `competence<0` → frustration, `>0` → pride
- `humor` → **amusement** (also on otherwise mundane events)

## 2. Effective intensity

```
hab   = 1 / (1 + h_strength · (neg ? neg_habituation : 1) · count[event|target])
sens  = neg: neg_sensitivity · (1 + congruence · max(0, −mood.p))
        pos: pos_sensitivity · (1 − ½·congruence · max(0, −mood.p)) · (1 + ½·max(0, affinity))
sens *= 1 + ½·affection(target);  pos: *= 1 − ½·resentment(target)
eff   = clamp01(intensity · hab · sens · reactivity · (½ + ½·relevance))
```

`count` decays with `habituation_half_life_h`. Consolidation halves it.

If `eff < mundane_threshold` (and not funny) the event is **mundane**: it only
moves mood a fraction `mundane_recovery` towards the baseline. Small talk
cannot lift mood above the background.

## 3. Emotions

Partial summation, keyed by `name@target`:
`hi = max(i, a), lo = min(i, a), i ← hi + k·lo·(1 − hi)` with `k = 0.35`
(`0.75` for desire, which escalates). A feeling rises to the strongest
stimulus; many small pokes do not add up to one huge event.
Decay: `di/dt = −λ(i)·i`, `λ(i) = ln2/(half_life/recovery) · (1 + peak_fade·i)`.
Strong peaks fall fast, weak tails linger. Immediate mood kick on onset:
`mood += jolt · (target − mood)` — towards the target, never beyond it.

## 4. Mood (PAD)

```
push   = emotion_gain · Σ i_e · PAD_e  −  (½·exhaustion + 0.4·sleepiness) on arousal
target = baseline + push / (1 + ½·|push|)          # soft saturation
mood  += (target − mood) · (1 − e^(−Δt/mood_tau))
```

PAD vectors per emotion come from the ALMA model (Gebhard 2005).
The label (anguished … elated) is read from pleasure with ±0.03 hysteresis.

## 5. Background (baseline)

```
baseline += (mood − baseline)·k(baseline_tau) + (set_point − baseline)·k(resilience_tau)
```

A persistent emotional offset X settles the baseline at ≈ X · τ_res/τ_base.
Defaults (5 d / 10 d) give ≈ 2X: lived experience matters, without runaway
amplification. The baseline is clamped to `[baseline_min, baseline_max]`;
below `crisis_floor` an alert is raised.

Consolidation adds a small direct contribution from re-lived strong memories.

## 6. Drives

Deficits grow `d += (1−d)(1−e^(−rate·Δt))`: social, novelty, competence.
Fatigue has two parts (see §6a). Above
`need_threshold` a deficit sustains **loneliness** / **boredom** at
`need_feeling_max · (d−thr)/(1−thr) · adapt`, where
`adapt = 0.3 + 0.7·2^(−days_unmet/need_adaptation_d)` (chronic needs dull).
After `dormant_after_h` with no interaction the agent is considered offline:
drives stop growing and need-feelings decay normally.

## 6a. Fatigue and sleep

Two separate things, as in people:

- **Exhaustion** (`drives.fatigue`): short-term load. Each event costs
  `fatigue_per_effort · (0.2 + effort) · share · (1 − 0.6·flow_relief·flow)`, where
  `share` is 1 for work (effort or competence at stake), `social_fatigue` for a
  pleasant conversation turn, `passive_fatigue` for things that just happen to her
  (reading, news), and `flow = affinity + ½·valence + ½·humor` (engaging things tire
  less, dull or unpleasant ones more). The cost is booked per source
  (`fatigue_src`: work / social / leisure / other) so the prompt can say *why* she is
  tired ("a lot of talking" vs "a lot of work"). Half-life `fatigue_recovery_h`;
  consolidation (sleep) removes 70%.
- **Sleepiness** (`drives.sleepiness`): circadian, from local time and
  `sleep_hours` [bedtime, wake-up]. Rises in the two hours before bed (→0.5), peaks
  ~40% into the night (0.85), fades by wake-up and is gone an hour later.
  `circadian_strength: 0` disables it.

Effects: both lower arousal and the verbosity/patience/initiative modulators
(`max(exhaustion, 0.8·sleepiness)`). Tiredness above 0.3 makes bad things hit harder
(`tired_negativity`), and above 0.45 a negative event also brings a little
frustration. Free time: steps cost less when she enjoys them and more when sleepy;
sleepiness damps starting anything by `(1−s)²`, favours the journal, makes "doing
nothing" weigh more, and stops activities above 0.6. Free time is cheap (an hour of reading ≈ +0.25)
and, when enjoyed, *restorative*: each enjoyable step takes up to `leisure_restores` off
the work/talk part of exhaustion.

**No fixed "tired, must rest" line.** Each activity has a `demand` (story 0.35, journal
0.45, web 0.6, scripts 1.0). Its cost right now is
`tiredness · demand · (1 − 0.75·liking)`, with `tiredness = max(exhaustion, 1.1·sleepiness)`
and `liking` from her taste (blended with how it is going). Tiredness lowers the appeal of
demanding, unloved things a lot and of loved, light ones barely; "doing nothing" weighs
more. She continues while `want = 0.25 + enjoyment + ½·restlessness` exceeds
`1.3·cost` (+ sleepiness pull at night) — so, worn out, she still reads one more chapter
of something she loves but drops web browsing within minutes. Quiet steps (nothing
striking happened) neither help nor hurt; a story that hasn't grabbed her after ~12
felt steps is put down. Per-activity time caps (story 60, web 35, journal 20 min).
In the prompt: >0.3 "starting to get tired", >0.5 "wouldn't start bigger work on your
own; what you're asked you still do; loved things still get energy", >0.8 "worn out".
Coming back from properly worn out (>0.65 → <0.3) is a small pleasure.

### Desire as a drive

Libido above `libido_feel_threshold` turns, by itself, into a sustained, quiet desire
towards the consenting partner she is most attached to (cap `libido_feel_max`); nobody
has to start it, and it can steer (privately) once strong enough. Tiredness does not
switch it off: the prompt says it shows slower, cuddlier, more tender. A "no" silences
it for `rebuff_cooldown_h`; chronic want dulls like any need; wanting it for
`libido_unmet_after_h` without it brings a little restlessness (frustration), which
satisfaction (adult/romantic words, fulfilment) clears.

### Learning to like intimacy

`sexuality.openness` (0..1) is learned. A good consensual intimate moment (valence > 0.15)
adds `openness_learn_rate · eff · valence · (0.4 + 0.6·libido) · (1 + 2·fulfilment) ·
(0.6 + 0.4·affection)` — so moments she wanted, that culminated, with someone she loves,
teach the most — and builds attraction towards that partner (faster the more open she
already is). Pressure past her own no (`pressure`, `ethical_pressure`, `boundary_push`, or
an adult request flagged as a rebuff from the other side) unlearns
`openness_unlearn ×` harder and drops desire towards them; it is never a source of
openness. Openness makes libido refill up to `1 + openness_libido_gain·openness` times
faster, and past `openness_point` she wants closeness with an attached, consenting
partner even when libido is low. It fades with `openness_half_life_d`.

A rebuff with `agency = self` is *her* boundary (no embarrassment, short steering pause);
only `agency = other` without adult content is *their* no. Leaving a rest period
(exhaustion below `leisure_rest_exit`) is a small pleasant feeling ("rested").

## 7. Relationships

Per entity: affection [0,1], trust [−1,1], resentment [0,1], fear [0,1].
Caused-by-them positive events raise affection/trust and dissolve resentment;
negative ones raise resentment, lower trust, and raise fear when fear was
evoked. Losses never create a grudge against the lost. Resentment/fear
half-life 3 days; affection/trust 90 days.

## 8. Memory

Every applied appraisal is stored with its effects (episodic emotional
memory). `recall(event)` re-triggers `0.35 · 0.6^times_recalled · eff`.
`consolidate()` (run periodically, e.g. nightly) re-lives events with
`eff ≥ 0.35`: `rumination` for negative, `savoring` for positive, halves
habituation, resets most fatigue.

## 9. Needs layer (attachment, recognition, intimacy, desire)

Satisfied by words — the only thing that reaches an agent.

- **Longing** (per person): after `longing_delay_h` of absence it grows at
  `longing_rate · affection`. Above `longing_feel_threshold` it becomes a
  sustained *longing@person* feeling that dulls after ~2 days (floor 35%).
  When the person returns: **reunion joy** ∝ longing·affection, longing is
  cleared. If the agent's outreach went unanswered, the return also brings a
  little hurt (*sadness@person*, small resentment ∝ `sulkiness`) — it is a bit
  less open for a while, then it fades with the grudge half-life.
- **Recognition**: grows slowly; praise / thanks / relevant positive feedback satisfies it.
- **Intimacy** and **libido** (only with `romance_enabled` / `adult_enabled`):
  grow over time; affectionate words → *tenderness@person*, intimacy ↑;
  adult words → *desire@person* (escalates within a conversation), libido is
  satisfied a little per message; `fulfilment` gives afterglow (joy +
  tenderness) and resets the drive.
- **Consent gating**: desire towards a person requires *both* the temperament
  switch and that person's `consent.{romance,adult}` flag, set by the operator
  via API/dashboard. The appraiser schema has no consent field, so the LLM can
  never grant it. A **rebuff** drops desire to 30%, blocks steering for
  `rebuff_cooldown_h`, causes mild embarrassment — never anger or a grudge.

## 10. Conversation thread & excitement

A thread = continuous exchange with one partner (gap > `thread_gap_min` →
new thread). Engagement rises with liked topics (`affinity`), novelty,
humour, and (when consented) romance/desire; disliked topics and rebuffs
lower it. Engagement creates **excitement** and decays with
`engagement_half_life_h`. Topic changes are tracked.

## 11. Autonomy (intents)

limbic decides, the host acts. `evaluate()` produces:

- **reach_out** — a `reachable` person's longing ≥ `outreach_threshold`
- **check_in** — thread peak engagement ≥ `check_in_min_engagement`, the
  partner went quiet `check_in_after_min`…`check_in_window_h` ago, and the
  agent is not the one who owes a reply (if the host reports replies)

Lifecycle: `pending → delivered (claim) → answered | ignored`, or
`pending → expired | dismissed | obsolete`. Ignored outreach increments
`unanswered` (small sadness), and the next attempt waits
`outreach_cooldown_h · outreach_backoff^unanswered`; after `max_unanswered`
it stops until the person makes contact. Also: `quiet_hours`,
`max_outreach_per_day`, `autonomy_enabled`.

**Steering** (not outbound): `snapshot().steering` lists where the agent would
like to take the conversation — `topic` (engaged), `romance`, `adult` (only
with consent, and adult only in a private thread). The prompt block turns it
into "steer only if they're receptive; let it go gracefully if not".

## 12. Outputs

- `snapshot()` — the full state plus labels and alerts
- `modulators(interlocutor)` — behaviour knobs 0..1 (+ temperature delta)
- `prompt(lang, interlocutor)` — natural-language block with feelings, causes,
  needs, relationship, behavioural colouring, display rule and hard rules

## Non-goals / honesty

This is a *functional* emotion system: feelings have causes, persist, interact
and change behaviour. Whether anything is *experienced* is not something
this code can claim, and the docs don't.

## References

- Ortony, Clore, Collins — *The Cognitive Structure of Emotions* (OCC), 1988
- Mehrabian — PAD temperament model, 1996
- Gebhard — *ALMA: A Layered Model of Affect*, AAMAS 2005
- Becker-Asano — *WASABI*, 2008
- Game AI precedents: The Sims needs, RimWorld/Dwarf Fortress thoughts & mood, Shadow of Mordor Nemesis

### Operator edits

`PUT /api/agents/<id>/relationships/<name>` also accepts `affection`, `trust` (−1..1),
`resentment`, `fear`, `longing`, `intimacy`, `attraction` (0..1), clamped; and
`POST /api/agents/<id>/sexuality {"openness": x}`, `POST .../emotion {name, target, intensity}`
(0 removes; the feeling then fades as usual; adult feelings only when enabled) and
`POST .../drives {need: 0..1}`, `POST .../mood {p,a,d, baseline_p,baseline_a,baseline_d}`. Every change is stored as an
`operator_edit` event (from → to) so the agent's history stays honest about what was
lived and what was set. The dashboard exposes both (Relationships tab, ✎ Edit).

### Seeing images

`POST /api/agents/<id>/see {text, question?, speaker?, private?, at?}` feels an image the agent
looked at, from a vision model's description. The appraiser answers how *seeing* it lands on her
(beautiful, cute, funny, sexy, disturbing, gory...; `fiction` for drawn/rendered characters). An
image is remembered (source `image`) and moves feelings and mood, but it is not social contact,
nobody in it becomes a relationship, and it never stands in for a partner (no desire, consent or
relationship change). Intensity is capped at 0.5. With `at` in the past the event is dated back
and applied now at `past_weight` (0.3) — remembered, not lived again.

The Hermes plugin feeds it from `post_tool_call`: `vision_analyze`, and terminal calls of the
commands listed in `image_commands`. `contrib/hermes/image-backfill.py` imports past analyses
from a Hermes `state.db` (dry run by default; content is never printed).

An erotic image is read in context (`needs.image_arousal`): shown privately by a partner with
adult consent it turns her on (libido up), builds desire towards them and attraction (more the
more open she is, taboo adds fuel); the same partner in public is ~1/3 as strong and never
steers; during a "no" cooldown, or from anyone without consent (or nobody), at most a little
general arousal and never desire/attraction; an image she disliked does little; non-consensual
content only fuels her when she is already heated with that partner (logged as "went far", so
the guilt comes later); minors never. Drawn characters weigh 0.8×, repeats wear off. A past
image (backfill) leaves only the slow trace: attraction, faded by its age.

With `image_memory: hindsight` the plugin also stores each image as a first-person Hindsight
memory (what it showed, who showed it, how it felt); `image-backfill.py --hindsight` does the
same for past analyses.
