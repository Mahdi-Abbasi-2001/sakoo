import { useEffect, useState } from 'react';
import { api } from '../api/client';

let cached: { at: number; ok: boolean } | null = null;

/** Whether the server's AI model is set up and reachable (null while unknown). Features that need it are hidden when it is not. Asked at most every 5 minutes. */
export function useAssistantOnline(): boolean | null {
  const [ok, setOk] = useState<boolean | null>(cached && Date.now() - cached.at < 300000 ? cached.ok : null);
  useEffect(() => {
    if (cached && Date.now() - cached.at < 300000) { setOk(cached.ok); return; }
    let dead = false;
    api.chat(0).then((c) => { cached = { at: Date.now(), ok: c.available !== false }; if (!dead) setOk(cached.ok); }).catch(() => { if (!dead) setOk(true); });
    return () => { dead = true; };
  }, []);
  return ok;
}
