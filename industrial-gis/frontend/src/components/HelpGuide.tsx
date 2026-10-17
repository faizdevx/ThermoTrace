import { useEffect, useState } from 'react';

const KEY = 'thermotrace.guide.seen';

export function guideSeen(): boolean {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}
function markSeen() {
  try { localStorage.setItem(KEY, '1'); } catch { /* private window: guide simply shows again */ }
}

const DOT = (c: string) => (
  <span style={{ display: 'inline-block', width: 11, height: 11, borderRadius: '50%', background: c, marginRight: 6, verticalAlign: -1 }} />
);

const STEPS: { title: string; body: React.ReactNode }[] = [
  { title: 'What you are looking at',
    body: <>Each dot is a heat spot seen by NASA satellites (VIIRS), matched against 12,762 mapped factories, mines and refineries.
      {' '}{DOT('#b91c1c')}<b>Red</b> = routine industrial heat. {DOT('#ea580c')}<b>Orange</b> = possible industrial fire.
      {' '}{DOT('#7c3aed')}<b>Purple</b> = persistent heat, no mapped site. Pale squares = crop and forest fires.</> },
  { title: 'Is it an emergency?',
    body: <>The red <b>Alerts</b> button lists only what needs a look: abnormal heat at an industrial site. Open one to see why,
      the satellite imagery, and press <b>Confirm</b>, <b>Reject</b> or <b>Unsure</b>.</> },
  { title: 'Find a place',
    body: <>Type a plant or refinery name in the search box. Click any dot to see its class, its nearest facility, and how its heat compares with its own last year.</> },
  { title: 'How much to trust it',
    body: <>A dot says a place was hot, not that a plant is on fire. Always check imagery. Data is about 3 hours behind the satellite pass.</> },
];

export function HelpGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [i, setI] = useState(0);
  useEffect(() => { if (open) setI(0); }, [open]);
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { markSeen(); onClose(); } };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);
  if (!open) return null;
  const last = i === STEPS.length - 1;
  const done = () => { markSeen(); onClose(); };
  return (
    <div role="dialog" aria-modal="true" aria-label="Quick guide" data-testid="help-guide"
      style={{ position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(15,23,42,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
      onClick={done}>
      <div onClick={e => e.stopPropagation()}
        style={{ background: '#fff', borderRadius: 18, padding: '22px 24px', maxWidth: 440, width: '100%', boxShadow: '0 20px 60px rgba(15,23,42,0.35)' }}>
        <p style={{ margin: 0, fontSize: 12, fontWeight: 700, color: '#0369a1', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
          Quick guide {i + 1} / {STEPS.length}
        </p>
        <h2 style={{ margin: '4px 0 8px', fontSize: 19, color: '#0f172a' }}>{STEPS[i].title}</h2>
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: '#334155' }}>{STEPS[i].body}</p>
        <div style={{ display: 'flex', gap: 8, marginTop: 18, alignItems: 'center' }}>
          <button onClick={done} style={{ border: 'none', background: 'none', color: '#64748b', cursor: 'pointer', fontSize: 13 }}>Skip</button>
          <span style={{ flex: 1 }} />
          {i > 0 && <button onClick={() => setI(i - 1)} style={{ padding: '7px 14px', borderRadius: 99, border: '1px solid #cbd5e1', background: '#fff', cursor: 'pointer', fontSize: 13 }}>Back</button>}
          <button onClick={() => (last ? done() : setI(i + 1))} autoFocus
            style={{ padding: '7px 18px', borderRadius: 99, border: 'none', background: '#0f172a', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 700 }}>
            {last ? 'Got it' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}
