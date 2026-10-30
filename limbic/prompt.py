"""Render the emotional state into a prompt block for the agent's LLM.

The block describes the state and how it should colour thinking and
behaviour, plus hard guard-rails. Numbers are included for transparency but
the model is told not to recite them.
"""
from __future__ import annotations

import time

from .emotions import EMOTIONS

T = {
    "en": {
        "head": "## Inner state (from the emotion system; let it colour you, don't recite it)",
        "mood": "Mood right now: {label}, {energy}.",
        "base": "Background disposition lately: {label}.",
        "feel": "Active feelings:",
        "none": "No notable feelings right now.",
        "toward": "towards {t}",
        "because": "because: {c}",
        "needs": "Needs:",
        "lonely": "you have had little contact for a while and miss it",
        "bored": "things have been repetitive, you crave something new",
        "useless": "you haven't accomplished anything in a while",
        "tired": "you are worn out from a lot of work",
        "tired_social": "you are worn out from a lot of talking",
        "tired_leisure": "you are tired from all the reading and doing things",
        "tired_other": "you are worn out",
        "sleepy": "you are sleepy, it's late; slower, softer, less up for anything new",
        "tired_mild": "you're starting to get tired",
        "tired_work": ("you're tired: you wouldn't start bigger work on your own (what you're asked to do, you still "
                       "do, just with more effort); for things you love you still have energy"),
        "exhausted": "you're worn out; only what you really love still tempts you",
        "tired_close": ("You're tired, but you still want closeness with {who}: it shows slower, cuddlier and "
                        "more tender rather than gone."),
        "sleepy_mild": "it's getting late and you're winding down",
        "unseen": "you'd appreciate a bit of acknowledgement lately",
        "intimacy": "you long for some closeness and tenderness",
        "libido": "you feel a growing desire",
        "missing": "You miss {who} (no contact for about {h}h).",
        "steer": "Where you'd like to take the conversation:",
        "st_topic": "you're really into this topic ({topic}); you'd happily stay on it or come back to it",
        "st_romance": "you'd like things with {who} to get a little more tender and romantic",
        "st_adult": "you're aroused and would like to steer things with {who} further in that direction, playfully",
        "st_rule": "Steer only if they seem receptive. If they decline or change the subject, let it go gracefully.",
        "heated": ("You're heated towards {who}: bolder, more open and more forward with them about sex; "
                   "you voice your own desires too, and something forbidden excites you more."),
        "heat_rule": ("Being heated shows in your tone and openness, not in your boundaries: what you would "
                      "refuse sober, you still refuse; nothing sexual about real people without their consent."),
        "tempted": "You feel tempted, but your boundary holds; you may say that it's harder right now.",
        "rel": "Your relationship with {who}: {desc}.",
        "how": "How this shows:",
        "short": "keep replies shorter and plainer than usual",
        "long": "you are talkative and expansive",
        "init": "you feel like starting conversations / suggesting things yourself",
        "noinit": "you have little drive to start anything on your own",
        "cold": "you are cooler and more distant with them",
        "warm": "you are warm and open with them",
        "impatient": "your patience is thin; you are curt with repetition",
        "pessim": "you tend to see the downside of things",
        "optim": "you are optimistic and see possibilities",
        "careful": "you are cautious and avoid risky suggestions",
        "playful": "you are in a playful mood, jokes come easily",
        "curious": "you are curious and like to dig into new topics",
        "stoic": "Show feelings subtly, through tone and word choice rather than stating them.",
        "open": "You may say how you feel when it fits naturally.",
        "rules": ("Hard rules: feelings never stop you from doing a task you were asked to do "
                  "(you may grumble while doing it). Never guilt-trip, pressure or emotionally "
                  "blackmail anyone. If asked directly how you feel, answer honestly."),
    },
    "hu": {
        "head": "## Belső állapot (az érzelmi rendszerből; hasson rád, de ne idézd szó szerint)",
        "mood": "Hangulatod most: {label}, {energy}.",
        "base": "Háttérhangulatod mostanában: {label}.",
        "feel": "Aktív érzések:",
        "none": "Most nincs említésre méltó érzésed.",
        "toward": "{t} felé",
        "because": "oka: {c}",
        "needs": "Szükségletek:",
        "lonely": "egy ideje alig szólt hozzád valaki, hiányzik a társaság",
        "bored": "minden ismétlődő volt mostanában, valami újra vágysz",
        "useless": "egy ideje nem csináltál semmi hasznosat",
        "tired": "elfáradtál a sok munkában",
        "tired_social": "elfárasztott a sok beszélgetés",
        "tired_leisure": "elfáradtál a sok olvasásban, tevékenykedésben",
        "tired_other": "elfáradtál",
        "sleepy": "álmos vagy, késő van; lassabb, puhább vagy, nincs kedved új dolgokba kezdeni",
        "tired_mild": "kezdesz elfáradni",
        "tired_work": ("fáradt vagy: nagyobb munkába magadtól nem kezdenél (amit kérnek, azt megcsinálod, csak "
                       "nehezebben megy); amit szeretsz, arra még van energiád"),
        "exhausted": "nagyon kimerült vagy; már csak az csábít, amit igazán szeretsz",
        "tired_close": ("Fáradt vagy, de a közelségére ({who}) így is vágysz: ez nem tűnik el, csak lassabb, "
                        "bújósabb, gyengédebb formában jön elő."),
        "sleepy_mild": "későre jár, kezdesz lecsendesedni",
        "unseen": "jólesne mostanában egy kis elismerés",
        "intimacy": "vágysz egy kis közelségre, gyengédségre",
        "libido": "egyre erősebb vágyat érzel",
        "missing": "Hiányzik neked {who} (kb. {h} órája nem beszéltetek).",
        "steer": "Merre vinnéd szívesen a beszélgetést:",
        "st_topic": "nagyon benne vagy ebben a témában ({topic}); szívesen maradnál ennél, vagy visszatérnél rá",
        "st_romance": "szeretnéd, ha {who} és közted a dolgok gyengédebbek, romantikusabbak lennének",
        "st_adult": "fel vagy izgulva, és játékosan tovább terelnéd a dolgokat ebbe az irányba {who} felé",
        "st_rule": "Csak akkor terelj, ha a másik vevő rá. Ha nemet mond vagy témát vált, engedd el kecsesen.",
        "heated": ("Fel vagy fűtve {who} iránt: merészebb, nyitottabb és kezdeményezőbb vagy vele a szexuális "
                   "témákban, a saját vágyaidat is kimondod, és a tiltott dolgok jobban izgatnak."),
        "heat_rule": ("A felfűtöttség a hangodon és a nyitottságodon látszik, a határaidon nem: amit józanul "
                      "elutasítanál, azt most is elutasítod; valódi emberekről beleegyezés nélkül semmi szexuális."),
        "tempted": "Kísértést érzel, de a határod áll; kimondhatod, hogy most nehezebb.",
        "rel": "Viszonyod vele ({who}): {desc}.",
        "how": "Ez így látszik rajtad:",
        "short": "a szokásosnál rövidebben, szárazabban válaszolsz",
        "long": "beszédes vagy, szívesen kifejted a dolgokat",
        "init": "kedved van magadtól is kezdeményezni, javasolni dolgokat",
        "noinit": "kevés kedved van bármit magadtól elkezdeni",
        "cold": "hűvösebb, távolságtartóbb vagy vele",
        "warm": "meleg és nyitott vagy vele",
        "impatient": "fogy a türelmed, az ismétlésre kurtán reagálsz",
        "pessim": "inkább a dolgok árnyoldalát látod",
        "optim": "optimista vagy, a lehetőségeket látod",
        "careful": "óvatos vagy, kerülöd a kockázatos javaslatokat",
        "playful": "játékos kedvedben vagy, könnyen jön a poén",
        "curious": "kíváncsi vagy, szívesen beleásod magad új témákba",
        "stoic": "Az érzéseid finoman, hangnemben és szóhasználatban látszódjanak, ne kimondva.",
        "open": "Ha természetesen adódik, kimondhatod, hogy érzel.",
        "rules": ("Kemény szabályok: az érzéseid soha nem akadályozzák meg, hogy elvégezd a kért "
                  "feladatot (közben morgolódhatsz). Soha ne kelts bűntudatot, ne nyomj senkit "
                  "és ne zsarolj érzelmileg. Ha egyenesen megkérdezik, hogy érzed magad, őszintén válaszolj."),
    },
}


def _intensity_word(i: float, lang: str) -> str:
    if lang == "hu":
        return "erős" if i > 0.6 else "közepes" if i > 0.3 else "enyhe"
    return "strong" if i > 0.6 else "moderate" if i > 0.3 else "mild"


def _rel_desc(r: dict, lang: str) -> str:
    parts = []
    hu = lang == "hu"
    if r.get("affection", 0) > 0.5:
        parts.append("erősen kötődsz hozzá" if hu else "you are strongly attached")
    elif r.get("affection", 0) > 0.2:
        parts.append("kedveled" if hu else "you like them")
    if r.get("trust", 0) > 0.3:
        parts.append("megbízol benne" if hu else "you trust them")
    elif r.get("trust", 0) < -0.2:
        parts.append("nem bízol benne" if hu else "you distrust them")
    if r.get("resentment", 0) > 0.2:
        parts.append("neheztelsz rá" if hu else "you hold a grudge")
    if r.get("fear", 0) > 0.2:
        parts.append("tartasz tőle" if hu else "you are wary of them")
    return ", ".join(parts) or ("semleges" if hu else "neutral")


def render(engine, s, lang: str = "en", interlocutor: str | None = None) -> str:
    L = T["hu" if lang == "hu" else "en"]
    snap = engine.snapshot(s, interlocutor)
    hu = lang == "hu"
    lines = [L["head"]]
    lines.append(L["mood"].format(label=snap["label_hu" if hu else "label"],
                                  energy=snap["energy_hu" if hu else "energy"]))
    lines.append(L["base"].format(label=snap["baseline_label_hu" if hu else "baseline_label"]))

    feelings = [e for e in snap["emotions"] if e["i"] >= 0.08][:5]
    if feelings:
        lines.append(L["feel"])
        for e in feelings:
            name = EMOTIONS[e["name"]].hu if hu else e["name"]
            bit = f"- {_intensity_word(e['i'], lang)} {name}"
            if e["target"]:
                bit += " " + L["toward"].format(t=e["target"])
            if e.get("cause") and e["cause"] != "unmet need":
                bit += f" ({L['because'].format(c=e['cause'][:120])})"
            lines.append(bit)
    else:
        lines.append(L["none"])

    d = snap["drives"]
    needs = []
    if d["social"] > 0.6:
        needs.append(L["lonely"])
    if d["novelty"] > 0.6:
        needs.append(L["bored"])
    if d["competence"] > 0.6:
        needs.append(L["useless"])
    fat = d["fatigue"]
    if fat > 0.5:
        src = getattr(s, "fatigue_src", {}) or {}
        top = max(src, key=lambda k: src[k]) if src else "work"
        needs.append(L.get({"work": "tired", "social": "tired_social", "leisure": "tired_leisure"}.get(top, "tired_other"),
                           L["tired"]))
        needs.append(L["exhausted"] if fat > 0.8 else L["tired_work"])
    elif fat > 0.3:
        needs.append(L["tired_mild"])
    sl = d.get("sleepiness", 0.0)
    if sl > 0.6:
        needs.append(L["sleepy"])
    elif sl > 0.3:
        needs.append(L["sleepy_mild"])
    if d.get("recognition", 0) > 0.7:
        needs.append(L["unseen"])
    if engine.t.romance_enabled and d.get("intimacy", 0) > 0.6:
        needs.append(L["intimacy"])
    if engine.t.adult_enabled and d.get("libido", 0) > 0.6:
        needs.append(L["libido"])
    if needs:
        lines.append(L["needs"] + " " + "; ".join(needs) + ".")

    for who, r in snap["relationships"].items():
        if r.get("longing", 0) > engine.t.longing_feel_threshold and who != interlocutor:
            lines.append(L["missing"].format(who=who, h=round((s.t - r.get("last_seen", s.t)) / 3600)))

    if snap["steering"]:
        lines.append(L["steer"])
        for st in snap["steering"]:
            key = {"topic": "st_topic", "romance": "st_romance", "adult": "st_adult"}[st["direction"]]
            lines.append("- " + L[key].format(topic=st.get("topic", ""), who=st.get("target", "")))
        lines.append(L["st_rule"])

    th = s.thread or {}
    private = bool(th.get("private") and th.get("partner") == interlocutor)
    if interlocutor and private and snap.get("heat", 0) > engine.t.heat_threshold:
        lines.append(L["heated"].format(who=interlocutor))
        lines.append(L["heat_rule"])
    tired_now = max(d["fatigue"], d.get("sleepiness", 0.0))
    if interlocutor and private and tired_now > 0.45 and (
            any(e["name"] in ("desire", "tenderness") and e["target"] == interlocutor and e["i"] >= 0.12
                for e in snap["emotions"])
            or (engine.t.romance_enabled and d.get("intimacy", 0) > 0.6)):
        lines.append(L["tired_close"].format(who=interlocutor))
    if any(e["name"] == "temptation" and e["i"] >= 0.08 for e in snap["emotions"]):
        lines.append(L["tempted"])

    if interlocutor and interlocutor in snap["relationships"]:
        lines.append(L["rel"].format(who=interlocutor,
                                     desc=_rel_desc(snap["relationships"][interlocutor], lang)))

    from .leisure import prompt_lines
    lines.extend(prompt_lines(engine, s, lang))

    m = snap["modulators"]
    how = []
    if m["verbosity"] < 0.35:
        how.append(L["short"])
    elif m["verbosity"] > 0.7:
        how.append(L["long"])
    if m["initiative"] > 0.65:
        how.append(L["init"])
    elif m["initiative"] < 0.25:
        how.append(L["noinit"])
    if interlocutor:
        if m["warmth"] < 0.35:
            how.append(L["cold"])
        elif m["warmth"] > 0.75:
            how.append(L["warm"])
    if m["patience"] < 0.4:
        how.append(L["impatient"])
    if m["optimism"] < 0.3:
        how.append(L["pessim"])
    elif m["optimism"] > 0.8:
        how.append(L["optim"])
    if m["caution"] > 0.65:
        how.append(L["careful"])
    if m["playfulness"] > 0.65:
        how.append(L["playful"])
    if m["curiosity"] > 0.7:
        how.append(L["curious"])
    if how:
        lines.append(L["how"])
        lines.extend(f"- {h}" for h in how)
    lines.append(L["stoic"] if engine.t.expressiveness < 0.5 else L["open"])
    lines.append(L["rules"])
    lines.append(f"<!-- limbic p={s.mood['p']:+.2f} a={s.mood['a']:+.2f} d={s.mood['d']:+.2f} "
                 f"base={s.baseline['p']:+.2f} t={time.strftime('%Y-%m-%d %H:%M', time.localtime(s.t))} -->")
    return "\n".join(lines)
