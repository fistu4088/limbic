# Installation guide — Linux and Windows

`limbic` is a standalone, framework-independent emotion engine for LLM agents. The core is Python 3.10+ and Python's standard library; it has no runtime dependencies and does not require Hermes Agent, a particular model provider, or a GPU. Integrations live in optional `contrib/` adapters. You can use the Python library directly or run the HTTP API and call it from any language/framework.

This guide takes you from a clean machine to a running dashboard and API. It covers Linux and native Windows PowerShell. The Windows commands are documented but have not been verified on a Windows host; the project is currently tested on Linux. WSL users can follow the Linux instructions inside WSL.

## What you need

- Python 3.10 or newer (`python --version` / `python3 --version`)
- Git
- For meaningful appraisals: an OpenAI-compatible chat-completions endpoint, either hosted or local. The API endpoint is optional for a basic demo; without it, limbic uses its simple keyword fallback.
- No `requirements.txt`: there are no third-party runtime packages to install.

## 1. Download and verify

### Linux

```bash
git clone https://github.com/fistu4088/limbic.git
cd limbic
python3 --version
python3 -m unittest discover -s tests
```

The last command is optional; it should finish with `OK`.

### Windows (PowerShell)

Install Git for Windows and Python 3.10+ first. During Python setup, enable the option to add Python to PATH. Open a new PowerShell window and check:

```powershell
git --version
py -3 --version
```

Download and test:

```powershell
git clone https://github.com/fistu4088/limbic.git
Set-Location .\limbic
py -3 -m unittest discover -s tests
```

If `py` is not available, install Python from python.org, enable PATH, reopen PowerShell, and use `python` instead of `py -3` below.

## 2. Run a demo first

The demo writes to a disposable SQLite database. It does not connect to an LLM or change any external agent.

### Linux

```bash
python3 -m limbic demo --db ./demo.db --days 7
python3 -m limbic serve --db ./demo.db --host 127.0.0.1 --port 8770
```

### Windows (PowerShell)

```powershell
py -3 -m limbic demo --db .\demo.db --days 7
py -3 -m limbic serve --db .\demo.db --host 127.0.0.1 --port 8770
```

Leave that terminal open and visit <http://127.0.0.1:8770>. The dashboard should load. Stop the server with **Ctrl+C** before continuing. Keep the bind address at `127.0.0.1` unless you have deliberately configured a private network and firewall.

## 3. Configure an appraiser model (optional, recommended)

The main agent's own LLM generates its replies. limbic's appraiser separately turns messages/events into structured emotional appraisals. Configure an OpenAI-compatible `/chat/completions` base URL, model name and API key. Hosted services send appraisal text to their provider; check that provider's privacy and pricing terms. A local compatible endpoint keeps appraisal requests on your machine.

Examples:

| Provider | `LIMBIC_LLM_URL` | Example model |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| OpenRouter | `https://openrouter.ai/api/v1` | provider's model identifier |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-flash-lite-latest` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Ollama | `http://127.0.0.1:11434/v1` | model name installed in Ollama |
| llama.cpp / vLLM | `http://127.0.0.1:8080/v1` | model name loaded by the server |

### Linux shell session

Set these in the terminal that will launch limbic. Replace the example values; never commit a real key or paste it into an issue.

```bash
export LIMBIC_LLM_URL='https://api.openai.com/v1'
export LIMBIC_LLM_MODEL='gpt-4o-mini'
read -rsp 'Appraiser API key: ' LIMBIC_LLM_KEY; echo
export LIMBIC_LLM_KEY
```

For a local server, use its URL/model and set `LIMBIC_LLM_KEY=''` if it does not require a key.

### Windows PowerShell session

These values last only in this PowerShell window and child processes started from it. Replace the endpoint/model with your provider's values. The secure prompt avoids putting the key directly in command history.

```powershell
$env:LIMBIC_LLM_URL = 'https://api.openai.com/v1'
$env:LIMBIC_LLM_MODEL = 'gpt-4o-mini'
$secureKey = Read-Host 'Appraiser API key' -AsSecureString
$env:LIMBIC_LLM_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
```

For a local server, use its URL/model. If it requires no key, set `$env:LIMBIC_LLM_KEY = ''`.

## 4. Start a persistent limbic agent

Do not use the demo database for a real agent. Choose a durable database path and keep it backed up. On first run, the server creates the agent when the client posts it.

### Linux: run in the foreground

From the repository directory, in the same shell where you set model variables:

```bash
mkdir -p data
umask 077
python3 -c 'import secrets; print(secrets.token_hex(32))' > ./data/limbic.token
export LIMBIC_TOKEN="$(cat ./data/limbic.token)"
python3 -m limbic serve --db ./data/limbic.db --host 127.0.0.1 --port 8770
```

Keep the terminal/session running. For a permanent server, use a service manager such as systemd and store `LIMBIC_TOKEN` and the optional `LIMBIC_LLM_*` variables in a root-readable environment file. See [`contrib/systemd/limbic.service`](../contrib/systemd/limbic.service) for the example unit; adjust its repository path, Python executable and database path to your installation. Do not expose the unauthenticated read API to the public internet.

### Windows: run in PowerShell

In the same PowerShell window where you configured the model, create a random write token and start the server:

```powershell
New-Item -ItemType Directory -Force .\data | Out-Null
$env:LIMBIC_TOKEN = py -3 -c "import secrets; print(secrets.token_hex(32))"
Set-Content -NoNewline -Path .\data\limbic.token -Value $env:LIMBIC_TOKEN
py -3 -m limbic serve --db .\data\limbic.db --host 127.0.0.1 --port 8770
```

Keep this window open; closing it stops the server and clears its session environment. For unattended Windows use, run it under a dedicated Windows service manager and configure its environment securely. Do not store API keys or the write token in source files or put the server on a public interface.

## 5. Create an agent and verify the API

In the second terminal, use the token file created in step 4:

### Linux

```bash
cd limbic
export LIMBIC_TOKEN="$(cat ./data/limbic.token)"
curl -fsS -X POST http://127.0.0.1:8770/api/agents \
  -H "Authorization: Bearer $LIMBIC_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"aria","temperament":{"name":"Aria","likes":["science fiction","horror"],"dislikes":["rudeness"]}}'
curl -fsS http://127.0.0.1:8770/api/agents
```

### Windows (PowerShell)

The server and token file are in the same repository folder; open a second PowerShell window there:

```powershell
$env:LIMBIC_TOKEN = Get-Content -Raw .\data\limbic.token
$headers = @{ Authorization = "Bearer $env:LIMBIC_TOKEN" }
$body = @{ id = 'aria'; temperament = @{ name = 'Aria'; likes = @('science fiction', 'horror'); dislikes = @('rudeness') } } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8770/api/agents' -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8770/api/agents'
```

The API response should list `aria`. Open the dashboard and select that agent. Keep the token private; all write requests require it when configured.

## 6. Connect any agent framework

The framework-independent core has two ordinary integration points:

1. **Before the model call:** request the agent's state/prompt and add it to the model context.
2. **After the turn:** submit the user message and speaker for appraisal. Periodically call consolidation (for example, once per day).

Read [`INTEGRATION.md`](INTEGRATION.md) for direct Python, HTTP API, framework-hook and autonomy examples. The repository's `contrib/` directory contains optional adapters; you do not need to install or run them unless using that integration. In particular, Hermes Agent support is isolated under `contrib/hermes/` and documented separately in [`HERMES.md`](HERMES.md).

## 7. Optional Python package installation

Running `python -m limbic` from the cloned repository needs no installation. If you want the `limbic` command, install the local project into a virtual environment. This may require downloading the build backend (`setuptools`) during installation; it is a build-time dependency, not a runtime dependency.

### Linux

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
limbic --version
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
limbic --version
```

If PowerShell blocks activation, you can avoid changing execution policy and invoke the venv interpreter directly: `\.venv\Scripts\python.exe -m pip install .`, then `\.venv\Scripts\python.exe -m limbic --version`.

## Troubleshooting

- **`python` / `py` not found:** install Python 3.10+ and reopen the terminal; on Windows, enable Python PATH or use the `py -3` launcher.
- **Address already in use:** change `--port` consistently in the server command and API URL.
- **401 Unauthorized:** the client token must exactly match the server's `LIMBIC_TOKEN`.
- **No appraiser configured:** the service still runs using the keyword fallback; check the `LIMBIC_LLM_*` environment in the server process.
- **Dashboard works but no agent data:** create an agent through the authenticated API and select it in the dashboard.
- **Remote framework cannot connect:** bind only to a private interface, restrict access with a firewall/VPN, and configure TLS or a trusted reverse proxy for traffic outside loopback. Never expose the write API token over plain public HTTP.
