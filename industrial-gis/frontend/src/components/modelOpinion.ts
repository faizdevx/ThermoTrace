import type { ThermalProperties } from '@/types/industrial';

export type Agreement = 'agrees' | 'differs' | 'rules_only_class' | 'none';

export interface ModelOpinion {
  available: boolean;
  headline: string;
  agreement: Agreement;
  note: string;
  reasons: string[];
  version: string | null;
  bars: { id: string; label: string; p: number }[];
}

const NAME: Record<string, string> = {
  industrial_persistent: 'persistent industrial source',
  agricultural_burning: 'crop-residue burning',
  vegetation_fire: 'forest / vegetation fire',
};

/** What the trained model says, and whether it agrees with the rule class. The model has three classes; the rule
 *  engine also has "fire candidate", "unmapped persistent" and "other", which the model cannot output. */
export function modelOpinion(p: Pick<ThermalProperties, 'thermal_class' | 'model_class' | 'model_probability'
  | 'model_probabilities' | 'model_reasons' | 'model_version'>): ModelOpinion {
  if (!p.model_class || p.model_probability == null) {
    return {
      available: false, headline: 'No model opinion', agreement: 'none', reasons: [], version: null, bars: [],
      note: 'No trained model is deployed for this run; the rule class above is the only answer.',
    };
  }
  const shared = Object.keys(NAME);
  const agreement: Agreement = !shared.includes(p.thermal_class) ? 'rules_only_class'
    : p.thermal_class === p.model_class ? 'agrees' : 'differs';
  const note = agreement === 'agrees' ? 'Matches the rule classifier.'
    : agreement === 'differs' ? 'Disagrees with the rule classifier - treat this source as uncertain.'
      : 'The rules use a class the model does not have (fire candidate / unmapped / other); the model picks the closest of its three.';
  const bars = Object.entries(p.model_probabilities ?? {}).sort(([, a], [, b]) => b - a)
    .map(([id, v]) => ({ id, label: NAME[id] ?? id, p: v }));
  return {
    available: true, agreement, note, version: p.model_version ?? null, reasons: p.model_reasons ?? [], bars,
    headline: `${NAME[p.model_class] ?? p.model_class} (${Math.round(p.model_probability * 100)}%)`,
  };
}
