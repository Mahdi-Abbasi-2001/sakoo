// Starts the real API serving frontend/dist (same origin, throw-away data) and keeps it up until stopped. Prints the URL.
import { startServer } from './server.mjs';
const s = await startServer(); console.log(s.url);
process.on('SIGTERM', () => { s.stop(); process.exit(0); }); setInterval(() => {}, 1 << 30);
