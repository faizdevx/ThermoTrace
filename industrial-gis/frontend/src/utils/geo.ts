/** Bounding box [west, south, east, north] of any GeoJSON geometry, or null if it has no coordinates. */
export function geometryBounds(geometry: { coordinates?: unknown } | null | undefined): [number, number, number, number] | null {
  if (!geometry || geometry.coordinates === undefined) return null;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  const walk = (node: unknown): void => {
    if (!Array.isArray(node)) return;
    if (node.length >= 2 && typeof node[0] === 'number' && typeof node[1] === 'number') {
      const [x, y] = node as number[];
      if (x < w) w = x;
      if (x > e) e = x;
      if (y < s) s = y;
      if (y > n) n = y;
      return;
    }
    for (const child of node) walk(child);
  };
  walk(geometry.coordinates);
  return Number.isFinite(w) ? [w, s, e, n] : null;
}

export function mergeBounds(
  boxes: Array<[number, number, number, number] | null>,
): [number, number, number, number] | null {
  const valid = boxes.filter((b): b is [number, number, number, number] => b !== null);
  if (!valid.length) return null;
  return [
    Math.min(...valid.map(b => b[0])), Math.min(...valid.map(b => b[1])),
    Math.max(...valid.map(b => b[2])), Math.max(...valid.map(b => b[3])),
  ];
}

export const INDIA_BOUNDS: [number, number, number, number] = [68.0, 6.5, 97.5, 35.8];
