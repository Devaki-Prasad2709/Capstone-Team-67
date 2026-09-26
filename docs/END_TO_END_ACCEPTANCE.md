# End-to-end acceptance evidence

Date: 2026-09-26  
Branch: `dibsei/baseline-blocker-fixes`  
Acceptance runtime: `acceptance-20260926-1101`

## Result

The Louisiana East flood scenario completed repeatedly through the real local
Kafka, MinIO, Spark, YOLO, satellite-change, observation/GIS, TGNN, and
dashboard interfaces. No final observations or model results were injected
directly into the dashboard.

Two replay defects were found and fixed during acceptance:

- The operational view mixed unrelated historical Kafka records into the
  scenario. Operational AI, telemetry, social, and satellite views are now
  isolated by scenario and projected to the runner's current simulation time.
- Repeated telemetry IDs created equal-timestamp TGNN snapshots. Telemetry
  reconstruction is now idempotent, and scenario reset also resets
  replay-local social review state.

Strict asset validation passed with 9 registered assets, 12 timeline events,
21 GIS IDs, 7 stream events, no pending assets, and verified SpaceNet 8 and
ISBDA source files.

## Layer evidence

| Layer | Observed evidence |
|---|---|
| Scenario runner | Three post-start replays reached `completed`, 12/12 events, 0 upcoming events. One recorded final simulation time was `2026-09-24T12:05:30.640000Z`. |
| Producers/Kafka | Final topic counts were social 45, drone 58, satellite 52, AI 59, telemetry 10, and satellite-change 2. Scenario events used the existing producer contracts. |
| MinIO | `disaster-images` contained 21 drone objects and 22 satellite objects. Replays did not create new objects for exact duplicate content. |
| Spark | Spark restarted with the existing checkpoints, logged `Spark queries started`, consumed later replay and negative-test records, and left 47 social, 57 drone, 53 satellite, 61 AI, and 31 GIS Parquet part files. |
| YOLO | The committed checkpoint identity was `780241f6b42f9f0b0d83a8be8d3168e1ca864a756ff93907e72bbb7f9fe3cbf9`. The original scenario inference produced 18, 10, and 1 detections for the three ISBDA images. |
| Image deduplication | Later drone events were `is_duplicate=true`, `duplicate_method=sha256`, with canonical image IDs. The AI worker emitted `duplicate_skipped` with zero new detections for all three replay images. |
| Satellite analysis | Tile `2_23_44` produced 33 changed cells. The result retained the pre/post object references, footprint, scenario timestamp, and the official 22/40 flooded-feature reference. It remained a broad-area signal with `tgnn_integration=none`. |
| NLP/social | `social-001` entered pending review. Confirmation produced one hotspot; a clean replay rejection produced `REPORTED_FALSE`, zero hotspots, and zero graph observations. Spark also parsed the scenario social record as flood-related. |
| Observation/GIS | The three live YOLO outputs produced 29 accepted damage observations associated with exact OSM/graph IDs, including Estuary Road (`osm-way-791288888`) and the severe-damage target (`osm-way-1064972993`). |
| Graph/TGNN | At degradation, Estuary Road was damage 0.35, stress 1.0, failed, risk 0.7471. After recovery it was damage 0.15, stress 0.7018, operational, risk 0.2723. Scores are documented and displayed as uncalibrated relative risk, not probabilities. |
| Dashboard | The API returned 29 damage observations, 21 risk nodes, the top-five ranking, a highest-risk node, source freshness/provenance, social review state, satellite result, and zero operational errors on normal replay. |

## Acceptance cases

| Case | Outcome |
|---|---|
| Fresh start | Kafka, MinIO, and ZooKeeper became healthy; all workers and the dashboard started against the real services. A probe made during container warm-up initially saw the expected not-ready state; the readiness retry passed. |
| Pause/resume | Paused at 160.320 simulated seconds and remained at exactly 160.320 after a two-second wall-clock wait; resume completed the remaining events. |
| Reset | Reset returned the runner to idle with 0 completed and 12 upcoming events. The dashboard projection returned 0 damage observations, 0 telemetry events, 0 scenario social alerts, and no satellite result until those events replayed. |
| Duplicate images | Exact replays were SHA-256 duplicates and were skipped by YOLO; canonical references remained available. |
| Rejected social report | Pending 1 -> `REPORTED_FALSE`; pending 0, rejected 1, hotspots 0, graph observations 0. |
| Missing event field | A Kafka telemetry event without `damage` was quarantined with `Telemetry event requires damage`. |
| Out-of-order event | A late Kafka event timestamped before the current graph snapshot was quarantined with `Stale telemetry cannot overwrite newer graph state`. |
| Invalid coordinates | A checkpoint-backed AI result with latitude 91 was quarantined with `Scenario GPS is outside WGS84 bounds`. |
| Service restart | Dashboard and Spark were stopped and restarted. The dashboard rebuilt its projection from Kafka, while Spark resumed from its checkpoint and processed subsequent batches. |
| Recovery stage | Damage 0.35 -> 0.15, stress 1.0 -> 0.7018, status failed -> operational, relative risk 0.7471 -> 0.2723. |
| Complete replay | Multiple complete replays reached 12/12 without terminal repair. Duplicate telemetry no longer creates invalid equal-time TGNN snapshots. |

The three deliberately invalid records remained visible as dashboard audit
errors while the accepted graph stayed at the recovered values (damage 0.15,
stress 0.7018, operational, risk 0.2723).

## Automated checks

- Full suite: `115 passed, 2 skipped`.
- Strict scenario validation: passed.
- Docker health at evidence capture: Kafka, MinIO, and ZooKeeper healthy.

The skipped tests are pre-existing optional tests; no acceptance test failed.
