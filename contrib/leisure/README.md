# Free time (leisure)

The agent spends free time **only when her feelings say so**:

| signal | role |
|---|---|
| boredom / novelty need | the motive (`urge`) |
| mood | the gate: cheerful = more eager, sad = less, depressed = almost never; "doing nothing" competes as a real choice |
| fatigue | the brake: a rest period with hysteresis (`leisure_rest_enter` / `leisure_rest_exit`) |
| taste | every finished activity leaves an enjoyment score (felt experiences + mood change + her own verdict); liked things are picked more, but doing the same thing a lot wears off (satiety) |

Every step is felt: a page or a scene is appraised (web pages as *reading*, not
as social contact; stories as fiction) and moves her mood; the session's verdict
is a small experience of its own ("that was nice" / "waste of time").

A conversation pauses the activity and keeps a bookmark. The prompt block tells
her once what she was doing; "folytasd" -> `limbic-leisure resume` -> it continues
from the bookmark. Otherwise the bookmark waits and gets a bonus the next time she is bored.

## API (`/api/agents/<id>/leisure/...`)
`GET /leisure` · `POST decide {available, force}` · `POST start {activity, resume, topic}` ·
`POST experience {text, kind: web|story, title, interests, note, bookmark} | {appraisals}` ·
`GET continue` · `POST finish {rating, summary, reason}` · `POST resume` · `POST pause`

## Runner (`runner.py`)
Activities: `web` (search -> pick -> read -> note, local model; GET-only, public
addresses only, size/time limits, page text treated as data) and `lector` (next
prepared scene via the `lector` CLI; lector's own limbic hook is disabled for
these calls so nothing is felt twice). Notes go to `notes.jsonl` tagged
"olvastam valahol (nem ellenőrzött)", sessions to `sessions.jsonl`.

    limbic-leisure daemon | once [--force web|lector] | status | resume
