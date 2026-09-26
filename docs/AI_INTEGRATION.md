# AI integration and model lineage

The computer-vision functionality was adapted from
<https://github.com/Devaki-Prasad2709/Capstone-Team-67> at commit
`e06e69aa7873ad95803378cc33e40cb546406cc0`.

## Retained functionality

- Image validation, resize, frame sampling, motion filtering, blur filtering,
  bounded pHash near-duplicate filtering, and batching behavior.
- YOLO26s serving inference for `Slight`, `Severe`, and `Debris` using
  `best.pt`.
- A legacy YOLOv8 `last.pt`, `results.csv`, and plots are retained as
  historical evidence and are not used by the worker.

Hard-coded `E:/...` paths were replaced with settings and CLI arguments. The
new worker reads Kafka/MinIO events and publishes `ai-analysis-results`.
Producer-side SQLite deduplication stays authoritative, so the AI worker's
second pHash filter is disabled by default.

## Metric preservation

| Precision | Recall | mAP@50 | mAP@50-95 |
| ---: | ---: | ---: | ---: |
| 0.36917 | 0.28202 | 0.25171 | 0.10874 |

`python -m scripts.verify_ai_artifacts` verifies SHA-256 for all preserved
artifacts, then compares the serving checkpoint's embedded `train_metrics`
with the manifest. A new evaluation requires the original dataset and can
vary with dependencies, hardware, or dataset contents.

```text
drone-video -> AI worker -> ai-analysis-results -> Spark -> storage/processed/ai
     |              |
     +-> MinIO -----+
```

Results have one of four statuses: `analyzed`, `filtered`,
`duplicate_skipped`, or `error`.

For scenario events, every live detection retains its image/object reference,
class, confidence, bounding box, scenario timestamp, explicitly simulated GPS,
and declared GIS target. The result also records the SHA-256 identity of the
checkpoint loaded by the worker, `live-checkpoint-inference`, and
`prerecorded_output=false`. The observation consumer rejects scenario results
that omit those provenance fields and accepts simulated coordinates only with
the explicit `--allow-scenario-simulated-gps` switch.

```powershell
python -m consumers.observation_consumer `
  --gis-path scenarios\louisiana_east_flood\gis\infrastructure.geojson `
  --allow-scenario-simulated-gps
```

## Redistribution note

No `LICENSE` file was present in the upstream repository at the reviewed
commit. Before publishing publicly, confirm that the team may redistribute its
code, weights, sample, and training evidence. The full training dataset is not
duplicated here; one test fixture and approximately 19 MB of run evidence are.
