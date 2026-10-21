# Liara Launchpad (سکوی لیارا)

An AI agent that takes a finished project and deploys it to [Liara](https://liara.ir): it reads the project, decides what Liara needs
(app platform, databases, disks, environment variables), shows a plan with evidence and a price, asks for approval, deploys to the developer's own Liara account,
checks that the app answers, and when something fails it diagnoses the cause and offers a fix (fixes that cost money are never applied without asking).

## What it does
- **Starts from a sentence:** "tell the agent what you want to put online" (the dashboard's main box, `/agent`): a ready-made app, a GitHub project, or something to build from a description. The agent finds, fetches or writes it, and the person approves each step on a card; the deployment then continues on its own run page.
- **Understands projects:** docker-compose files are decomposed into Liara apps and managed databases (Liara does not accept compose files); single-app projects are investigated by an LLM with read-only tools. Every claim cites a file and line.
- **Prices before it spends:** real plan prices from the Liara API (per hour and per month, including VAT), shown before anything is created.
- **Deploys and verifies:** creates databases and apps on one private network, wires generated credentials into the app, uploads the source, waits for the build, and checks the app answers over HTTP.
- **Self-heals:** classifies failures (build timeout, out of memory, wrong port, database login, missing dependency or variable, blocked foreign service) and applies a fix after approval. Code fixes go to a copy of the project, never the developer's files.
- **Diagnoses a running app:** "Check my running app" (and the chat's `diagnose_app`) probes the site, reads the runtime logs, releases, CPU and memory against the plan, and ranks the likely causes of an error or a slowdown with the evidence for each. A fix the person approves (roll back, resize, set a variable) can be applied from the panel, then the app is checked again. It needs no language model.
- **Guides the registrar steps:** for a custom domain it lists what only the person can do at the registrar, with IRNIC's panel for a `.ir` domain (steps taken from public tutorials) and Liara's nameservers filled in.
- **Releases new code:** edits made in the chat or a newer Git commit are released on the running app as a run of its own whose page follows the build line by line.
- **Checks readiness:** committed secrets (by location, never the value), debug mode, SQLite data loss, localhost binding, services unreachable from Iran.
- **Safe by construction:** dry run by default in the CLI; real runs pause at an approval request; the agent only deletes what it created itself; secret values never reach the model, the logs or the event stream.

## Quick start
```bash
virtualenv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # add LIARA_API_TOKEN, LIARA_TEAM_ID (if your account is a team) and LLM_API_KEY
.venv/bin/liara-agent whoami                      # read-only: teams, networks, apps
.venv/bin/liara-agent plans                       # read-only: live plans and prices
.venv/bin/liara-agent deploy ./my-project         # dry run: plan, cost, and the requests that would be sent
.venv/bin/liara-agent deploy ./my-project --execute   # real run (asks you to type the project name)
.venv/bin/liara-agent cleanup ./my-project        # delete what the agent created for that project
.venv/bin/liara-agent serve                       # web API on :8000 (see docs/api.md)
```
The LLM is any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`). Docker-compose projects need no LLM.

## Layout
- `src/liara_agent/detect/` project understanding (compose, Dockerfile, Procfile, fingerprints, readiness)
- `src/liara_agent/agent/`, `tools/` the LLM investigation loop and its sandboxed read-only tools
- `src/liara_agent/liara/` Liara catalog (generated from the CLI), live prices, guarded API client
- `src/liara_agent/deploy/` pipeline, executor, database variables; `heal/` diagnosis and fixes
- `src/liara_agent/events.py`, `interact.py` the event log and the question/approval channel shared by the CLI and the web API
- `src/liara_agent/web/` FastAPI app: accounts, encrypted tokens, safe uploads, runs, live streaming
- `docs/` API reference, design brief, message-code (translation) catalog, sample event streams

## Tests
```bash
.venv/bin/python -m pytest -q    # no network, no credits: Liara and the LLM are faked
```
Real-service behaviour was verified separately with throwaway apps (created and deleted by the agent): deploy, wrong-port repair, Postgres with generated credentials.

## Demo and tools
- `docs/demo-script.md`: an 8-minute script (deploy, an HTTP 500, diagnosis and fix, a release, a domain) with a checklist.
- `demo/payments-shop` + `scripts/demo_incident.py`: a tiny shop and a script that removes its payment key on the real app, so the 500 is real.
- `scripts/check_llm.py`: checks that a model endpoint (for example BoxAPI) is good enough for the agent: answers, streaming, tool calls and their round trip, Persian, and planning a real project.

## The web app
`liara-agent serve` starts the API and, if `frontend/dist` exists, serves the built frontend from the same origin.
```bash
cd frontend && npm ci && npm run build      # build the UI (self-hosted fonts, no CDN at runtime)
cd .. && .venv/bin/liara-agent serve        # http://127.0.0.1:8000
cd frontend && npm run try                  # demo with a stand-in for api.liara.ir: any token works, nothing touches Liara
```
Responses are gzip-compressed (the live event stream is not), and hashed build files are cached for a year.
Environment: `WEB_SECRET_KEY` (encrypts stored Liara tokens; otherwise a key file is created in `WEB_DATA_DIR`), `WEB_DATA_DIR`, `WEB_COOKIE_SECURE=true` behind HTTPS, `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`.
Docs: `docs/api.md` (API), `docs/events.d.ts` (event types), `docs/message-codes.md` (the translation contract).

## Running this app on Liara
`Dockerfile` and `liara.json` (docker platform, port 8000, a disk mounted at `/data`) are written but **not yet built or deployed**. Set `WEB_SECRET_KEY` and the LLM variables as environment variables on the app, and keep the `/data` disk, otherwise accounts and run history are lost on redeploy.

## Evaluation set
`evals/cases.py` has 13 realistic miniature projects (Express, Next.js with Prisma, Django, Flask, Laravel, Vite, Go, static, FastAPI with Celery, a monorepo, a Telegram bot, committed secrets, compose files), each with the answer a correct plan must contain.
`pytest tests/test_evals.py` scores the deterministic layer for free. `python scripts/run_evals.py` scores the real LLM agent on the same cases (needs `LLM_API_KEY`; about a dozen small runs) and prints a table plus token usage; results go to `evals/results/`.

## Known limits
Object-storage buckets, Liara email servers and a WordPress choice (managed product vs a PHP app plus a database) are built and tested against fakes, but have never run on the real service: the bucket's S3 endpoint and the key request shape are probed, not documented, and the email server's cheapest plan costs about 1.6M toman a month.
Not yet verified on the real service: disks, buckets, email servers.
Custom domains (own DNS or Liara DNS, with SSL) are built but untested against the real service; deployment rollback is not built. The Dockerfile is untested.

## Beyond the core (built, tested against fakes only)
Private repositories (access token, stored encrypted, sent as a header), branch previews (a branch becomes its own project), uptime checks (a background loop, default every 5 minutes, `WEB_MONITOR_INTERVAL_SECONDS=0` switches it off) and a Markdown report per run. None of these has been run against a real private repository or a real Liara app yet.

### Sign in with GitHub and connect GitHub (optional)
Register an OAuth app at github.com/settings/developers (Homepage: your server's address; Authorization callback URL: `https://<your server>/api/github/callback`), then set `WEB_GITHUB_CLIENT_ID` and `WEB_GITHUB_CLIENT_SECRET` as environment variables and restart. The login and register pages then offer "Continue with GitHub" (asks only who you are), and "New deployment" and Settings offer "Connect GitHub" (asks for the `repo` permission: pick private repositories without a token, open pull requests with the agent's fixes). With `WEB_REGISTRATION_CODE` set, a new account made through GitHub still needs the invite code (typed before pressing the button). Tested against a stand-in for GitHub, not GitHub itself.
