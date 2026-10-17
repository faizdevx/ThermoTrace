import { useMemo, useRef, useState } from 'react';
import type { FacilityFeature } from '@/types/industrial';
import { prettyKind, searchFacilities, type FacilityHit } from '@/components/facilityLookup';

interface Props {
  features: FacilityFeature[] | undefined;
  loaded: boolean;
  onPick: (hit: FacilityHit) => void;
}

/** Find a plant, mine, refinery or flare by name and fly the map to it. */
export function FacilitySearch({ features, loaded, onPick }: Props) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => searchFacilities(features, text), [features, text]);

  const pick = (h: FacilityHit) => {
    onPick(h);
    setText(h.name);
    setOpen(false);
  };

  return (
    <div ref={box} style={{ position: 'relative', flex: '1 1 220px', maxWidth: 420, minWidth: 140 }}
      onBlur={e => { if (!box.current?.contains(e.relatedTarget as Node | null)) setOpen(false); }}>
      <input
        id="facility-search" type="search" role="combobox" aria-expanded={open && hits.length > 0} aria-controls="facility-hits"
        aria-label="Find a facility"
        placeholder={loaded ? 'Find a plant, mine, refinery…' : 'Loading facilities…'}
        value={text} disabled={!loaded}
        onChange={e => { setText(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={e => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, hits.length - 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
          else if (e.key === 'Enter' && hits[active]) { e.preventDefault(); pick(hits[active]); }
          else if (e.key === 'Escape') setOpen(false);
        }}
        style={{ width: '100%', boxSizing: 'border-box', padding: '7px 12px', borderRadius: 99, border: '1px solid #cbd5e1',
          background: '#f8fafc', fontSize: 13, outline: 'none', fontFamily: 'inherit' }}
      />
      {open && text.trim().length >= 2 && (
        <ul id="facility-hits" role="listbox" style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 1200,
          margin: 0, padding: 4, listStyle: 'none', background: '#fff', borderRadius: 12, boxShadow: '0 8px 28px rgba(15,23,42,0.18)',
          maxHeight: 320, overflowY: 'auto' }}>
          {hits.length === 0 && <li style={{ padding: '8px 10px', fontSize: 12, color: '#64748b' }}>No facility with that name.</li>}
          {hits.map((h, i) => (
            <li key={h.id} role="option" aria-selected={i === active} tabIndex={-1}
              onMouseDown={e => { e.preventDefault(); pick(h); }} onMouseEnter={() => setActive(i)}
              style={{ padding: '7px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 13, background: i === active ? '#e0f2fe' : 'transparent' }}>
              <div style={{ fontWeight: 600, color: '#0f172a' }}>{h.name}</div>
              <div style={{ fontSize: 11, color: '#64748b' }}>{prettyKind(h.kind)}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
