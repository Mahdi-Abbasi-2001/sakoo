import { useCallback, useEffect, useRef, type RefObject, type UIEvent } from 'react';

/** Keeps a scroll box on its newest content while the run goes. It follows from the start; the person scrolling up stops it, scrolling back to the end resumes it.
 *  Growth is watched in the DOM itself (not in a render count), so a burst of lines, a capped list that keeps the same length, or a line that grows all keep following.
 *  A scroll the page made itself is told apart from the person's by remembering where it put the box. */
export function useFollow(box: RefObject<HTMLElement | null>, enabled: boolean) {
  const follow = useRef(true);
  const placed = useRef(-1);
  const toEnd = useCallback(() => {
    const el = box.current; if (!el) return;
    el.scrollTop = el.scrollHeight; placed.current = el.scrollTop;
  }, [box]);
  useEffect(() => {
    const el = box.current; if (!el || !enabled) return;
    if (follow.current) toEnd();
    const mo = new MutationObserver(() => { if (follow.current) toEnd(); });
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => { if (follow.current) toEnd(); }) : null;
    ro?.observe(el);
    return () => { mo.disconnect(); ro?.disconnect(); };
  }, [box, enabled, toEnd]);
  const onScroll = useCallback((e: UIEvent<HTMLElement>) => {
    const el = e.currentTarget;
    if (Math.abs(el.scrollTop - placed.current) < 2) return;          // our own scroll to the end
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }, []);
  return { onScroll, toEnd };
}
