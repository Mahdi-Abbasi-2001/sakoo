// Starts the real API on a free port with throw-away data (no .env is read), serving frontend/dist, with a stand-in for api.liara.ir so a deploy run can
// go all the way to the approval without a Liara token and without touching Liara. Leave it running, open the printed addresses in a normal browser,
// press Ctrl-C to stop it. Run `npm run build` first.
import { startServer } from '../e2e/server.mjs';
import { startFakeLiara } from '../e2e/fake-liara.mjs';
const liara = await startFakeLiara();
const srv = await startServer({ env: liara.env });
const u = srv.url;
console.log(`
Deploy Agent is running at ${u}  (throw-away data; Ctrl-C stops it)

Landing page            ${u}/
  frozen frames         ${u}/?t=8.5   ${u}/?t=10.4   ${u}/?t=19.5   ${u}/?t=26.4      (the five frame buttons)
  Persian               open any page, then press فا in the header
Sign in / create        ${u}/login   ${u}/register
Connect Liara           ${u}/settings          (any token works here: a stand-in answers instead of api.liara.ir)
Dashboard, new, project ${u}/dashboard    ${u}/new    (after you register)
Recorded runs           ${u}/demo/waiting      plan approval, question, Apply and Decline
                        ${u}/demo/failed       secret, build timeout, fix approval, live, declined fix
                        ${u}/demo/dryrun       a preview that sends nothing
  jump to a moment      add ?at=10 (plan approval)  ?at=12 (secret)  ?at=23 (fix approval)  ?at=30 (live)

To try keyboard only: press Tab on any page. To try reduced motion: turn on "reduce motion" in your operating system, then reload.
`);
process.on('SIGINT', () => process.exit(0)); process.on('SIGTERM', () => process.exit(0));
process.on('exit', () => { try { liara.stop(); } catch { /* gone */ } });
setInterval(() => {}, 1 << 30);
