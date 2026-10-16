import { useEffect, useState } from 'react';

/** True on phone-width screens (matches the 640px breakpoint in styles/index.css). */
export function useNarrow(query = '(max-width: 640px)'): boolean {
  const get = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false);
  const [narrow, setNarrow] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return narrow;
}
