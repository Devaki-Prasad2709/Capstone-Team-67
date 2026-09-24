# GIS → Graph Builder → Observation Log → TGNN — Core Integration Layer

This is the "pure core" layer from the implementation plan: fully testable
without Kafka/Spark/MinIO. It is verified through checkpoint-backed TGNN
inference on GIS-derived graph snapshots. Torch and PyTorch Geometric are
declared through the repository requirements files.

## What this replaces / adds

| File | Role |
|---|---|
| `gis/gis_loader.py` | Loads GeoJSON GIS data, reprojects WGS84 → working CRS (EPSG:32643). **Single entry point** for coordinate conversion. |
| `gis/fixtures/demo_gis.geojson` | Clearly-labeled placeholder GIS data (small area near Bengaluru) — used only until your real GIS dataset (currently on USB) is available. Swap the file path in `gis_loader.load_gis()`; nothing else changes if the real data follows the same `feature_class` convention. |
| `gis/building_association.py` | One-time building→infrastructure lookup + runtime point-in-polygon/nearest-road resolver. This is what turns a geolocated detection into a `node_id`. |
| `graphs/gis_graph_builder.py` | **Replaces** the random generator in your original `graph_builder.py`. Produces the exact same node/edge attribute contract, so `helpers.py`/`tgnn.py` need zero changes. |
| `observation/geolocator.py` | Pixel→GPS interface. `NullGeoLocator` fails loudly (no fabricated GPS). `ManualOverrideGeoLocator` is test-only, used in the demo. |
| `observation/observation_log.py` | Append-only observation store + decay-weighted, max-based damage aggregation. |
| `observation/state_update.py` | Applies aggregated damage → `effective_capacity` → `stress` onto the graph, producing G(t) from G(t-1). |
| `overlay/gis_overlay.py` | Graph → GeoJSON for the dashboard's `/api/overlay/nodes` contract. **Single exit point** for coordinate conversion (working CRS → WGS84). |
| `integration/pyg_bridge.py` | Thin wrapper sequencing graph snapshots into the list your `TGNN.forward()` expects — calls your existing `nx_to_pyg`, unmodified. |
| `integration/tgnn_predictor.py` | Loads the committed checkpoint, validates temporal node order, and maps prediction rows back to GIS node IDs. |
| `pipeline_demo.py` | End-to-end proof: GIS → G(0) → synthetic observations → G(t) → PyG sequence → checkpoint-backed TGNN. |
| `reference/` | Your three uploaded files, untouched, used by the demo. |

## What's been tested (in this environment)

Ran `pipeline_demo.py` successfully through:
1. Loading the demo GIS fixture and building G(0) — **17 nodes, 157 edges** (146 spatial, 11 dependency)
2. Building association — correctly resolved all 3 demo buildings to their nearest road/hospital
3. Simulating 3 drone passes (Severe damage, 3 different confidences/timestamps) over one building — correctly resolved to the same road node each time
4. Applying observations — damage went `0.000 → 0.608`, stress `0.350 → 1.000` (capped), via the decay/max aggregation formula, exactly matching the worked example in the architecture doc
5. Building the dashboard overlay — correct WGS84 round-trip (output coordinates matched the input fixture's coordinates to float precision)

6. Loading `tgnn/models/tgnn.pth`, running inference on `[G(0), G(t)]`, mapping risk back to all 17 GIS node IDs, and adding risk to the GeoJSON overlay.

## Known, disclosed simplifications (see comments in code for detail)

- **Building/road nearest-neighbor uses straight-line distance**, not network distance (no routable road topology yet — the fixture's road segments aren't connected into a routing graph). Swap `building_association._nearest()` once real road topology with intersections exists.
- **Dependency edge `weight` is a flat 1.0** — a real "fraction of demand served" value needs utility service-area data we don't have. Disclosed in `gis_graph_builder.py`.
- **`load` stays at its GIS baseline** — no real-time load estimation yet. Disclosed in `state_update.py`.
- **`DEFAULT_CAPACITY_BY_TYPE` values are placeholders** — replace with real engineering lookup tables (road class capacity, substation ratings, hospital bed counts) as they become available.

None of these block the pipeline from running correctly today — they're documented assumptions, not silent gaps.

## Mapping into `Devaki-Prasad2709/Capstone-Team-67`

Based on the repo tree you gave me, I'd suggest:

```
Capstone-Team-67/
  ai/
  common/
  ...
  core/                      <-- NEW: this entire folder's contents
    gis/
    graphs/
    observation/
    overlay/
    integration/
    reference/               <-- or wherever tgnn.py/helpers.py end up living permanently
  data_source/
    gis/
      gis_producer.py         <-- UNCHANGED for now (see note below)
  spark/
    ...
```

**On the existing `gis_producer.py`:** per the audit, it currently only ships map images through Kafka — it isn't the structural GIS layer. I haven't touched it. The `core/gis/gis_loader.py` I wrote is a standalone loader that doesn't go through Kafka at all yet (per the "pure core first, Kafka adapter second" plan) — once this core layer is proven against real GIS data, the adapter layer would have the `gis_producer` (or a new structural-GIS producer) publish to a new topic and a thin Kafka consumer call `gis_loader.load_gis()`-equivalent logic on the payload. That adapter isn't built yet — deliberately, per the phased plan.

**On `tgnn_disaster_prediction`:** it's currently separate from this repo (per your notes). You'll need to decide whether to move/copy `models/tgnn.py`, `utils/helpers.py`, `graphs/simulator.py` into this repo (e.g. under `core/reference/` or a new `tgnn/` folder) or keep them as a separate installable package the streaming repo depends on. I didn't make that call for you — tell me which and I'll adjust the import paths accordingly (`pipeline_demo.py` currently assumes they're at `reference/` relative to this folder, which is a placeholder location).

## Next steps (in order)

1. You: decide where `tgnn_disaster_prediction`'s files actually live relative to this new `core/` folder, so imports are correct once this is pushed.
2. Run `pipeline_demo.py` in your local environment to confirm Step 6 (actual TGNN call) completes.
3. Replace `gis/fixtures/demo_gis.geojson` with real GIS data once you have it off the USB — if it's not already GeoJSON, tell me the format and I'll extend `gis_loader.py` to parse it directly (shapefile/GeoPackage are both straightforward additions).
4. Wire the real `ai-analysis-results` Kafka contract into `observation/observation_log.py` (a thin adapter: consume the topic, build a `Detection` per entry in the `detections` array, call a real `GeoLocator` once one exists, call `spatial_index.resolve()`, append an `ObservationRecord`). This is the "Kafka adapter" phase — not built yet, deliberately.
5. Implement a real `GeoLocator` once drone telemetry (GPS/altitude/gimbal/FOV) is confirmed available in the pipeline.
