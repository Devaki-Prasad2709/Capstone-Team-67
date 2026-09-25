# Final demo scenario: Louisiana-East flood

The project now has one frozen, testable scenario instead of disconnected demo
inputs. Its canonical manifest is
`scenarios/louisiana_east_flood/scenario.json`.

## Story

The baseline graph is built from roads and buildings in Jean Lafitte,
Jefferson Parish, Louisiana, inside the exact footprint of SpaceNet 8 tile
`2_23_44`. The real satellite pair contains 40 labelled features, 22 marked
flooded. It is followed by drone observations over Estuary Road, a geolocated
social distress report, and degraded road telemetry. These observations update graph state;
the checkpoint-backed TGNN then ranks node risk and the dashboard presents the
progression and recovery update.

## Data truth labels

| Input | Origin | Status |
|---|---|---|
| Roads and buildings | OpenStreetMap snapshot | Ready |
| Timeline and telemetry | Simulation | Ready |
| Social message and coordinates | Simulation | Ready |
| Drone event envelopes, GPS, and scenario time | Simulation | Ready |
| Drone image bytes and official annotations | ISBDA external dataset | Ready |
| Satellite before/after imagery | SpaceNet 8 tile `2_23_44` | Ready |
| YOLO detections | Committed model inference | Ready |
| TGNN risks | Committed checkpoint inference | Ready |

The scenario does not disguise simulated values as field observations. The
drone images and annotations are real ISBDA data, while their Louisiana GPS,
target associations, and scenario timestamps are explicitly simulated.

## Implementation order from here

1. Add a scenario runner that publishes the event files on the simulated clock.
2. Connect the dashboard to scenario start/pause/reset and timeline state.
3. Run the full Kafka, MinIO, Spark, YOLO, graph, and TGNN acceptance rehearsal.
4. Capture expected screenshots and freeze the release commit/tag.

## Validation commands

```powershell
.venv\Scripts\python.exe -m scripts.validate_scenario scenarios\louisiana_east_flood\scenario.json
.venv\Scripts\python.exe -m scripts.validate_scenario scenarios\louisiana_east_flood\scenario.json --strict
.venv\Scripts\python.exe -m pytest -q
```

The strict command validates the frozen registrations and provenance. Pass the
external root options shown in the scenario README to additionally verify every
raw source file by SHA-256.
