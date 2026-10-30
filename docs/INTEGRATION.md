# Integration

The contract is two calls per turn and one periodic job:

```
before LLM call   → prompt block (and optionally modulators) into the context
after the turn    → observe(user message [+ agent reply]) → appraisal → feelings
periodically      → consolidate()   (e.g. nightly cron)
```

## 1. Library

```python
from limbic import Limbic, Store, LLMAppraiser

store = Store("/var/lib/limbic/limbic.db")
mind = Limbic("aria", store=store)
mind.appraiser = LLMAppraiser(BASE_URL, MODEL, api_key=KEY, temperament=mind.temperament)

def on_user_message(text, user):
    system = BASE_SYSTEM + "\n\n" + mind.prompt(lang="en", interlocutor=user)
    knobs = mind.modulators(user)
    reply = llm(system, text, temperature=0.7 + knobs["temperature_delta"])
    mind.observe(text, speaker=user, context=last_turns())
    return reply
```

Events that are not chat can be fed directly:

```python
mind.feel({"event": "deploy_failed", "summary": "the nightly deploy failed again",
           "valence": -0.5, "intensity": 0.5, "agency": "self", "competence": -0.6, "blocked": True})
```

## 2. HTTP sidecar

```
POST /api/agents                      {"id": "aria", "temperament": {...}}
GET  /api/agents                      list with current label
GET  /api/agents/{id}/state?interlocutor=Ádám
GET  /api/agents/{id}/prompt?lang=en&interlocutor=Ádám[&format=text]
POST /api/agents/{id}/observe         {"text": "...", "speaker": "Ádám", "context": "..."}
POST /api/agents/{id}/read            {"text": "<scene>", "title": "...", "memory": "optional reading notes"}
POST /api/agents/{id}/see             {"text": "<image description>", "question": "...", "speaker": "Ádám", "private": false, "at": null}
POST /api/agents/{id}/events          {"appraisal": {...}} | {"appraisals": [...]}
POST /api/agents/{id}/tick
POST /api/agents/{id}/consolidate     {"window_s": 86400}
POST /api/agents/{id}/recall/{event_id}
POST /api/agents/{id}/reset
GET  /api/agents/{id}/events?hours=168
GET  /api/agents/{id}/history?hours=168&points=600
GET|PUT /api/agents/{id}/temperament
DELETE /api/agents/{id}
```

Writes need `Authorization: Bearer $LIMBIC_TOKEN` when a token is configured.

Shell example:

```bash
curl -s "$LIMBIC/api/agents/aria/prompt?lang=en&interlocutor=Ádám&format=text"
curl -s -X POST "$LIMBIC/api/agents/aria/observe" -H "Authorization: Bearer $TOK" \
     -H 'Content-Type: application/json' -d '{"text":"great job","speaker":"Ádám"}'
```

Nightly consolidation via cron:

```
30 3 * * *  curl -s -X POST -H "Authorization: Bearer $TOK" $LIMBIC/api/agents/aria/consolidate
```

## 3. Framework hooks (sketch)

Any agent framework with a pre-LLM and post-turn hook can do it:

- **pre-LLM**: `GET /prompt` → append to system prompt (or as a context message).
  Optionally use `modulators.temperature_delta` on sampling.
- **post-turn**: `POST /observe` with the user message, speaker identity, and the last
  few turns as `context`.
- **memory system**: when the agent's long-term memory surfaces an old episode, call
  `recall/{event_id}` (or `feel` a weaker appraisal) so remembering *feels* like something.
- **cron**: nightly `consolidate`.

For Hermes Agent there is a ready plugin — see section 4 and the full guide [HERMES.md](HERMES.md).

## 4. Hermes plugin (recommended): feelings + autonomy, no cron

`contrib/hermes/plugin/` is a native Hermes plugin. Copy it to
`<HERMES_HOME>/plugins/limbic`, add `limbic` to `plugins.enabled` of every profile
that should have feelings, and configure it in **Desktop → Settings → Plugins →
limbic** (or `plugins.entries.limbic.settings.*`). Per profile you choose the limbic
agent id, language, and whether **Feelings** and **Autonomy** are on.

- Images: an image the agent sees (attached + described by `auxiliary.vision`, a `vision_analyze`
  call, or a script listed in `image_commands`) is felt via `/see` *before* the model answers, and
  the model gets it as its own sight. Details: [HERMES.md § 6](HERMES.md#6-images-let-it-feel-what-it-sees).
- `pre_llm_call` injects the inner-state block (user-message channel; the system
  prompt and its cache are untouched); `post_llm_call` appraises the exchange in
  a background thread and reports the channel the person used.
- **Autonomy** runs as a daemon thread inside every host process that can deliver:
  the messaging gateway (Telegram, Discord, …) and the Desktop/TUI backend. Each
  tick it asks limbic for pending intents, picks the channel (default: where the
  person last talked; fallback to the preference list after `fallback_after_min`),
  claims the intent (atomic — two hosts never both send) and starts a turn in that
  chat with `ctx.inject_message`. The agent writes the message itself; normal
  delivery sends it. Gateway delivery needs
  `plugins.entries.limbic.allow_gateway_injection: true` and a `routes` entry
  (`{"Person": {"telegram": {"chat_id": …, "user_id": …, "chat_type": "dm"}}}`);
  Desktop delivery needs a live Desktop session with that person.
- The injected impulse is prefixed `⟦limbic:` so the agent knows it is its own
  impulse, and the plugin never appraises it as the person's words.
- A failed delivery dismisses the intent, so it never counts as "ignored".

## 5. Autonomy without the plugin (cron gate)

limbic produces intents; something has to turn them into messages.

**Poll** — `GET /api/agents/{id}/intents?status=pending`, then
`POST /intents/{iid}/claim?lang=hu` returns the intent and a ready
instruction for the agent's LLM ("you decided to message X because …, write
it in your voice"). Send the LLM's output on the person's channel
(`intent.contact`).

**Webhook** — set `webhook_url` in the temperament; the server's scheduler
(every `--autonomy-interval` s) POSTs `{agent, intent, instruction, instruction_hu}`.
Claim it after sending.

**Hermes cron** — `contrib/hermes/limbic-outreach.sh` is a cron *pre-run
script*: it prints `{"wakeAgent": false}` when there is nothing to do (the
agent is not woken, no tokens), otherwise it claims one intent and prints the
instruction, which Hermes prepends to the job prompt; the agent writes the
message and Hermes delivers it to the job's `deliver` target.

```
schedule: every 10m
script:   /opt/limbic/contrib/hermes/limbic-outreach.sh
env:      LIMBIC_AGENT=aria LIMBIC_URL=http://limbic.example:8770 LIMBIC_TOKEN_FILE=/path/to/limbic.token
prompt:   "If an instruction from limbic is above, write exactly that message and nothing else."
deliver:  telegram (the person's chat)
```

Report replies (`POST /api/agents/{id}/reply`) after each agent answer so
check-ins never fire when the agent is the one who owes a reply. Mark
private 1:1 conversations with `"private": true` on `observe`.

Operator-only relationship settings:

```
PUT /api/agents/{id}/relationships/{name}
    {"reachable": true, "contact": "telegram:123", "consent": {"romance": true, "adult": false}}
```

## Choosing an appraiser model

The appraiser is a small, structured, frequent call. A local 3–8B instruct
model is enough; keep `temperature` low. Calibrate: ordinary chat must come
back at intensity ≤ 0.1, otherwise the agent becomes melodramatic.


## Stories (reading manga, novels, webtoons)

`POST /read` appraises a scene as **fiction** (`Appraisal.fiction = true`): the agent is moved like a
human reader. Mood shifts, she grows fond of characters or dislikes villains, suspense becomes hope or
fear, and strong scenes are stored as episodic memories that nightly consolidation re-lives.
Fiction is deliberately fenced off from her own life:

- `fiction_gain` (temperament, default 0.7) scales it below real events;
- it never counts as social contact (no loneliness relief, no conversation thread);
- characters are `fictional` relationships, never people: no longing, no outreach, and a character
  sharing a real person's name gets its own `"<name> (story)"` entry;
- erotic/romance/taboo/fulfilment/consent fields are zeroed, so a story never feeds desire.

A reader tool calls it after each scene the agent actually reads (not on peeks). See the sibling
project [lector](https://github.com/fistu4088/lector) for a ready-made one.
