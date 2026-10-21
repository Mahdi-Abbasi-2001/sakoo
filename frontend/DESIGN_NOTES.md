# Design notes

The canvas boards are final. Anything the build finds that the design did not answer is written here, and the build keeps going.

## Step 1
- **No sign-in or register page exists on the canvas.** They are composed from the existing parts (title, lead, one `card`, the standard field and button), and are not a design decision. Please review them.
- **Settings is one page.** The Connect board shows a side list (Liara account, Language, Theme, Your account). It is built as one scrolling page with that list as in-page links, and `/connect` redirects to `/settings`.
- **Header on a phone.** The boards have no phone header for the settings pages. At 700px and below the header wraps onto two rows (brand and toggles, then navigation and the connection badge) so nothing overlaps at 390px.
- **Team name.** The Ready and Connected states in the Connect board show the team's name. After saving, the API only returns `team_id`, so the Connected line shows the id. See BACKEND_REQUESTS.md.
- **Theme choice.** The theme is a local choice (kept in the browser), shown as a choice in Settings and as a button in the header. The design had a Theme link but no board for it.

## Step 2 (run page and demo routes)
- **Architecture is drawn in SVG with an automatic layout**, not with absolutely placed boxes as on the board: internet, then apps (with their disks), then databases, edges labelled with the variable that links them, mirrored in Persian. Below 700px it becomes a list, because a 760-unit drawing cannot keep 11px text on a phone.
- **Pipeline on a phone** becomes a vertical list (no horizontal scroll), with the connector line hidden. The boards have no phone pipeline for this state.
- **The in-card Apply on a phone and tablet is below the first screen**, because the card follows the headline. The sticky bar (price, Apply, Decline, 44px each) is what stays in view, as the design intends. If you want Apply on the first screen at 390px too, the headline block has to shrink: that is a design call.
- **"Attempt n / 3"** uses 3 from the board. The backend does not say the maximum (see BACKEND_REQUESTS.md).
- **Run page loading** has a skeleton (the boards have none): the same blocks in the same places.
- **Demo bar** (the row of demo links, play, pause, step, restart) is not on any board. It exists only on `/demo/*`.
- **Tiles:** while a run is live the tiles read "RUNNING / HR" and "RUNNING / MONTH", before that "COST", and "BILLING NOW" once something exists but the run is not live.
- **Wording for plurals** ("1 app", "2 apps") comes from `unit.*` keys; the board always wrote the plural.

## Step 3 (dashboard, new deployment, project page)
- **Run numbers.** The project row has no run number, only a hash id. The run history numbers its runs from the oldest (#1) up, by position. The boards show the same numbers.
- **Dashboard "Started 2 min ago"** uses the run's start time. Dates older than a week show as a date and time, in the Persian calendar when the language is Persian.
- **Waiting card countdown.** The dashboard card fetches the waiting run to read its `expires_at`; with several waiting runs it shows the general sentence instead of one countdown.
- **Environment variables card** is built from the plan of the live run (the names, and whether a value came from a database or from you). It shows names only. If a project has no live run, the card is not shown.
- **Dashboard "Live" label for a succeeded run** uses the catalog word `status.succeeded` ("Live" in English, "موفق" in the draft Persian). The Persian word should probably be "زنده"; it is in the catalog for the review.
- **New deployment: the board's "If something is wrong" examples** are the error states of the same form. They are rendered under the field they belong to; they are not a separate section.
- **Project name:** the form suggests a name from the zip file name or the Git address (lowercase letters, digits and hyphens). The backend's own rules for names are not documented; the field shows whatever error the backend returns.

## Step 4 — i18n and RTL

- **Language toggle** (header, `EN | فا`): sets `<html lang dir>` and `<title>` at once, stores the choice in `localStorage` (so the login page remembers it) and, when signed in, in the account via `PUT /settings {language}` — which also sets the language of the AI's notes. Labels are autonyms and never translated.
- **Digits:** in Persian every number the UI writes (counts, prices, times, step numbers, tab counts) uses Persian digits and the Persian thousands/decimal separators. Identifiers keep Latin digits on purpose: versions (`16.14`), ports (`:3000`), plan names (`small-g2`), `HTTP 401`, and the literal character class `0-9` in the username rule.
- **Isolation:** Latin-only strings inside Persian text (app names, paths, URLs, commands, env names) are wrapped in LRI/PDI so punctuation does not jump to the wrong side. Logs, code, paths and URLs are always `dir=ltr`.
- **Arrows** that mean "forward" are mirrored in RTL (`.ic-flip`); the Apply/Decline buttons do not move or reorder.
- **Dates:** Persian pages show relative times ("۲ دقیقه پیش") and Persian-calendar dates via `Intl` (`fa-IR-u-ca-persian`); English uses Gregorian.
- **Plurals:** phrases come from `{apps_phrase}`-style keys with a zero form ("no databases" / «بدون پایگاه‌داده»). Persian has no grammatical plural after numbers, so the same form is used for 1 and many.
- **Catalogs:** `src/i18n/en.json` and `fa.json` are plain JSON, flat keys, same key set (tested). The Persian file is a draft; `src/i18n/inferred.json` lists the 107 entries whose wording was inferred rather than taken from a real sample, for review.
- **Gap:** text with no code (`plan_ready.how`, evidence notes, `would_send[].summary`, `diagnosis.evidence[]`, AI text with `code: null`) is shown as the backend sent it — see BACKEND_REQUESTS.md.

## Step 5 — landing page

- **Where it lives:** `/` shows the landing page to a visitor who is not signed in and the dashboard to a signed-in user; `/welcome` always shows the landing page. The page is a lazy chunk (about 27 kB gzipped, mostly the scene drawings), so the app itself does not pay for it. An anonymous visit makes one API call (`GET /api/session`).
- **Built from the boards:** `scripts/gen-landing.mjs` reads `design/landing/landing-{en,fa}.html` and writes the scene SVG (`src/landing/scenes.gen.ts`) and the stylesheet (`src/styles/landing.gen.css`). The Persian scene keeps its own mirrored geometry. Every word inside a scene is a `{{w.key}}` placeholder filled from the catalogs. The engine (`src/landing/engine.ts`) is the board's script ported to TypeScript: the whole sheet is one function of the replay clock.
- **Class names:** 31 class names (`btn`, `chip`, `appr`, `nav`, …) are also used by the app, so the landing copies carry an `lp-` prefix. Neither stylesheet leaks into the other.
- **Background:** the same graphite background as the app (directional light, grain, vignette, no page grid). The grid exists only inside the sheet. The warm light behind the hero follows the replay: it rises while the approval waits.
- **Differences from the board, all small:**
  1. The language and theme controls are the app's (`EN | فا` and the sun/moon button) instead of two links to separate pages. Changing language restarts the replay in that language.
  2. The three footer links "Docs / Status / Contact" were placeholders with no address, so they are not rendered. The footer has Sign in and Create account. Please send real URLs.
  3. "max size [placeholder]" shows the real upload limit from `GET /api/limits`, only for signed-in users (the endpoint needs a session). Visitors see no size.
  4. The rule "It works in your own Liara account" had `[placeholder: token scope and storage]`. I wrote what `docs/api.md` states: the token is stored encrypted and never shown again. Please confirm the wording.
  5. For a visitor the call to action goes to Create account, with a "Sign in" link under it. For a signed-in user it goes to New deployment. The drop zone and Git field on the landing page only remember what was chosen. They do not carry it into the app.
  6. The brand is "Liara Launchpad" in English and «سکوی لیارا» in Persian.
- **Numbers in the replay** are the board's (from the build-timeout and dry-run samples). They are not fetched.
- **Reduced motion:** real `prefers-reduced-motion`. No clock, no running animation, the Live frame plus the five frame buttons, and the scroll-linked sheet snaps to Read, Plan, Approve and Live. Hover/focus lifts and transitions are off (colour change only).

## Step 6 — responsive, accessibility, motion

Things the checks found in the build (not in the boards) and what was done. None changes a board; the diagram geometry is the only visible change.

- **Architecture diagram** (run page): the disk node was too short for its third line (text hung on the border), the gap between the app and database columns was too narrow for a variable name (`DATABASE_URL` was cut by the node boxes), and the Persian worker line was wider than its node. Nodes are now 210 wide with 88 between columns (viewBox 780 wide), disks 66 tall, and the disk line says "price not verified" instead of the long sentence.
- **Landing hero:** file labels that fly into the orb shrink below legible size while crossing its bright ring. Below 80 % scale the label is removed (not faded). The price labels above the nodes had no halo and sat on the network's grid lines while a resource lit its cells; they now have the same halo as the other scene text. The six state names under the hero are no longer cut with an ellipsis.
- **Light theme:** log line numbers, times and the violet "waiting" kind label were 4.35–4.46:1 on tinted rows; now darker.
- **Keyboard:** a real skip link that appears on focus (both layouts); radio groups and tab lists use arrow keys (mirrored in Persian) with one Tab stop each; Enter in the token field checks the token; the cleanup dialog takes focus, closes on Escape and gives focus back; `scroll-padding` keeps a focused control out from under the sticky header and the bottom approval bar; the language buttons are named "English" / "فارسی".
- **Screen reader:** two live regions that exist from the first render. A request that needs you is announced assertively with what is asked, the price, and the choices; the end of the run is announced politely (live with URL and checks, failed with the reason and what was created, declined, preview, interrupted); an answer you send is acknowledged once. When the card you used disappears, focus moves to the page heading instead of the top of the document. The page title carries the state ("Waiting for you · blog · Deploy Agent"). Direction marks are stripped from announcements and titles. The decline note is the description of the Decline button; the totals are the description of Apply.
- **Reduced motion:** on the landing page hover and focus no longer lift the panel or the call to action (the board only removed the transition, the 2 px move still happened). Everything else was already still.
- **Known weak spots, left as they are:** disabled buttons (Sign in before the form is valid, "Add a custom domain", Delete) are 1.4–4.2:1; WCAG exempts disabled controls, but "Add a custom domain" is permanently disabled and says what it is next to a "Planned" badge. A focus ring that lands on a half pixel at 100 % zoom looks dimmer on one edge (measured 1.8:1 on the top edge of the log search on a phone, 10:1 on the other three); on a 2x screen it is crisp.

## Cleanup (run page, dashboard, project page)

A cleanup writes no event, so the run page cannot learn about it from the stream. It now reads `cleaned_at` from `GET /api/runs/{id}` and, right after a successful cleanup, uses the cleanup's own answer (`deleted`) so the page changes at once, then reads the run again for the real time. The "removed" state is an overlay on the event-derived view (`applyRemoval` in `reduceRun.ts`): resources become "removed", billing is 0, the addresses are gone, and the pipeline and timeline are untouched. It is its own situation ("Removed from Liara"): a calm card with the deleted names and when, billing tiles at 0 plus a "deleted" tile, no live card, no Open app or Copy, no 7-day warning, no delete button, and the failed-run "this still bills" banner is hidden. The "Live" pipeline step says "removed" instead of "live". Run lists (dashboard row, project header and history) tell a cleaned-up run as "Removed" instead of "Live" or "Failed". Focus moves to the page heading and the polite live region says "Removed from Liara".
Which names are listed after a reload: the run row only says when, so the list is what the run created (`result.created`), which is exactly what a cleanup deletes.
