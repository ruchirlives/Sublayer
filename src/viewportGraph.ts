/** Changes that can stream frames must not invalidate every in-flight image. */
export function viewportGraphContext(graph: string): string {
  const parsed = JSON.parse(graph);
  if (Array.isArray(parsed?.nodes)) {
    parsed.nodes = parsed.nodes.map((n: Record<string, unknown>) => {
      if (n?.operation === "transform") return { ...n, transform: null };
      if (n?.operation === "pose") return { ...n, pose: null };
      if (n?.operation === "cache_edit" && n.cacheEdit && typeof n.cacheEdit === "object") {
        const ce = n.cacheEdit as Record<string, unknown>;
        return {
          ...n,
          cacheEdit: {
            targetQuery: ce.targetQuery ?? "",
            simEnabled: Boolean(ce.simEnabled),
            simCacheVersion: ce.simCacheVersion ?? 0,
            layers: ce.layers,
            temporalSmoothFrames: ce.temporalSmoothFrames ?? 0,
            temporalSmoothStrength: ce.temporalSmoothStrength ?? 0,
            maskWeights: ce.maskWeights,
          },
        };
      }
      return n;
    });
  }
  return JSON.stringify(parsed);
}
