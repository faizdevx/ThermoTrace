import { useEffect, useRef, useCallback } from 'react';

/**
 * Returns a debounced version of `callback` that only fires after
 * `delay` ms of inactivity. The returned function is stable across renders.
 */
export function useDebouncedCallback<TArgs extends unknown[]>(
  callback: (...args: TArgs) => void,
  delay: number,
): (...args: TArgs) => void {
  const callbackRef = useRef(callback);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Always keep callbackRef current so the debounced fn sees latest closure
  useEffect(() => {
    callbackRef.current = callback;
  });

  return useCallback(
    (...args: TArgs) => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => callbackRef.current(...args), delay);
    },
    [delay],
  );
}
