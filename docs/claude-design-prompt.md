# What to say in Claude Design

## The design session has access to this project folder, so no attachments are needed
Give it these rules at the very start of Message 1 (they are included below). Also exclude `.env` and `.agent-state/` in the tool's settings if it lets you:
`.env` holds a live Liara token that can create and delete billable resources.

## Message 1 (paste this)

You have access to my project folder `liara-agent/`. This is a design task only.
- Read: `docs/design-brief.md`, everything in `docs/samples/`, and for reference `src/liara_agent/events.py` (event kinds) and `src/liara_agent/plan/model.py` (plan fields).
- Do NOT read `.env` or anything inside `.agent-state/`; they hold private credentials.
- Do NOT edit anything under `src/`, `tests/` or `docs/`. Put all design output in a new folder `design/`.

I'm designing a web app for a hackathon. Name for now: "Deploy Agent" (placeholder, tell me if you have better ideas).

**What it is:** a developer finishes a project but doesn't know how to deploy it. They upload it (zip or Git URL). An AI agent reads the project, decides what the Liara cloud needs (app, databases, disks, environment variables), shows a plan with evidence and a price, asks for approval, deploys it to the developer's own Liara account, checks it works, and if something fails it explains why in plain language and offers a fix (some fixes cost more money and are never applied without asking).

**Audience:** developers who can code but have never deployed. Mostly Iranian. They are nervous about breaking things and about surprise bills. The UI's job is to remove both fears. Tone: calm, plain, specific, like a senior colleague next to you.

**Please read `docs/design-brief.md` fully.** It defines the pages, the run page layout, every state, the event stream that drives the UI, and the rules (show evidence for every decision, always show price before spending, never show secret values, Persian and English with full RTL).

**Important:** the three JSON files in `docs/samples/` are real output from the backend. Please design with exactly these fields and event kinds, not invented ones. The UI is a function of the event log: events have the envelope {id, ts, kind, data}.

**Visual direction:** dark theme first (light optional), calm and technical, our own identity (do not copy Liara's logo or wordmark), teal/green accent is fine. Status colours: running blue, success green, warning amber, failed red, "waiting for you" must look clearly different from success. Monospace for logs and paths. Persian typeface with good Latin pairing.

**Start with only this, and nothing else yet:**
1. A small design system: colour tokens (dark and light), type scale, spacing, and the core components: status badge, evidence chip, cost chip, pipeline node, architecture node and edge, timeline step, approval card, question card, secret input card, diagnosis card, diff card, log viewer, test row.
2. The **deployment run page** for the happy path, on desktop: header with project, status and cost; pipeline graph (Analyze, Plan, Confirm, Provision, Deploy, Verify, Live); live architecture diagram (app, database, disk, private network, with env-var labels on edges and a price per node); agent timeline with evidence chips; tabs for Logs, Tests, Plan, Files changed, Costs.

Use the dry-run sample as the content for the first screens. Ask me questions if anything in the brief is ambiguous before you start.

## Follow-up messages, one at a time, in this order

**2. Failure and fix** (use `events_build_timeout_then_fixed.json`)
Now the same run page when the build times out: the diagnosis card, the priced approval card ("+306 toman/hour, apply?"), the retry loop on the pipeline with an "attempt 2 of 3" counter, and the success state with the live URL and the 7-day warning about the free address. Show the secret input card too (the user is asked for JWT_SECRET before deploy; the value must never be displayed after entry).

**3. Plan review and approval**
The plan review step before anything is created: everything in plan_ready, with evidence next to every item, the notes and questions, the cost table with per-line and total prices (including VAT), and the big "Create these resources" confirmation. Also the blocked state (cannot proceed) and the declined state.

**4. Other failure and result states**
From section 5.5 of the brief: failure we cannot fix (a foreign service blocked from Iran, shown as "needs a decision", not as a red error), preflight blockers, dry-run banner ("nothing was created"), cancelled with cleanup of what was already created.

**5. The rest of the app**
Landing page (keep copy short; I'll refine it later), register and login, dashboard with an empty state, new deployment (upload zip or Git URL), project page with deployment history, and settings including the "connect your Liara account" flow with the mandatory team selector.

**6. Persian (RTL)**
Convert the run page and the plan review to Persian, full RTL. Code, logs, paths and URLs stay left-to-right inside RTL text. Use the draft Persian strings from section 8 of the brief; I will have them reviewed.

**7. Responsive**
Tablet and phone versions of the run page (single column; approvals first).

**8. Prototype**
A clickable prototype of two paths: the happy path, and the failure-and-fix path.

## Notes for you (not part of the prompt)
- Do not paste the challenge description in yet; the landing page wording should wait for it.
- If Claude Design asks for a product name or logo, a placeholder is fine.
- Check every Persian string with a native reader before it ships.
- When screens are ready, export them (or share links) and I can build the frontend against the real API.

---

# Round 4: landing page, then build the frontend

## Message A: the landing page (paste this first)

Please design the **landing page** now. The final wording will change once I have the official challenge description, so use sensible placeholder copy, keep the structure easy to edit, and mark every claim you are unsure about as `[placeholder]`.

Content, in this order: (1) a hero with a one-line promise and one call to action ("Deploy my project"), plus a quiet secondary link; (2) three steps: upload or paste a Git link, review the plan and its price, approve and watch it go live; (3) a short "what it checks for you" list from the readiness findings in `docs/message-codes.md` (secrets by location only, debug mode, SQLite data loss, localhost binding, services blocked from Iran); (4) a "how we keep you safe" block: we only change what we created, you see the price before anything is created, your secrets never reach the AI or the logs, you approve every fix that costs money; (5) a small live-looking sample of the run page (reuse the real run page component, not a screenshot); (6) footer with language toggle.

Rules: no invented statistics, no testimonials, no fake logos, do not use Liara's logo. Persian (RTL) and English versions, dark and light. Desktop 1440, tablet 768, phone 390. Reuse the existing tokens and components.

## Message B: can you build it? (paste this second, before asking for code)

Before building anything: can you write files into my project folder? Please test by creating `frontend/README.md` with one line, and tell me yes or no. If you cannot write files, say so and stop; do not paste code into chat. If you can, also tell me whether you can run commands (node, npm), because that decides how we test.

## Message C: build the frontend (only after B says yes)

Please build the real frontend from the design. **Location: `liara-agent/frontend/`** (inside the liara-agent project, next to `src/` and `docs/`). Your earlier test file went to the parent folder (`liara/frontend/README.md`): please delete that one-line file and its folder, and work only in `liara-agent/frontend/`. All paths below are relative to `liara-agent/`. Rules:

**Scope.** Write only under `liara-agent/frontend/`. Do not edit anything under `src/`, `tests/`, `docs/` or `scripts/`. Never read `.env`, `.web-data/` or `.agent-state/`. If you need the backend to change, write the request into `liara-agent/frontend/BACKEND_REQUESTS.md` instead of working around it.

**Stack.** Vite + React + TypeScript, react-router, plain CSS using CSS variables for the design tokens (dark and light), no UI framework. The build output goes to `liara-agent/frontend/dist`; the API server already serves that folder at `/` (same origin, so the session cookie and CSRF just work). In development, proxy `/api` to `http://127.0.0.1:8000`.

**Contract (do not invent anything).**
- API: `docs/openapi.json` and `docs/api.md`. Send `credentials: "include"` and the header `X-Requested-With: XMLHttpRequest` on every POST/PUT/DELETE.
- Events: `docs/events.d.ts` has TypeScript types for every event. A run is a list of events `{id, ts, kind, data}`. Use `EventSource` on `/api/runs/{id}/stream` (the browser resends `Last-Event-ID` when it reconnects). Ignore unknown event kinds.
- Real data to develop and test against: `docs/samples/*.json`.
- Wording: every backend message is a code plus params. Build i18n catalogs `frontend/src/i18n/en.json` and `fa.json` from the catalog board in the design and `docs/message-codes.md`, and fall back to the English `message`/`text` only when a code has no translation. Persian is a draft that I will have reviewed, so keep the catalogs in plain JSON files.

**Architecture that matters.**
1. One pure function `reduceRun(events) -> RunViewState` that turns the event list into everything the run page shows (pipeline node states, architecture nodes with prices, timeline, logs, tests, open requests, cost, final result). Every screen reads that state; no screen parses events itself. It must work for a live stream and for a replay.
2. Open requests (question / confirm / secret) are derived from the events: a request without a response is open. They must survive a page reload.
3. A secret typed by the user is sent once and never stored in React state after sending, never put in the URL, never logged.
4. Approval buttons Apply and Decline both work; declining must never look like an error.
5. A demo mode at `/demo/waiting`, `/demo/failed`, `/demo/dryrun` replays `docs/samples` through the same reducer with no backend, so the design can be reviewed without Liara.

**Fonts and assets.** Self-host every font file (Sora, Vazirmatn, JetBrains Mono, or whatever the final design uses) in the build, and load nothing from a CDN at runtime: the users are in Iran and third-party font hosts may be slow or unreachable. Use `font-display: swap` and subset the Persian and Latin ranges. Use CSS variables for one token set with explicit surface roles (glass, panel, terminal). Keep animations to CSS and SVG, and respect `prefers-reduced-motion`.

**Run and look at your own work.** After each stage run `npm install`, the tests, `npm run build`, and open the running app in the built-in browser at 1440x900, 768x1024 and 390x844. Measure, do not guess: the approval card's Apply button must be visible on load at 1440x900 on the run page, no horizontal overflow, every control at least 44px on phone, no console errors, and Persian pages must be `dir=rtl`. Show me the numbers. Do not install anything globally, do not use sudo, and do not run `git init`.

**Tests.** Vitest for the reducer (use the three sample streams: assert pipeline states, cost totals, open requests, the `resource_updated` price change, and that no secret value appears in the state), and for the i18n lookup (every code used in the samples has an English entry). Run them and show me the output.

**Order of work** (stop after each step and show me what you built and what you verified):
1. Scaffold, API client from the OpenAPI file, auth pages, connect-Liara flow with the team selector.
2. `reduceRun` and its tests, then the run page wired to the live stream, plus the demo routes.
3. Dashboard, new deployment, project page.
4. i18n and RTL for everything, language toggle.
5. Landing page, built to the landing and motion spec from Round 8 (message H).
6. Responsive layouts and accessibility pass (keyboard, focus, reduced motion).

Be honest about what you could not run or check.

---

# Round 5: a richer visual direction (the first direction was too minimal)

## Message D (paste this)

The current design is too simplistic and minimal for what I want. That is partly because the brief asked for "calm and plain", so please treat this message as **replacing the visual direction in the brief** (the product rules, event contract, states and accessibility rules all stay). I want something rich, memorable and clearly crafted, not a clean admin template.

**Direction: "Persian-futurist command center".** One coherent idea with four layers, in this order of priority:

1. **Structure: a rich developer command center.** Layered surfaces with depth (not flat cards), dense but ordered information, an animated pipeline where work visibly flows along the edges, an architecture diagram that feels alive (glowing connections, small data pulses, resources that "power on" when created), terminal-style panels with real character, small details everywhere (micro-labels, status glints, live counters).
2. **Skin: Persian character.** A palette and ornament that could only come from here: think turquoise (firouzeh), lapis and deep indigo, with saffron or gold accents; geometric patterns derived from Iranian tilework, used as subtle grid lines, section dividers and glows rather than as clip-art; calligraphic-inspired display lettering for headlines. Please verify that any Persian display font really has the letters پ چ ژ گ ک ی before choosing it.
3. **The agent has a presence.** A visible AI agent element with states (reading the project, thinking, waiting for you, working, done, failed) that animates and ties the whole run page together. It must stay legible: states are also words and shapes, not only light.
4. **Bold, expressive moments** on the landing page and in empty states: very large confident typography, strong colour, a hero that is an actual animated demonstration of a run (using the real run page component), not an illustration of one.

**What must not change.** Real data only (the sample event streams). The approval card stays the most prominent element of a waiting run: richness goes into surfaces, motion and detail, never into competing with the primary action. Status never relies on colour alone, text contrast stays WCAG AA, and all animation respects reduced-motion and is cheap (CSS and SVG, no heavy libraries). Logs must stay easy to read. RTL must be correct, not mirrored by accident.

**Colour check.** The earlier palette was measured so that brand, success, waiting and running colours are distinguishable. Please redo that check with the new palette (including simulated red-green colour blindness) and report the numbers.

**Do not restyle everything yet.** First produce **three different style explorations of one screen**: the run page "waiting for your approval" (use events_waiting_for_approval.json), dark theme, 1440 wide, in Persian RTL. Make the three genuinely different from each other (for example: one leaning futuristic and glowing, one leaning ornamental and warm, one leaning dense and technical with a bold hero header), each combining the four layers above in its own way. Name them, write two lines on what each is going for, and tell me which you would pick and why. I will choose one, and only then do we apply it to the landing page, the run page in all states, the dashboard and the account pages, in both languages.

---

# Round 6: modern and futuristic, not traditional

## Message E (paste this)

Thank you, the explorations are colourful and alive, which is what I wanted. But I need to correct the brief: I do NOT want a traditional Persian look. This is the AI agent of liara.ir, a modern cloud platform, so it should feel futuristic, modern and native to that world. "Persian" only means Persian-language support and correct RTL, not heritage style. Please remove all traditional references: tilework and star motifs, the mihrab arch, calligraphic or Kufic display fonts, gold dividers, lapis and indigo as the brand identity.

Keep from "Night Lapis" everything structural, because that part worked: the layered surfaces with depth, the pipeline with work flowing along its edges, the living architecture diagram (pulses, resources that power on), the agent element with its six labelled states, the approval card as the loudest object, the status colour measurements, and the accessibility rules.

New skin:
- A near-black graphite or deep-space base.
- Liara's own colour family as the dominant accent: a teal-to-green gradient, like the dark Liara console. Stay in that family but do not use or imitate Liara's logo or wordmark.
- Modern futuristic touches: glass panels, soft glow, gradient meshes, a subtle grid, animated data flow, depth from light rather than from ornament.
- A contemporary geometric sans for Latin text and headlines, and Vazirmatn at heavy weights for Persian. No decorative or calligraphic display faces. Check that any font you use for Persian has پ چ ژ گ ک ی.

Please produce three skins of the SAME layout (use Night Lapis's layout as the base, same screen: Persian RTL run page waiting for approval, dark, 1440):
1. "Aurora": near-black with teal-to-green aurora gradients, glass surfaces, soft glow.
2. "Electric": high-contrast graphite with a sharp electric accent, a visible grid, crisp lines and a more technical feel.
3. "Liara-native": the closest to the real Liara console in colour and mood, but richer, with depth and motion.

Redo the colour-distance and contrast check for the new palettes, as before. Brand, success, waiting, running, warning and failed must stay distinguishable (including red-green colour blindness), so if the new brand colour sits too close to the success colour, move one of them and tell me. Name each skin, say in two lines what it is going for, and tell me which one you would pick. Then I will choose and we apply it everywhere.


---

# Round 7: decision

## Message F (paste this)

Decision: one design language. Use Aurora's mood everywhere (glow, teal-to-green gradients, lit backgrounds, depth). Use opaque panels for everything dense: logs, tables, plan and cost lists, forms, and all phone layouts. Use glass only for the shell and hero surfaces (header, the approval card, landing sections), over a darkened aurora so contrast stays at least 4.5:1. Build ONE token set with explicit surface roles (glass, panel, terminal), not two skins. Keep Sora for Latin and Vazirmatn for Persian. Please apply it to: the landing page (English and Persian, 1440, 768, 390), the run page in all states (waiting for approval, build failed and fix offered, live, plus tablet and phone), the dashboard, new deployment, project page and connect-account page, in both languages. Reuse the real event data. Rules: no traditional motifs, status never relies on colour alone, reduced motion respected, CSS and SVG only. At the end, give me the final token list (colours, type, spacing, radii, surface roles) in one place.


---

# Round 8: the landing page is a front door, not a sales page; add a motion system

## Message H (paste this before building)

Two adjustments before we build.

**1. The landing page reads like a product being sold. It should not.** This is the deploy agent built for Liara: the front door of a Liara service, not marketing. Rewrite its role and voice:
- The tool is the hero. Put a drop zone for a zip and a Git URL field at the top ("Deploy to Liara"), next to a living run. If the user is not signed in, the control says "Connect your Liara account to continue" instead of a sales button.
- Replace promises with facts, in a direct first-party voice: what it creates on Liara (apps, databases, disks, with real plan names and prices), what it understands (compose files, Dockerfiles, Node, Python and so on), what it checks (the readiness findings), and what it will never do (the safety rules, stated as facts).
- Remove: the hype headline, benefit cards, the closing call-to-action band, any "get started free" wording, and the footer line "an independent tool, not made by Liara".
- Still no invented statistics, testimonials or logos, and no Liara logo or wordmark. Keep `[placeholder]` on anything unconfirmed.

**2. Wow factor, and not generic AI-looking.** I want the pages to be animated and to have a signature idea that is specific to this product. Please design around this concept: **"the blueprint builds itself."**
- The hero replays a real run from `docs/samples` at its real timing: files stream into the agent orb; the project resolves into a thin-line architecture drawing with dimension marks and callouts, like an engineering schematic; prices tick up as the dimensions; the approval is the only element that pulses hard; then the dashed "proposed" resources light up solid and the live URL appears.
- The same run advances as the visitor scrolls the landing page (scroll-linked, with a fallback where scroll-driven animation is unsupported).
- The faint background grid is the Liara private network: cells light up as resources are created. A subtle cursor spotlight follows the pointer on desktop.
- The agent orb has a distinct animation per state: reading (particles drawn in), thinking, waiting (slow breathing), working, done (a single burst), failed.
- Typography with character: large mono numerals that tick, Persian and Latin sharing baselines properly, micro-annotations in mono.
- The product pages get motion too: page transitions, flowing pipeline edges, number tickers, a success moment when a deploy goes live, skeleton shimmers instead of spinners, depth on hover.

Avoid what makes pages look generic: floating glass cards with nothing in them, purple blob gradients, stock illustrations, emoji, symmetrical three-column feature cards, and words like "supercharge", "unlock" or "seamless". Prefer asymmetric layouts, annotations, and real data as decoration.

**Rules for all motion.** Every animation communicates state, never just decoration. Never move or resize the Apply and Decline buttons, never cause layout shift, never animate log text. Use CSS, SVG and the Web Animations API only (no heavy libraries) and keep it smooth. `prefers-reduced-motion` gets a static version that still tells the whole story. Pause animation when the tab is hidden. Contrast must stay at least 4.5:1 at every animation state. RTL must be correct in every animation (direction of flow, reading order).

**Deliverables now (before the build).** (a) A motion spec board: what moves, trigger, duration, easing, and the reduced-motion fallback for each item. (b) The revised landing page, English and Persian, at 1440 and 390, with the hero sequence working as a looping animation that I can watch in a browser. (c) Render and check it yourself in the browser pane and report numbers: no console errors, no horizontal overflow, contrast at the brightest animation frame, and what happens with reduced motion.

---

# Round 9: background

## Message I (paste this)

The hero blueprint and the overall landing page are good, and the measurements you reported are exactly what I wanted. One change: I don't understand the page-wide black background with the uniform grid. It reads like a generic dev-tool template, nothing on the page explains it, and it competes with the blueprint drawing in the hero.

Please remove the page-wide grid and replace it like this:
1. A clean graphite base with soft, directional lighting (for example a teal glow behind the hero that warms toward the approval moment), a very faint film-grain texture and a vignette, so it has depth without looking like graph paper.
2. Keep a grid ONLY inside the blueprint panel, where it is literally the drawing paper.
3. Make the private-network idea real instead of a texture: in the "What it creates" section, draw the actual network as a few nodes and edges that light up as resources appear.

Apply the same background to the product pages (run page, dashboard, account pages), in dark and light, English and Persian. Keep contrast at least 4.5:1 and re-check it, keep the Apply/Decline rectangle fixed, and render it yourself and report the numbers again.

---

# Round 10: freeze the design, build it

## Message J (paste this whole message; it replaces Message C)

The design phase is over. Treat every board on the canvas as final and do not change them any more; if the build reveals a design problem, write it into `liara-agent/frontend/DESIGN_NOTES.md` and keep building. Now build the real frontend from the design.

**Location: `liara-agent/frontend/`** (inside the liara-agent project, next to `src/` and `docs/`). Your earlier test file went to the parent folder (`liara/frontend/README.md`): please delete that one-line file and its folder, and work only in `liara-agent/frontend/`. All paths below are relative to `liara-agent/`. The landing page experiments in `design/landing/` are design references; move what you need into the build and leave `design/` otherwise alone.

**Scope.** Write only under `liara-agent/frontend/`. Do not edit anything under `src/`, `tests/`, `docs/` or `scripts/`. Never read `.env`, `.web-data/` or `.agent-state/`. If you need the backend to change, write the request into `liara-agent/frontend/BACKEND_REQUESTS.md` instead of working around it.

**Stack.** Vite + React + TypeScript, react-router, plain CSS using CSS variables for the design tokens (dark and light), no UI framework. The build output goes to `liara-agent/frontend/dist`; the API server already serves that folder at `/` (same origin, so the session cookie and CSRF just work). In development, proxy `/api` to `http://127.0.0.1:8000`.

**Contract (do not invent anything).**
- API: `docs/openapi.json` and `docs/api.md`. Send `credentials: "include"` and the header `X-Requested-With: XMLHttpRequest` on every POST/PUT/DELETE.
- Events: `docs/events.d.ts` has TypeScript types for every event. A run is a list of events `{id, ts, kind, data}`. Use `EventSource` on `/api/runs/{id}/stream` (the browser resends `Last-Event-ID` when it reconnects). Ignore unknown event kinds.
- Real data to develop and test against: `docs/samples/*.json`.
- Wording: every backend message is a code plus params. Build i18n catalogs `frontend/src/i18n/en.json` and `fa.json` from the catalog board in the design and `docs/message-codes.md`, and fall back to the English `message`/`text` only when a code has no translation. Persian is a draft that I will have reviewed, so keep the catalogs in plain JSON files.

**Architecture that matters.**
1. One pure function `reduceRun(events) -> RunViewState` that turns the event list into everything the run page shows (pipeline node states, architecture nodes with prices, timeline, logs, tests, open requests, cost, final result). Every screen reads that state; no screen parses events itself. It must work for a live stream and for a replay.
2. Open requests (question / confirm / secret) are derived from the events: a request without a response is open. They must survive a page reload.
3. A secret typed by the user is sent once and never stored in React state after sending, never put in the URL, never logged.
4. Approval buttons Apply and Decline both work; declining must never look like an error.
5. A demo mode at `/demo/waiting`, `/demo/failed`, `/demo/dryrun` replays `docs/samples` through the same reducer with no backend, so the design can be reviewed without Liara.

**Motion.** Implement the full motion system from the final boards and the landing/motion spec on the real pages: page enter (rise only), flowing pipeline and architecture edges, number tickers with locked widths, the success moment, skeleton shimmers (also for the run page, which the boards do not have), hover depth, the landing hero replay and the scroll-linked run. All of it in CSS, SVG and the Web Animations API, paused when the tab is hidden, and with a real `prefers-reduced-motion` version (also test hover and focus transitions under reduced motion). Re-measure contrast on every page with the motion applied, including a few mid-animation frames.

**Fonts and assets.** Self-host every font file (Sora, Vazirmatn, JetBrains Mono, or whatever the final design uses) in the build and load nothing from a CDN at runtime: the users are in Iran and third-party font hosts may be slow or unreachable. Subset the Persian and Latin ranges, preload the files that matter, use `font-display: swap`, and give the fallback font adjusted metrics (`size-adjust`, `ascent-override` and friends) so the font swap does not move anything. Your design test showed Apply shifting 7px on a cold first load: in the build, measure a cold load (empty cache) and show me that Apply and Decline do not move. Use CSS variables for one token set with explicit surface roles (glass, panel, terminal).

**Run and look at your own work.** After each stage run `npm install`, the tests, `npm run build`, and open the running app in the built-in browser at 1440x900, 768x1024 and 390x844. Measure, do not guess: the approval card's Apply button must be visible on load at 1440x900 on the run page, no horizontal overflow, every control at least 44px on phone, no console errors, and Persian pages must be `dir=rtl`. Show me the numbers. Do not install anything globally, do not use sudo, and do not run `git init`.

**Tests.** Vitest for the reducer (use the three sample streams: assert pipeline states, cost totals, open requests, the `resource_updated` price change, and that no secret value appears in the state), and for the i18n lookup (every code used in the samples has an English entry). Run them and show me the output.

**Order of work** (stop after each step and show me what you built and what you verified):
1. Scaffold, API client from the OpenAPI file, auth pages, connect-Liara flow with the team selector.
2. `reduceRun` and its tests, then the run page wired to the live stream, plus the demo routes.
3. Dashboard, new deployment, project page.
4. i18n and RTL for everything, language toggle.
5. Landing page with the hero replay and the scroll-linked run.
6. Responsive layouts and accessibility pass (keyboard, focus, reduced motion).

Be honest about what you could not run or check.
