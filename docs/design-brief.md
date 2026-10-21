# Design brief: the Liara deploy agent (working name: TBD)

Status: draft written 2026-10-17, before the challenge description is published. Sections marked **[WAIT FOR SPEC]** should not be finalised yet.
Real sample data lives in `docs/samples/`. Backend behaviour described below exists in code today (the events listed in section 6 are all implemented and tested); anything speculative is marked **[WAIT FOR SPEC]**.

---

## 1. What this product is

A developer finishes a project but does not know how to deploy it. They give us the project (zip upload or public Git URL). An AI agent:
reads the project, decides what Liara needs (app platform, databases, disks, environment variables), shows a plan with evidence and a price,
asks for approval, creates the resources on the developer's own Liara account, deploys, checks that the app answers, and when something fails
it explains why in plain language and offers a fix (sometimes one that costs more, which it never applies without asking).

**Audience:** developers who can write code but have never deployed. Mostly Iranian, Persian-speaking, many reading English technical terms fine.
They are nervous about two things: breaking something and surprise bills. The UI's job is to remove both fears.

**Tone:** calm, plain, specific. Like a senior colleague sitting next to you. No jargon without a one-line explanation. Never cute.

## 2. Design principles

1. **Show your work.** Every decision (platform, database, port) displays the evidence behind it (`package.json` line 12, `docker-compose.yml` service `db`). Evidence is a first-class UI element, not a tooltip.
2. **No surprise spending.** Any step that creates or changes billable resources shows the price (per hour and per month, including 10% VAT, in toman) *before* the button. Price changes are shown as deltas ("+306 toman/hour").
3. **The user is always in control.** Nothing irreversible happens without an explicit approval card. Declining must be as easy as accepting.
4. **Make waiting feel alive.** Builds take a minute or more. The pipeline and logs must show real progress, not a spinner.
5. **Failure is a normal screen, not an error page.** A failed deploy shows: what happened, how sure we are, the evidence, and the next action.
6. **Secrets stay hidden.** Values are never shown after entry; variable *names* only. Anything secret-looking is masked by default (even values that appear inside the plan JSON, e.g. a database URL with a password).
7. **Persian first-class.** Full RTL layout, not a mirrored afterthought. Code, logs, file paths and URLs stay LTR inside RTL text.

## 3. Visual direction

- Dark theme by default (matches the Liara console, which is dark with a teal-to-green accent); light theme optional.
- Our own identity: do not copy Liara's logo or wordmark. Use a similar calm, technical feel with our own name and mark.
- Status colours: running (blue), success (green), warning (amber), failed (red), waiting-for-you (purple/teal, the "needs your input" colour must be distinct from success).
- Monospace for logs, paths, commands. A Persian typeface with good Latin pairing for UI text (Vazirmatn is a common open-source choice; unverified that it is the best fit).
- Numbers: show Persian digits in the Persian UI for prose, but **prices and technical values** (ports, versions) toggle with a setting; default to Latin digits for technical values to avoid copy/paste errors.

## 4. Pages

| Page | Purpose | Spec dependency |
|---|---|---|
| Landing | Explain in one screen: upload, review plan, live. One CTA. | **[WAIT FOR SPEC]** wording and emphasis |
| Register / Login | Username + password. Rate-limit and error states. | none |
| Dashboard | Projects list: name, last deployment status, live URL, quick actions. Empty state for first-timers. | none |
| New deployment | Upload zip or paste Git URL; choose project name; shows what we will and won't read (secrets are never uploaded). | none |
| **Deployment run page** | The centerpiece. See section 5. | none |
| Project page | Deployment history (versions), rollback, environment variables (names only, masked), attached databases/services, logs, links to the Liara console. | partly **[WAIT FOR SPEC]** |
| Settings | Connect Liara account (API token, team), language, theme. | none |

Navigation: simple top bar (logo, projects, settings, language toggle, theme toggle). The run page is reached from a project.

## 4a. Connecting a Liara account (first-run flow)

The user pastes a Liara API token (created at console.liara.ir, API page). We must teach this in the UI: a short numbered guide with a screenshot placeholder.
After saving we call the account and show: the team(s) found, the private network(s) found, existing app names (so they see we can see their account).
**If the account belongs to a team, a team selector is mandatory**; without it the account looks empty and name-clash checks silently fail (this was a real bug source).
State to design: token invalid, token valid but no team chosen, valid and ready.
Copy must say plainly: "We only ever modify apps and databases we created ourselves. Your existing apps are never touched."

## 5. The deployment run page

One page, one run. Layout on desktop: three regions.

```
+-------------------------------------------------------------+
|  Header: project name | status badge | cost badge | actions   |
+--------------------------+----------------------------------+
|  A. Pipeline graph       |  C. Agent panel (right/primary)  |
|  B. Architecture diagram |   - reasoning steps + evidence   |
|                          |   - questions & approval cards   |
+--------------------------+----------------------------------+
|  D. Tabs: Logs | Tests | Plan | Files changed | Costs        |
+-------------------------------------------------------------+
```
On mobile these stack: header, agent panel first (because approvals live there), then pipeline, diagram, tabs.

### 5.1 Pipeline graph (region A)

Nodes, left to right: **Analyze, Plan, Confirm, Provision, Deploy, Verify, Live**.
Node states: pending, running, waiting-for-you, ok, failed, skipped.
A self-heal retry is drawn as a visible loop from Verify (or Deploy) back to Deploy with an attempt counter ("attempt 2 of 3"), and a small "fix" node on the loop describing the change ("moved to silver tier").
Clicking a node filters the logs and agent panel to that step.
For multi-app plans, Provision/Deploy/Verify show one sub-node per app/database (e.g. `database:blog-db`, `app:blog-web`).

### 5.2 Architecture diagram (region B)

Built from the plan and updated live as the agent discovers things (nodes appear dashed while "proposed", solid once created).
Nodes: app(s), managed databases (Postgres, Redis...), disk, object storage, mail, custom domain, the internet.
Edges are labelled with the environment variable that connects two nodes (e.g. app -> database labelled `DATABASE_URL`).
Each node shows: type icon, name, plan/size and hourly price. The whole diagram has a total price chip.
Node states mirror the pipeline (proposed, creating, ready, failed).
Show the private network as a bounding box around apps and databases.

### 5.3 Agent panel (region C)

A vertical timeline of short steps, plain language, each with an evidence chip that opens the file and line.
Example: "I found the app starts with `node src/index.js` (package.json, line 7)".
Interactive cards inside the timeline:
- **Question card:** one question, answer options or free text, "I don't know, use a safe default".
- **Approval card:** what will happen, price delta, buttons Apply / Decline. Declining records why the plan stops.
- **Secret input card:** asks for a value for a named variable. Password-style field, never echoed back, "skip" allowed with a warning.
- **Diagnosis card (on failure):** title (human name of the problem), one-sentence summary, evidence lines (monospace), then fix options, each with price delta if any, and "Do it myself" for manual ones.
- **Patch card:** shows a unified diff of a proposed code change. Always states "applied to a copy of your project, never your files" and offers "Download patch".

### 5.4 Tabs (region D)

- **Logs:** build output streaming, then app logs. Filter by step. Search. Pause/auto-scroll. Copy. Error lines highlighted.
- **Tests:** list of checks with pass/fail, duration, detail. Today there is one real check (HTTP answers with status below 400); design for many (health endpoint, database connection, key pages).
- **Plan:** the full plan as a readable form (apps, databases, env var names with a secret badge, disks, notes, questions). Evidence next to each item.
- **Files changed:** every file the agent added or changed in the copy (Dockerfile, requirements.txt) as diffs, with download of the patch or fixed zip.
- **Costs:** table of each resource, its plan and tier, hourly and monthly price including VAT, total, and "what changed" when a fix altered it.

### 5.5 Result states of a run

- **Live:** big success state with the URL, a copy button, and the **7-day warning**: the free `<name>.liara.run` address stops working 7 days after the first deploy, so offer "Add a custom domain" prominently. Also: cost running now, "Delete everything this run created" (with a typed confirmation).
- **Failed, fixable:** diagnosis card is the hero.
- **Failed, not fixable by us:** e.g. the app calls a service that Liara's network cannot reach (Telegram, OpenAI). Say plainly it is a network restriction, not a bug, and list the options. Not a red error; an informational "needs a decision".
- **Blocked before starting:** preflight blockers (e.g. a service needing privileged mode). Lists blockers with how to fix.
- **Dry run:** a distinct banner "Nothing was created. This is a preview", with the exact list of requests that *would* be sent.
- **Cancelled by user:** shows what was already created and still billing, with a one-click cleanup.

## 6. Events that drive the UI

Everything the UI shows is a function of an append-only **event log**. The same log is printed by the CLI today and will be streamed to the browser
(SSE/WebSocket). A finished run can be replayed from the stored log, so a "run history" page costs nothing extra.

**Envelope** (every event): `{ "id": <int, increasing from 1>, "ts": <unix seconds>, "kind": "<kind>", "data": { ... } }`.
The payload is always under `data`; payloads have their own `id`/`kind` fields, which is why they are nested.
Reconnect with "give me events after id N". Unknown kinds must be ignored (forward compatible).
**Secret values never appear in any event.** Environment values are not even sent: plans expose variable names, whether a value is set, and `$DB_*` references.

**One run, not two.** A web run is always a real run that pauses at the `confirm_request` of kind `plan` before anything is created; the "preview" is that waiting state, and the approval card's Apply is the deploy button. Dry run exists only as the CLI mode and as the `would_send` list inside `plan_ready`.
**Tier names in the UI:** use `tier_label` (basic / silver / gold). The API ids (basic / standard / pro) never appear in the UI.

`step` identifies a pipeline node: `database:<id>` or `app:<name>`. `phase` is one of `analyze`, `plan`, `confirm`, `deploy`.

### Lifecycle
| kind | data | drives |
|---|---|---|
| `run_started` | `project`, `dry_run` | header, dry-run banner |
| `phase_started` / `phase_finished` | `phase`; finished adds: analyze -> `manifests`, `hints[]`, `env_var_names[]`; confirm -> `approved`; deploy -> `ok` | pipeline nodes Analyze, Plan, Confirm, and the Provision/Deploy/Verify group |
| `plan_ready` | `how`, `plan` (see section 7), `cost{lines[], totals}` (each line has `tier` and `tier_label`), `disks[]` {app, name, mount_to, price: null, billing: "unverified"}, `readiness{counts, findings[{code, level, file, line, params}]}` (pre-deploy checks: committed secrets by location only, debug left on, SQLite data loss, localhost binding, foreign services unreachable from Iran; see `docs/message-codes.md`), `would_send[]` {method, path, summary} (the exact requests a real run would make; computed without sending anything), `network_id`, `usage` (LLM calls/tokens or null) | Plan tab, architecture diagram, cost badge, "see the requests that would be sent" |
| `run_finished` | `ok`, `dry_run`, `created{apps[],databases[]}`, `urls{app:url}`, `would_send[]` (dry run), `stopped` (blocked / cancelled / no_plan / error), `failed_step`, `reason`, `blockers[]`, `diagnosis{code,summary,evidence[],fixes[]}` | final state screen |

### Agent investigation (single-app projects)
| kind | data | drives |
|---|---|---|
| `tool_call` | `name` (list_dir / read_file / grep / ask_user / submit_plan), `arguments` (truncated) | timeline line "reading `src/index.js`" |
| `agent_message` | `text` | timeline |
| `plan_updated` | `apps`, `databases` (counts) | "agent proposed a plan"; the full plan follows in `plan_ready` |
| `plan_rejected` | `reason` | low-prominence timeline note (the agent retries internally) |

### Deploy
| kind | data | drives |
|---|---|---|
| `step_started` / `step_finished` | `step`; finished adds `state` (e.g. READY), `dry_run` | node running / ok |
| `resource_created` | `kind` (database/app), `id`, `type` or `platform`, `plan`, `tier`, `tier_label`, `hourly_price` (toman incl. VAT), `dry_run` | diagram node turns solid, running cost total |
| `resource_updated` | `kind`, `id`, `plan`, `tier`, `tier_label`, `hourly_price` (the NEW price), `previous{plan,tier}` | node changes size/tier, cost badge updates (e.g. after a priced fix) |
| `build` / `build_error` | `step`, `line` | log stream |
| `warning` | `step`, `message` | amber notice |
| `test_result` | `step`, `name`, `ok`, `detail`, `url` | tests tab, Verify node, live URL |

### Self-heal
| kind | data | drives |
|---|---|---|
| `heal_attempt` | `step`, `attempt` | loop edge and counter ("attempt 2 of 3") |
| `diagnosis` | `code`, `params`, `summary` (English fallback), `evidence[]`, `fixes[]` as `{code, kind, text, params, cost_delta_hourly}`, `app` | diagnosis card |
| `fix_applied` / `fix_declined` | `fix` (text), `note`, `kind` (resize / set_port / set_env / code_patch / refresh_db_credentials), `params` (e.g. new plan and tier), `cost_delta_hourly`, `app` | timeline, loop edge |
| `patch_ready` | `file`, `diff` (unified diff) | patch card, Files-changed tab |


### 6b. Translation contract **(read this before writing any Persian)**
All backend-authored wording is a **code plus parameters**; the UI owns the text in each language. The full catalog, with every code and its parameters, is `docs/message-codes.md`.
- Where codes appear: plan `notes[]`, `questions[]` and `question` events (with `options` as stable ids to localize), `confirm_request` (`code`, `params`), `warning`, `diagnosis` (`code`, `params`, and `fixes[]` as `{code, kind, text, params, cost_delta_hourly}`), `fix_applied`/`fix_declined`, `run_finished.reason_code`.
- Every event also carries an English `message`/`text` fallback. Show it only when the UI has no translation for that code.
- Text written by the AI model itself (agent notes, agent questions, `agent_message`) has `code: null` and arrives in the user's language (setting `en` or `fa`).
- Identifiers stay as they are: file names, paths, variable names, ports, versions, service names, `tier_label` (basic / silver / gold).
- `run_finished.created` lists resource **names** (`apps[]`, `databases[]`), plus `database_ids[]` with Liara's internal ids (not for display).
- Questions can have `options` (ids such as `worker`, `needs_port`) rendered as choices; with no options, show a free-text field.

### Asking the user (request/response pairs; answered through the API, see below)
| kind | data | UI element |
|---|---|---|
| `question` -> `answered` | `id`, `text` (fallback), `code`, `params`, `options[]` (ids; empty = free text) -> `id`, `value` | question card |
| `confirm_request` -> `confirm_response` | `id`, `prompt` (fallback), `code`, `params`, `kind` (`plan` = create billable resources, `fix` = change a plan/port, `patch` = change code), `price_delta_hourly` (toman incl. VAT, may be null) -> `id`, `accepted` | approval card; `plan` kind is the big "create these resources" confirmation |
| `secret_request` -> `secret_provided` | `id`, `name` -> `id`, `name`, `provided` (bool, never the value) | secret input card (password field) |
| `request_expired` | `id`, `request` | a request timed out and was safely declined (no approval, no secret, conservative answer) |

**Answer channel (web):** the browser answers with `respond(request_id, value)`: `true/false` for confirms, text for questions, the secret text for secret requests.
The worker blocks until answered or the timeout (15 minutes by default). A timeout always means "no": nothing is approved and nothing is spent.
For the UI this means: a pending request is a card that must stay visible and actionable, survive page reloads (re-derive it from the log: a request without a matching response is open), and show a countdown or "expires" state.

Diagnosis codes (stable identifiers to map to friendly titles and icons):
`build_timeout`, `out_of_memory`, `missing_python_module`, `missing_node_module`, `missing_env`, `db_localhost`, `outbound_blocked`,
`wrong_port`, `bound_to_localhost`, `no_dockerfile`, `not_serving`, `unknown`.

## 7. Data shapes the UI renders

Real samples, produced by the pipeline itself (not hand-written):
- `docs/samples/plan_with_cost.json`: the `plan_ready` payload for a compose project (2 apps, Postgres, Redis), with per-line and total cost. Note environment values are absent by design.
- `docs/samples/events_waiting_for_approval.json`: a real run paused at the plan approval request (10 events). Nothing has been created at this point. This is the main "plan ready" screen.
- `docs/samples/events_dry_run_compose.json`: the CLI dry-run mode (25 events). Secondary; has no approval request.
- `docs/samples/events_build_timeout_then_fixed.json`: a full real-run stream (30 events): the agent plans a Node app with a Postgres database (and explains in a note why a Redis dependency did not become a Redis database), plan approved, secret requested, the build times out, diagnosis, priced fix approved (resource_updated carries the new tier and price), redeploy succeeds, HTTP check passes. The secret value the user typed does not appear anywhere in it.
Key fields:
- **Readiness:** the Plan tab should show `readiness.findings` grouped by level, each with its file:line and a localized explanation by `code`. The approval's `params.readiness_warnings` says how many warnings there are.
- **Plan:** `apps[]` {name, platform, port, role web|worker, env[] of {name, reference, has_value}, disks[], evidence[]}, `databases[]` {kind, id, version, evidence[]}, `env[]` {name, secret, source user|generated|database}, `questions[]`, `notes[]` {level info|warning|blocker, message, evidence[]}.
- **Cost line:** {kind app|database, name, plan, tier, hourly_incl_vat, monthly_incl_vat}. Money is in toman, 10% VAT already included in the `_incl_vat` fields. Monthly = hourly x 720.
- **Tier names:** the API uses basic / standard / pro; the console says bronze or basic / silver / gold. Use basic / silver / gold in the UI and explain once what each includes (build time limit 5/15/60 minutes, deploys per day, backups).
- **Evidence:** {file, line?, note}.

## 8. Copy: key messages (EN, with draft FA for native review)

Draft Persian below is a starting point only. It must be reviewed by a native speaker before shipping.

| Moment | English | فارسی (پیش‌نویس) |
|---|---|---|
| Plan ready | "Here's what I'll create. Nothing happens until you approve." | «این چیزهایی است که می‌سازم. تا تأیید نکنید هیچ اتفاقی نمی‌افتد.» |
| Price line | "About 4,583 toman/hour (3.3M/month) including VAT" | «حدود ۴٬۵۸۳ تومان در ساعت (۳٫۳ میلیون در ماه) با احتساب مالیات» |
| Approval | "Move to the silver tier? +306 toman/hour" | «ارتقا به بسته نقره‌ای؟ ۳۰۶ تومان در ساعت بیشتر» |
| Build timeout | "Your build ran past this tier's 5-minute limit. This is a plan limit, not a bug in your code." | «ساخت پروژه از محدودیت ۵ دقیقه‌ای این بسته بیشتر طول کشید. این مشکل از بسته است، نه از کد شما.» |
| Network restriction | "Your app can't reach api.telegram.org from Liara's network. Redeploying won't fix this." | «برنامه شما از شبکه لیارا به api.telegram.org دسترسی ندارد. استقرار دوباره مشکل را حل نمی‌کند.» |
| Expiry warning | "The free address stops working 7 days after your first deploy. Add your own domain." | «آدرس رایگان هفت روز پس از اولین استقرار از کار می‌افتد. دامنه خودتان را اضافه کنید.» |
| Safety promise | "We only change things we created. Your other apps are never touched." | «فقط چیزهایی را تغییر می‌دهیم که خودمان ساخته‌ایم. به برنامه‌های دیگر شما دست نمی‌زنیم.» |

## 9. States and edge cases to design

- Empty states: no projects, no logs yet, no tests yet, no questions.
- Long-running build (several minutes): queue position text ("waiting for the builder, N ahead"), elapsed timer.
- Many events fast (a build can emit hundreds of lines): virtualised log list.
- Plan with a **blocker** note (cannot proceed): disable Approve, explain.
- Plan with **questions**: Approve disabled until answered or "use defaults" chosen.
- Several apps (monorepo/compose): per-app sub-nodes and per-app tabs in Logs.
- Partial failure: database created, app failed. Show what exists and is billing; offer cleanup.
- Account problems: invalid token, no balance (Liara answers 402 "not enough balance"), name already taken (409).
- Disconnected stream: banner "reconnecting", replay from the last event id.
- A fix was declined: the run ends in a "waiting for you / stopped" state, resumable.
- Very long file paths, very long log lines: truncation with expand.
- Secret-looking values inside plan data: always masked, click-to-reveal is **not** offered.

## 10. Accessibility and responsiveness

- WCAG AA contrast on the dark theme; status must never rely on colour alone (icon + text).
- Fully keyboard-operable approval and question cards; focus moves to a new card when it appears; live region announcements for step changes.
- Graphs (pipeline, architecture) need a text/list alternative view.
- Works down to 360 px wide; the run page degrades to a single column.
- Respect reduced-motion (no continuous animations when set).

## 11. Out of scope for the first design pass

Billing management, team collaboration/roles, GitHub OAuth and webhooks (stretch), preview deployments per branch, uptime monitoring,
admin screens, a VM/server deployment flow (the agent deliberately avoids creating VMs: they cannot be deleted for 24 hours).

## 12. Open questions (answer before final visual design)

1. Challenge description and judging criteria **[WAIT FOR SPEC]**: which screens or numbers are scored?
2. Product name and logo.
3. Is the web app itself deployed on Liara (planned: "dogfooding")? That affects the auth and database choices, not the visuals.
4. Single-user login only, or will judges expect multi-user accounts and per-user isolation?
5. Persian-only, English-only, or both at launch? (Plan assumes both with RTL.)
6. Do we show live prices from the Liara API (accurate, needs a token) or the bundled snapshot (works offline, may be stale)? Today the CLI uses live prices when a token exists.

## 13. Deliverables expected from the design pass

1. Design tokens (colours, type scale, spacing, radii, elevation) for dark and light, LTR and RTL.
2. Component set: status badge, evidence chip, cost chip, pipeline node, architecture node and edge, timeline step, question card, approval card, secret input card, diagnosis card, patch/diff card, log viewer, test row, empty states.
3. Screens: landing, register, login, dashboard, new deployment, run page in each state of section 5.5, project page, settings (including the token connection flow).
4. A clickable prototype of one happy path and one failure-and-fix path (use the sample event stream).
5. Responsive variants of the run page (desktop, tablet, phone) and a Persian (RTL) version of it.
