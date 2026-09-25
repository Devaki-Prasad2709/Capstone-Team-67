# Louisiana-East flood scenario

This directory is the canonical demo contract for one bounded disaster story.
It combines a pinned, real OpenStreetMap structural snapshot with a deterministic
simulated event timeline. Model outputs are produced by the real committed YOLO
and TGNN artifacts; they are not stored here as invented results.

## Current readiness

- Structural GIS: ready (21 OSM source features in the selected tile).
- SpaceNet 8 tile `2_23_44`: ready (real pre/post imagery; 22 of 40
  reference features are flood-labelled).
- Simulated drone, telemetry, and social event envelopes: ready.
- Three real ISBDA drone images: ready (Slight, Severe, and Debris examples).
- Drone Louisiana GPS assignments and scenario timestamps: simulated and
  explicitly identified in `drone/selection.json`.

Validate what is available now:

```powershell
.venv\Scripts\python.exe -m scripts.validate_scenario scenarios\louisiana_east_flood\scenario.json
```

Run the strict gate and verify both external datasets and their registered
SHA-256 checksums:

```powershell
.venv\Scripts\python.exe -m scripts.validate_scenario `
  scenarios\louisiana_east_flood\scenario.json `
  --strict `
  --spacenet-root D:\Capstone-Team-67\datasets\Satellite\spacenet8\Spacenet8_Louisiana-East_Trainingtar `
  --isbda-root D:\Capstone-Team-67\datasets\Drone\ISBDA
```

To refresh the OSM snapshot deliberately (this changes the frozen input):

```powershell
.venv\Scripts\python.exe -m scripts.fetch_osm_scenario_gis `
  --bbox=-90.08553068272339,29.75831551754242,-90.07989924402005,29.763946956245753 `
  --output scenarios\louisiana_east_flood\gis\infrastructure.geojson
```

OpenStreetMap data is (c) OpenStreetMap contributors and licensed under ODbL 1.0.
