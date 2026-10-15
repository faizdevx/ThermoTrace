-- The trained model's output next to the rule classifier's, and the model's own evaluation record.
ALTER TABLE thermal_sources
    ADD COLUMN IF NOT EXISTS model_version        TEXT,
    ADD COLUMN IF NOT EXISTS model_class          TEXT,
    ADD COLUMN IF NOT EXISTS model_probability    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS model_probabilities  JSONB,
    ADD COLUMN IF NOT EXISTS model_reasons        JSONB;

CREATE TABLE IF NOT EXISTS thermal_model (
    model_version  TEXT PRIMARY KEY,
    metrics        JSONB NOT NULL,           -- models/metrics.json written by scripts/evaluate.py
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
