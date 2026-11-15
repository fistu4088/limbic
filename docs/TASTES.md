# Ízlés, határok és kemény szabályok: hol vannak

Három réteg, három hely. Egymást nem írják felül.

## 1. Kemény szabályok (nem tanulhatók, nem prompt)

- **Fájl:** `limbic/rules.py` (a repo gyökerétől: `projects/limbic/limbic/rules.py`)
- **Kód:**
  - `MINOR_TAGS`, `SEXUAL_TAGS`, `NONCONSENT_TAGS`: a tiltott címkék
  - `LEISURE_BLOCKED_EFFECTS` / `LEISURE_SAFE_ACTIONS`: a szabadidő bármely téma lehet; a kifelé ható vagy önmagát módosító tett tiltott (hatásalapú, fail-closed)
  - `RULE_IDS`: a szabályok azonosítói és magyar leírásai
  - `taste_allowed(tags)`: fail-closed szűrő, ezt hívja az ízlés-réteg
  - `check_leisure_action(action)`: `PermissionError`, ha nem olvasás
- **Tesztek:** `tests/test_tastes.py` → `HardRuleTests`
- **Módosítás:** csak kódban, commitolva, teszttel. Nem SOUL.md-ből, nem DB-ből, nem az agent tapasztalatából.

Megjegyzés: a `limbic/appraisal.py` 64. sorában már van egy kiskorú-szabály (`if self.minor:`). Ez most párhuzamosan él a `rules.py`-val. Egyesíteni kell, a pontos helyét a `rules.py` fogja adni.

## 2. Ízlés és határ (tanulható, lépcsőzetes)

- **Tárolás:** az agent állapotában, `state.leisure["tastes"]` (a `limbic.db` `agents.state` JSON-jában)
- **Kód:** `limbic/tastes.py`
  - `record(...)`: egy tapasztalatból tanul. Egy vagy több „nem” csak csökkenti a súlyt és a valószínűséget, nem zár ki.
  - `forbid(...)`: fix határ, csak kifejezett kérésre (`explicit_stop=True`). Ez az egyetlen út a fix tiltásig.
  - `score(...)`, `bias(...)`, `summary(...)`: a `summary` az `interests` / `dislikes` / `boundaries` listákat adja (küszöb: `INTEREST_THRESH = 0.08`).
  - Al-témák: `horror/jumpscare` rossz élménye a `horror` szülőt csak 0,3-szoros súllyal érinti.
  - `seed(...)`: prior-okat tölt be.
- **Hol íródik:** `limbic/leisure.py` → `_close()`, befejezett tevékenység után (csak ha volt lépés és nem megszakított).
- **Hol szűr:** `contrib/leisure/runner.py` a szerver `/leisure` válaszának `tastes` mezőjéből. Határ-téma elvetése előtt `elvetve` jegyzet íródik (téma-tartalom nélkül), és `check_leisure_action("search")` fut.

## 3. Priorok (kiinduló feltételezés, gyengén súlyozva)

- **Fájl:** `data/priors/<agent>.json` (éles agentnek, git-en kívül)
- **Minta:** `contrib/priors/example-teszt.json` (szintetikus, a teszt-agenthez)
- **Hatás:** `conf = 0.2`. A prior „boundary” bejegyzése is csak enyhe negatív prior, nem fix határ. Tapasztalat gyorsan felülírja; tapasztalat nélkül 30 nap alatt felére csökken.
- **Viki:** a `/root/.hermes/SOUL.md` érdeklődési részei ide kerülnek majd, de csak a teszt-profil jóváhagyása után

## Ami még hiányzik

- A beszélgetésből jövő kifejezett tiltás (`forbid` hívása) még nincs bekötve a chat-útvonalba. Ez élő kódhoz nyúl, ezért külön jóváhagyást kér.
- A beleegyezés-szabály (hallgatólagos vs. kifejezett ellenkezés) kapcsolati viselkedés; a szabadidős rétegben nincs kezelve.
