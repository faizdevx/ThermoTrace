-- Boundary provenance: the boundary pipeline emits source_url but the base schema lacked it.
ALTER TABLE india_boundary ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE states ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE districts ADD COLUMN IF NOT EXISTS source_url TEXT;
