# Graph-state and cascade validation

The graph state engine now maintains this node contract in every temporal
snapshot:

`damage`, `load`, `capacity`, `effective_capacity`, `utilization`, `stress`,
numeric `status`, readable `status_label`, dependency availability,
state/observation timestamps, observation age, and freshness state.

## State equations

```text
intrinsic_capacity = capacity × (1 - damage)^1.5
effective_capacity = intrinsic_capacity × dependency_factor
utilization = load / capacity
stress = min(1, load / effective_capacity)
```

Dependency edges are directed provider-to-dependent edges with `edge_type=1`.
A provider failure reduces the dependent's `dependency_factor`; it does not copy
physical damage onto that dependent. Fixed-point propagation supports multi-hop
chains such as power → telecom → social.

## Louisiana road transition

The frozen telemetry creates this explainable Estuary Road sequence:

| Field | Baseline | Degraded at 12:02:30 | Recovery at 12:05:00 |
|---|---:|---:|---:|
| Load | 0.28 | 0.72 | 0.44 |
| Damage | 0.00 | 0.35 | 0.15 |
| Effective capacity | 0.80 | 0.4192 | 0.6269 |
| Utilization | 0.35 | 0.90 | 0.55 |
| Stress | 0.35 | 1.00 | 0.7018 |
| Status | operational | failed | operational |

Only an explicitly typed recovery/restoration event may reduce damage. Ordinary
telemetry and visual observations cannot silently erase prior damage.

## Temporal safety

- Every update returns a deep-copied graph; the baseline and earlier snapshots
  remain unchanged.
- Missing evidence is labelled `missing`; it does not fabricate zero-damage
  observations.
- Old evidence is labelled `stale` after the configured freshness window.
- Future visual observations are excluded until their timestamp is reached.
- Out-of-order telemetry/snapshot updates are rejected.
- Exact telemetry replay is idempotent; same-time conflicting telemetry fails.
- `explain_graph_transition()` produces a JSON-ready field-level before/after
  record for every scenario milestone, including an explicit no-change result
  for satellite, pending social, inference, and visualization-only events.

The scenario tile contains no functional dependency edges, so its risk cluster
must not be described as a real infrastructure cascade. Cascade behavior is
verified separately with a power → telecom → social test graph.
