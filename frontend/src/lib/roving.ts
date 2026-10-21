import type { KeyboardEvent } from 'react';

/** Arrow-key behaviour of a radio group or a tab list: the arrows (mirrored in a right-to-left page), Home and End move focus to the next
 *  item and select it. Only the selected item is in the Tab order, so one Tab enters the group and the next leaves it. */
export function rovingKeys(role: 'radio' | 'tab') {
  return (e: KeyboardEvent<HTMLElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>(`[role=${role}]:not(:disabled)`)];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0 || items.length < 2) return;
    const rtl = getComputedStyle(e.currentTarget).direction === 'rtl';
    const fwd = e.key === 'ArrowDown' || (e.key === 'ArrowRight' && !rtl) || (e.key === 'ArrowLeft' && rtl);
    const n = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : (i + (fwd ? 1 : -1) + items.length) % items.length;
    e.preventDefault();
    items[n].focus();
    items[n].click();
  };
}
