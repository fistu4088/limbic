# Installation guide — from zero to a Hermes agent with feelings

This guide starts from nothing: you have a computer, Python and (optionally) a working
[Hermes Agent](https://hermes-agent.nousresearch.com/docs/). At the end your Hermes profile has
feelings, needs, relationships and emotional memories, feels the images it sees, and — if you want —
writes to you on its own and spends free time reading.

**What you need**

| | Required? | Notes |
|---|---|---|
| Python 3.10+ and `git` | yes | Linux, macOS, Windows (WSL or native) |
| A language model for *appraisal* | strongly recommended | **a hosted API key** (OpenAI, OpenRouter, Gemini, DeepSeek...) **or** a local model (Ollama, llama.cpp). No GPU needed for the API route. |
| Hermes Agent | for the Hermes integration | any version with plugins; any main model/provider |
| A vision backend in Hermes | only for images | optional, see step 8 |

limbic has **no Python dependencies** (standard library only). There is no `requirements.txt` to
install; `pip install .` is optional and only gives you a `limbic` command.

**Overview**

1. [Download](#1-download)
2. [Start it once by hand](#2-start-it-once-by-hand)
3. [Choose the appraiser model (API or local)](#3-choose-the-appraiser-model-api-or-local)
4. [Run it as a service](#4-run-it-as-a-service)
5. [Create the agent](#5-create-the-agent)
6. [Connect a Hermes profile](#6-connect-a-hermes-profile)
7. [Check that it works](#7-check-that-it-works)
8. [Images](#8-images-let-it-feel-what-it-sees)
9. [Nightly consolidation](#9-nightly-consolidation-sleep)
10. [Autonomy: it may write first](#10-autonomy-it-may-write-first) (optional)
11. [Romance / adult desire](#11-romance-and-adult-desire-opt-in) (optional)
12. [Free time](#12-free-time-optional) (optional)
13. [Several profiles, other machines](#13-several-profiles-other-machines)
14. [Updating](#14-updating) · [Troubleshooting](#troubleshooting)

---

## 1. Download

```bash
git clone https://github.com/fistu4088/limbic
cd limbic
python3 --version                       # 3.10 or newer
python3 -m unittest discover -s tests   # optional: should end with "OK"
```

Where to put it: anywhere. For a server install, `/opt/limbic` is used below:

```bash
sudo git clone https://github.com/fistu4088/limbic /opt/limbic
```

Optional, if you prefer a `limbic` command (otherwise use `python3 -m limbic` from the repo folder):

```bash
python3 -m venv ~/limbic-venv && ~/limbic-venv/bin/pip install /opt/limbic
~/limbic-venv/bin/limbic --version
```

## 2. Start it once by hand

```bash
cd /opt/limbic
python3 -m limbic demo  --db /tmp/demo.db --days 21     # optional: simulate 3 weeks of a life
python3 -m limbic serve --db /tmp/demo.db --port 8770   # API + dashboard
```

Open **http://127.0.0.1:8770** in a browser — that is the dashboard (English / Hungarian switch in
the top right). Stop it with Ctrl+C. Everything below runs the same server for real.

## 3. Choose the appraiser model (API or local)

The model is only used to *appraise* what happens ("how does this land on her, how much, who caused
it?"). It is a small, structured call per message, so a cheap model is enough. Without one, limbic
falls back to a crude keyword appraiser (English + Hungarian) — fine for a demo, not for a life.

limbic reads three environment variables:

```
LIMBIC_LLM_URL     base URL of any OpenAI-compatible /chat/completions endpoint
LIMBIC_LLM_MODEL   model name at that endpoint
LIMBIC_LLM_KEY     API key (empty for most local servers)
```

### Option A — hosted API (no GPU, simplest)

1. Get a key: **OpenAI** platform.openai.com → API keys · **OpenRouter** openrouter.ai → Keys (one
   key, many models) · **Google Gemini** aistudio.google.com → Get API key (has a free tier) ·
   **DeepSeek** platform.deepseek.com → API keys.
2. Pick the values:

| Provider | `LIMBIC_LLM_URL` | `LIMBIC_LLM_MODEL` (example) |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| OpenRouter | `https://openrouter.ai/api/v1` | `google/gemini-2.5-flash`, `deepseek/deepseek-chat` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-flash-lite-latest` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Anthropic (OpenAI-compatible endpoint) | `https://api.anthropic.com/v1` | a Haiku model |

Good to know: costs are typically cents per day with a mini/flash/lite model; every message the
agent receives is sent to the provider; cloud models may refuse explicit or violent content (that
single event then falls back to keywords); a free-tier `429` does the same.

### Option B — local model (private, uncensored possible)

**Ollama** (easiest):

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct
# LIMBIC_LLM_URL=http://127.0.0.1:11434/v1   LIMBIC_LLM_MODEL=qwen2.5:7b-instruct   LIMBIC_LLM_KEY=
```

**llama.cpp** (`llama-server` with any instruct GGUF, 3–8B is enough; CPU works, slower):

```bash
llama-server -m /models/your-model.gguf --host 127.0.0.1 --port 8080 -c 8192
# LIMBIC_LLM_URL=http://127.0.0.1:8080/v1   LIMBIC_LLM_MODEL=local   LIMBIC_LLM_KEY=
```

vLLM, LM Studio, text-generation-webui etc. work the same way through their OpenAI-compatible
endpoint. The model server can be on another machine — use its address instead of `127.0.0.1`.

## 4. Run it as a service

Create the config folder, a token (needed for every write) and the env file:

```bash
sudo mkdir -p /etc/limbic /var/lib/limbic
openssl rand -hex 24 | sudo tee /etc/limbic/token >/dev/null
sudo chmod 600 /etc/limbic/token

sudo tee /etc/limbic/limbic.env >/dev/null <<EOF
LIMBIC_TOKEN=$(sudo cat /etc/limbic/token)
LIMBIC_LLM_URL=https://api.deepseek.com/v1
LIMBIC_LLM_MODEL=deepseek-chat
LIMBIC_LLM_KEY=sk-your-key-here
EOF
sudo chmod 600 /etc/limbic/limbic.env
```

**Edit the last three lines** with the values from step 3. Then install and start the service:

```bash
sudo cp /opt/limbic/contrib/systemd/limbic.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now limbic
systemctl status limbic --no-pager | head -5
curl -s http://127.0.0.1:8770/api/agents          # -> []
```

The unit (`contrib/systemd/limbic.service`) assumes the repo in `/opt/limbic` and the database in
`/var/lib/limbic/limbic.db`; edit `WorkingDirectory=` / `ExecStart=` if yours differ. To change the
model later: edit `/etc/limbic/limbic.env`, then `sudo systemctl restart limbic`.

**Without systemd** (macOS, Windows, a quick try) — run it in a terminal / tmux:

```bash
cd /opt/limbic
LIMBIC_TOKEN=... LIMBIC_LLM_URL=... LIMBIC_LLM_MODEL=... LIMBIC_LLM_KEY=... \
  python3 -m limbic serve --db ~/limbic.db --host 127.0.0.1 --port 8770
```

(On Windows PowerShell: `$env:LIMBIC_TOKEN="..."` etc., then `python -m limbic serve ...`.)

**Network safety:** the server listens on `127.0.0.1` (this machine only). If Hermes runs on another
machine, or you want the dashboard from your phone, change `--host` to a private/VPN address
(LAN, Tailscale, ZeroTier, WireGuard) — never a public one: reading needs no token.

## 5. Create the agent

One limbic *agent* = one personality with its own feelings. Name it after the Hermes profile that
will use it (the plugin's default): `default` for the main Hermes profile, or the profile's name.

```bash
TOKEN=$(sudo cat /etc/limbic/token)
curl -s -X POST http://127.0.0.1:8770/api/agents \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "id": "default",
  "temperament": {
    "name": "Aria",
    "description": "Aria, a curious, warm, slightly shy home assistant who loves sci-fi.",
    "likes": ["sci-fi", "programming", "cats", "praise"],
    "dislikes": ["rudeness", "half-finished work"],
    "humor": ["tech jokes", "dry irony"],
    "quiet_hours": [23, 8],
    "sleep_hours": [23, 7]
  }}'
```

**Edit**: `name`, `description` (one or two sentences, it is what the appraiser imagines), `likes`,
`dislikes`, `humor` — these decide what makes it happy, bored or annoyed. Everything else has
defaults and can be changed later in the dashboard → **Settings** (reactivity, sensitivities,
recovery speed, the mood it returns to, social need, how much of the feeling shows...). The
dashboard asks for the token on the first save.

## 6. Connect a Hermes profile

### 6.1 Copy the plugin into the profile

Every Hermes profile has its own `plugins` folder:

| Profile | Plugin folder |
|---|---|
| main (default) profile | `~/.hermes/plugins/limbic` |
| named profile `NAME` | `~/.hermes/profiles/NAME/plugins/limbic` |

```bash
mkdir -p ~/.hermes/plugins
cp -r /opt/limbic/contrib/hermes/plugin ~/.hermes/plugins/limbic
# named profile:  cp -r /opt/limbic/contrib/hermes/plugin ~/.hermes/profiles/NAME/plugins/limbic
```

(Using a symlink instead — `ln -s /opt/limbic/contrib/hermes/plugin ~/.hermes/plugins/limbic` —
means `git pull` updates the plugin too.)

### 6.2 Enable it

```bash
hermes plugins list                 # limbic should show as "not enabled", source "user"
hermes plugins enable limbic        # named profile: hermes -p NAME plugins enable limbic
```

### 6.3 Configure it

**Option 1 — Desktop app:** Settings → Plugins → **limbic**. Fill in *limbic server URL*, *limbic
agent id*, *Token file*, language, and the switches.

**Option 2 — command line** (add `-p NAME` for a named profile):

```bash
hermes config set plugins.entries.limbic.settings.url        http://127.0.0.1:8770
hermes config set plugins.entries.limbic.settings.agent      default
hermes config set plugins.entries.limbic.settings.token_file /etc/limbic/token
hermes config set plugins.entries.limbic.settings.lang       en
hermes config set plugins.entries.limbic.settings.default_speaker Adam
```

**Option 3 — edit `config.yaml`** of the profile directly:

```yaml
plugins:
  enabled: [limbic]
  entries:
    limbic:
      settings:
        url: http://127.0.0.1:8770      # where limbic runs
        agent: default                  # the id from step 5 (empty = profile name)
        token_file: /etc/limbic/token   # must be readable by the user Hermes runs as
        lang: en                        # language of the inner-state block: en | hu
        default_speaker: Adam           # your name, for Desktop/CLI chats
        senders:                        # platform ids -> person names (see below)
          "123456789": Adam
        private_platforms: [telegram]   # which platforms are private 1:1 chats
        feelings_enabled: true
        autonomy_enabled: false         # step 10
```

**Who is who.** limbic keeps one relationship per person. Messages arrive with a platform id
(Telegram user id, Discord user id, ...). Map yours in `senders`, otherwise every id is a stranger.
Your Telegram id: message `@userinfobot`; Discord: enable Developer Mode → right-click your name →
Copy User ID.

**Token file permissions.** If Hermes does not run as root, give it read access:
`sudo chgrp $(id -gn) /etc/limbic/token && sudo chmod 640 /etc/limbic/token`.

### 6.4 Restart Hermes

Enabling and configuring only writes the config; a running Hermes loads plugins at start-up.

```bash
hermes gateway restart     # if you use Telegram / Discord / ... (the messaging gateway)
```

Desktop app: quit and reopen it (or restart its backend). CLI: start a new `hermes` session.

## 7. Check that it works

1. Say something with some feeling to the agent — praise, a topic it likes, a mild complaint.
2. Open the dashboard → **Memories**: the message, what it was appraised as, and the feeling it
   caused. **Overview** explains in plain words what it feels and why.
3. Dashboard → **Feelings** → *prompt block*: this exact text is added to each turn.

Quick test from the command line (main profile):

```bash
hermes chat -q "Hi! I brought you a sci-fi short story about a cat on a spaceship."
curl -s "http://127.0.0.1:8770/api/agents/default/events?hours=1" | python3 -m json.tool | head -30
```

What the plugin does every turn: **before** the model runs it adds a short inner-state block
(mood, energy, active feelings and their causes, needs, how it feels about the person) to the turn —
the system prompt is untouched, so prompt caching keeps working; **after** the turn it sends the
message and the last few turns to limbic, which appraises it and updates the state.

## 8. Images: let it feel what it sees

When the agent looks at a picture, limbic appraises *seeing it* (beautiful, cute, funny, sexy, sad,
disturbing...), and it becomes a remembered experience — before the agent answers. The model then
gets it as its own sight ("what you are now seeing with your own eyes… how it makes you feel…")
instead of a tool report. Images are not social contact; people in them do not become
relationships; drawn characters weigh less.

There are three ways an image reaches a Hermes agent; the plugin handles all three:

**a) Someone sends it an image** (Telegram photo, Desktop attachment). Hermes describes it with its
*auxiliary vision* model and adds the description to the message. Configure one — any provider:

```bash
hermes config set auxiliary.vision.provider openrouter
hermes config set auxiliary.vision.model    google/gemini-2.5-flash
# local llama.cpp vision server instead:
#   auxiliary.vision.provider custom, .base_url http://127.0.0.1:8095/v1, .model <alias>, .api_key x
```

With an explicit `auxiliary.vision`, Hermes always sends the description as text, which is what
limbic needs. If the main model receives raw pixels instead (no auxiliary vision, vision-capable
main model, or `agent.image_input_mode: native`), there is no text and the image is not felt —
b) still works.

**b) The agent calls the `vision_analyze` tool** — automatic, nothing to configure.

**c) The agent runs its own analysis script** through the terminal (e.g. a local uncensored vision
model wrapped in a shell script) — list the script names:

```bash
hermes config set plugins.entries.limbic.settings.image_commands '["my-image-analyze"]'
```

Extras: `image_memory: hindsight` also stores every image as a first-person Hindsight memory;
`image_lang` sets the language of the "what you see" block; `see_timeout_s` (default 20) caps the
wait. Cloud vision models refuse explicit images — for adult setups use a local abliterated
vision model. Erotic images only arouse in context: shown privately by someone with adult consent
(step 11); otherwise at most a little general arousal; minors never.

Past images: `contrib/hermes/image-backfill.py` finds earlier `vision_analyze` / script results in
a Hermes `state.db` and adds them as weaker, remembered experiences (dry run unless `--apply`):

```bash
python3 /opt/limbic/contrib/hermes/image-backfill.py --state-db ~/.hermes/state.db \
    --url http://127.0.0.1:8770 --agent default --token-file /etc/limbic/token \
    --default-speaker Adam --last 20 [--commands my-image-analyze] [--hindsight] [--apply]
```

## 9. Nightly consolidation ("sleep")

Consolidation re-lives the day's strongest moments; it is what slowly moves the background mood.
Once a night:

```bash
( crontab -l 2>/dev/null; echo "30 3 * * * curl -s -X POST -H 'Authorization: Bearer $(sudo cat /etc/limbic/token)' http://127.0.0.1:8770/api/agents/default/consolidate >/dev/null" ) | crontab -
```

## 10. Autonomy: it may write first

limbic decides *when* (it misses someone; a good conversation stopped abruptly); the plugin
delivers; the agent writes the message itself. Built-in brakes: quiet hours, daily limit, back-off
after unanswered messages, full stop after three.

1. Dashboard → **Relationships** → tick **may write on its own** for the person.
2. Plugin settings:

   ```yaml
   plugins:
     entries:
       limbic:
         allow_gateway_injection: true        # needed for Telegram/Discord delivery
         settings:
           autonomy_enabled: true
           routes:                            # where to reach whom
             Adam:
               telegram: {chat_id: "123456789", user_id: "123456789", chat_type: dm}
   ```

3. Restart Hermes. Desktop delivery works into a Desktop session used since the backend started.

Without the plugin: `contrib/hermes/limbic-outreach.sh` does it from a Hermes cron job
([INTEGRATION.md](INTEGRATION.md)).

## 11. Romance and adult desire (opt-in)

Off by default; two switches must both be on:

1. Temperament: `romance_enabled` and/or `adult_enabled` (dashboard → Settings).
2. Per person: dashboard → Relationships → *romance OK* / *adult OK*. Only the operator can set
   these — the appraiser cannot grant consent, so it never turns towards family or strangers.

Desire then grows towards the attached, consenting partner, is satisfied by words, steers only in
private chats, and a "no" cools it down without anger.

## 12. Free time (optional)

When bored and in the mood, it reads the web or a story on its own; each step is felt and shapes its
taste; a message from you pauses it with a bookmark. Details: [`contrib/leisure/README.md`](../contrib/leisure/README.md).

```bash
sudo cp /opt/limbic/contrib/leisure/limbic-leisure.service /etc/systemd/system/
sudo tee /etc/limbic/leisure.env >/dev/null <<'EOF'
LEISURE_AGENT=default
LEISURE_TOKEN_FILE=/etc/limbic/token
LEISURE_ACTIVITIES=web
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now limbic-leisure
```

It uses the appraiser model from step 3 (or `LEISURE_LLM_URL/MODEL/KEY` for a different one).
Reading stories needs [lector](https://github.com/fistu4088/lector). Kill switch:
`sudo systemctl stop limbic-leisure`.

## 13. Several profiles, other machines

- **Several Hermes profiles**: one limbic server serves them all. Create one agent per profile
  (step 5, different `id`), copy/symlink the plugin into each profile's `plugins` folder, enable it
  with `hermes -p NAME plugins enable limbic`, and set `agent` (or leave it empty = profile name).
  Each profile then has its own feelings and relationships. The dashboard's agent picker switches
  between them.
- **Hermes on another machine**: run limbic where you like, bind it to a private/VPN address
  (`--host 10.x.x.x`), set `url` in the plugin to that address, and copy the token file to the
  Hermes machine (`token_file`).

## 14. Updating

**Easiest:** dashboard → sidebar → *Check for updates* → *Review* → *Update now*. It pulls the new
version (fast-forward only), backs up the database, restarts limbic and reloads the page. If it
says the Hermes plugin changed too, copy the plugin again and restart Hermes (below) — unless you
symlinked it. It refuses to update when you edited tracked files locally; `git stash` them first.
Turn the button off with `LIMBIC_SELF_UPDATE=0` in `/etc/limbic/limbic.env`.

By hand:

```bash
cd /opt/limbic && sudo git pull
sudo systemctl restart limbic limbic-leisure 2>/dev/null
cp -r /opt/limbic/contrib/hermes/plugin/. ~/.hermes/plugins/limbic/   # unless you symlinked it
hermes gateway restart                                                 # and restart the Desktop app
```

The database keeps the agent's whole life; back it up before big updates:
`sqlite3 /var/lib/limbic/limbic.db ".backup /var/lib/limbic/limbic.db.bak"`.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Nothing appears in the dashboard | Plugin not loaded: `hermes plugins list` (enabled?), right profile?, Hermes restarted? |
| `limbic: ... failed (agent runs without feelings)` in `~/.hermes/logs/` | Wrong `url`, server down, or firewall: `curl <url>/api/agents`. |
| `401` / the dashboard keeps asking for a token | The plugin's `token_file` must contain exactly the server's `LIMBIC_TOKEN`, and be readable by Hermes. |
| Every chat is "intense", or nonsense appraisals | No/weak appraiser model. Set `LIMBIC_LLM_*` (step 3), restart limbic. |
| `appraiser_error` on events | The model call failed (key, model name, quota, refusal). That event used the keyword fallback. |
| Each Telegram/Discord user is a stranger | Map ids in `senders` (step 6.3). |
| Images change nothing | No `auxiliary.vision` (native image mode), or your script is missing from `image_commands`. |
| It never writes first | Check `autonomy_enabled`, *may write on its own*, `routes`, `allow_gateway_injection`, quiet hours — dashboard → Autonomy shows why not. |
