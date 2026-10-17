const PATHS: Record<string, React.ReactNode> = {
  flame: <path d="M12 2c1 4-3 5-3 9a3 3 0 0 0 6 0c0-1-.5-2-1-3 3 1 5 4 5 7a7 7 0 0 1-14 0c0-6 5-8 7-13z" />,
  siren: <><path d="M7 18v-6a5 5 0 0 1 10 0v6" /><path d="M5 21h14M12 2v2M4.5 6.5l1.5 1.5M19.5 6.5 18 8" /></>,
  factory: <path d="M3 21V9l6 4V9l6 4V4h3v17zM8 17h2m3 0h2" />,
  warning: <><path d="M12 3 2 20h20z" /><path d="M12 10v5m0 3v.01" /></>,
  spark: <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2" /></>,
  check: <path d="m4 12 5 5L20 6" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>,
  inbox: <path d="M3 13h5l1 3h6l1-3h5M5 5h14l2 8v6H3v-6z" />,
  layers: <path d="m12 3 9 5-9 5-9-5zM3 13l9 5 9-5" />,
};

export type IconName = keyof typeof PATHS;

/** Decorative inline icon (aria-hidden): the visible label next to it carries the meaning. */
export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ flexShrink: 0 }}>
      {PATHS[name]}
    </svg>
  );
}
