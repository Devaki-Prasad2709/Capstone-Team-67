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

## Locked temporal story

| Step | Time | Transition |
|---:|---:|---|
| 1 | 12:00:00 | Load the 21-node baseline GIS graph |
| 2 | 12:00:30 | Display real SpaceNet post-event change evidence |
| 3 | 12:01:00 | Receive the first drone observation |
| 4 | 12:01:30 | Receive Severe, followed by Debris, evidence |
| 5 | 12:02:00 | Create a pending social distress alert |
| 6 | 12:02:15 | Simulated responder confirms the alert |
| 7 | 12:02:30 | Receive degraded road telemetry |
| 8 | 12:03:00 | Apply accumulated evidence to a new graph snapshot |
| 9 | 12:03:30 | Recalculate TGNN risks and rank the top five |
| 10 | 12:04:00 | Display the model-derived spatial risk cluster |
| 11 | 12:05:00 | Apply explicit recovery telemetry, reducing measured load and damage |
| 12 | 12:05:30 | Recalculate risk and display deltas |

Step 10 is deliberately described as a spatial risk pattern, not a confirmed
functional cascade. This tile has 322 spatial edges and no power, telecom, or
other dependency-provider nodes, so its graph has zero dependency edges. The
complete machine-readable transition contract is in `timeline.json`.

## Implementation order from here

1. Connect the dashboard to the completed scenario controller's
   start/pause/resume/reset/speed/status operations.
2. Connect the telemetry Kafka consumer to the validated observation adapter
   and persistent graph snapshot service.
3. Run the full Kafka, MinIO, Spark, YOLO, graph, and TGNN acceptance rehearsal.
4. Capture expected screenshots and freeze the release commit/tag.

The runner is implemented in `scenario_runtime/` with the executable entry
point `python -m scripts.run_scenario`. It emits only raw inputs through normal
producer schemas. Graph snapshots, TGNN predictions, risk clusters, and
dashboard effects remain downstream-derived outputs.

## Validation commands

```powershell
.venv\Scripts\python.exe -m scripts.validate_scenario scenarios\louisiana_east_flood\scenario.json
.venv\Scripts\python.exe -m scripts.validate_scenario scenarios\louisiana_east_flood\scenario.json --strict
.venv\Scripts\python.exe -m pytest -q
```

The strict command validates the frozen registrations and provenance. Pass the
external root options shown in the scenario README to additionally verify every
raw source file by SHA-256.
