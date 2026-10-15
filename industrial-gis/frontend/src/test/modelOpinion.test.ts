import { describe, expect, it } from 'vitest';
import { modelOpinion } from '@/components/modelOpinion';
import type { ThermalClassId } from '@/types/industrial';

const base = {
  thermal_class: 'industrial_persistent' as ThermalClassId, model_class: 'industrial_persistent' as const, model_probability: 0.93,
  model_probabilities: { industrial_persistent: 0.93, agricultural_burning: 0.05, vegetation_fire: 0.02 },
  model_reasons: ['distance to the nearest mapped facility 120 m supports a persistent industrial source (+0.31)'],
  model_version: 'thermal-gbm-ab12cd34-20261015',
};

describe('modelOpinion', () => {
  it('reports agreement with the rule class, probability bars and reasons', () => {
    const m = modelOpinion(base);
    expect(m.available).toBe(true);
    expect(m.agreement).toBe('agrees');
    expect(m.headline).toBe('persistent industrial source (93%)');
    expect(m.bars.map(b => b.id)).toEqual(['industrial_persistent', 'agricultural_burning', 'vegetation_fire']);
    expect(m.reasons).toHaveLength(1);
    expect(m.version).toContain('thermal-gbm');
  });

  it('flags disagreement', () => {
    const m = modelOpinion({ ...base, thermal_class: 'agricultural_burning' as ThermalClassId });
    expect(m.agreement).toBe('differs');
    expect(m.note).toMatch(/uncertain/);
  });

  it('explains that the model has no counterpart for fire-candidate / unmapped / other', () => {
    const m = modelOpinion({ ...base, thermal_class: 'industrial_fire_candidate' as ThermalClassId });
    expect(m.agreement).toBe('rules_only_class');
    expect(m.note).toMatch(/does not have/);
  });

  it('degrades to "no opinion" when no model ran', () => {
    const m = modelOpinion({ thermal_class: 'other' as ThermalClassId, model_class: null, model_probability: null, model_probabilities: null,
      model_reasons: null, model_version: null });
    expect(m.available).toBe(false);
    expect(m.headline).toBe('No model opinion');
  });
});
