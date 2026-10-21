# Liara Launchpad frontend

Vite, React, TypeScript, react-router, plain CSS variables. No UI framework, nothing loaded from a CDN.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to http://127.0.0.1:8000
npm test           # vitest
npm run build      # type-checks, then writes dist/, which `liara-agent serve` serves at /
npm run gen:api    # types from docs/openapi.json and docs/events.d.ts
npm run gen:fonts  # self-hosted fonts and their adjusted-metric fallbacks
npm run gen:landing # scene drawings and stylesheet of the landing page, from design/landing
npm run try        # real API + stand-in for api.liara.ir on a free port, prints every address to open in a normal browser
```
End-to-end checks (headless Chrome over the DevTools protocol, no extra packages): `node e2e/step1.mjs` with `APP_URL` pointing at a running server.

Step 6 checks (accessibility, responsive, motion), each writes its numbers to /tmp and prints a summary:
`e2e/step6-contrast.mjs <lang> <theme>` (every text on every page against the painted pixels), `e2e/step6-mid.mjs` (landing hero every 0.5 s, CSS animations seeked, hover/focus forced),
`e2e/step6-keyboard.mjs <lang> [rm]` (tab order, focus ring on pixels, operate approval, secret, toggles), `e2e/step6-sr.mjs <lang>` (what the live regions say and the accessibility tree),
`e2e/step6-rm.mjs <lang>` (reduced motion everywhere), `e2e/step6-layout.mjs <width> <lang>` (overflow, targets, structure).
