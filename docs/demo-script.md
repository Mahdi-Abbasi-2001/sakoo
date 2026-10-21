# Demo script: from zip to a fixed 500

About 8 minutes. Every scene uses something that exists and was run; the slow parts (a long build) are marked so they can be cut or sped up.

## Before recording (the checklist)

- The model is reachable: from Iran OpenAI answers 403, so start the server with the proxy, or use BoxAPI (see `scripts/check_llm.py`). The project page's "Check my running app" works without a model; the chat does not.
- Server: `HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808 NO_PROXY=localhost,127.0.0.1,.liara.ir,.liara.run WEB_DATA_DIR=.web-data .venv/bin/python -m uvicorn --factory liara_agent.web.app:create_app --host 127.0.0.1 --port 8000`
- Liara: a token connected in Settings, Persian selected, no leftover apps named `payments-shop*`, and the daily deployment count not used up (basic tier: 20 a day; a rejected release is HTTP 428).
- Make `demo/payments-shop` a zip (`cd demo && zip -r payments-shop.zip payments-shop`).
- The one real cost: the app on `small-g2` is about 1,146 toman/hour with VAT. Delete the project at the end ("Delete project" removes everything its runs made).

## Scene 1: first deploy (about 2 minutes)

1. Either say it (dashboard, "Tell the agent what you want to deploy": *"یه سرور n8n برای اتوماسیون‌هام می‌خوام"*: a single approval card, then the run page) or New project, upload `payments-shop.zip`.
2. Start a run. The agent reads the project (Dockerfile, port 3000), proposes one app, the plan and the price, and a private network: *"nothing is created before you approve"*.
3. Approve. Watch the pipeline and the build log. When the shop answers, the live address appears in the result popup.
   - If the plan asks for `PAYMENT_API_KEY`, type any value in the private box: it goes to Liara and never to the model.
   - If it does not, the shop answers 500 straight away and the agent's own repair loop finds the missing variable: that is Scene 3's story told early; either is fine.

## Scene 2: the incident (about 30 seconds)

In a terminal: `python scripts/demo_incident.py break payments-shop-xxxx` (the app name is on the project page). It removes the variable, as an edit in the console would. Reload the shop address: it answers **500**.

If an alert webhook is set (Settings), after two failed checks it receives a message that already says why ("Most likely: an environment variable is missing (PAYMENT_API_KEY). Evidence: RuntimeError: PAYMENT_API_KEY is not set"), and the project page shows the diagnosis marked "found automatically" before anyone presses a button.

## Scene 3: find out why, fix it, check (about 2 minutes)

1. Project page, Overview: **Check my running app**. In a few seconds: state "Down", `503/500`, and the ranked causes. The top one is *"An environment variable is missing"* with the log line `RuntimeError: PAYMENT_API_KEY is not set` as the evidence. No model is involved here.
2. Press **Apply this** next to "Set the missing variable". The approval card opens with a private box for the value. Approve.
3. *"Done. Checking the result in a moment…"* After 20 seconds the panel checks again: **The app is healthy again.**
4. Repeat the same through the chat (run page, Persian): *"سایتم خطای ۵۰۰ می‌ده، ببین مشکل از کجاست"*. The assistant calls the diagnosis, explains the cause in words with the log line, and proposes the same card.

## Scene 4: ship a change (about 2 minutes)

On a run page, in the chat: *"یه صفحه‌ی /about اضافه کن که اسم فروشگاه رو نشون بده"*. The assistant reads `app.py`, proposes an edit as a diff card; approve; then *"دیپلوی کن"*: the page jumps to the release run and follows the build live; when it is live, open `/about`. (Edits live on the server's copy, a Git repository is not touched.)

## Scene 5: watching and the domain (about 1 minute)

- Overview: uptime strip and CPU/memory charts of the running app.
- Settings, Your domain: connect `myshop.ir`; the card lists the steps at IRNIC's panel (nic.ir, "دامنه‌های من", the DNS part, the nameserver rows, the security answer, "اعمال") with Liara's nameservers filled in. The assistant cannot log in there, so it says what only the person can do.

## Cleanup

`Delete project` (type the name). It removes the app, its disk and its private-network membership; networks themselves cannot be deleted through Liara's API, so remove empty ones in the console.

## If something goes wrong on camera

- The model is unreachable: the chat says so and the panel keeps working; the planner falls back to rules for a simple project.
- A slow first start: the run shows "still starting" and waits up to 6 more minutes by itself.
- A 428 on a release: the daily deployment count is used up; wait for the reset or use another app name.
