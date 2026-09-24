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

## Redistribution note

No `LICENSE` file was present in the upstream repository at the reviewed
commit. Before publishing publicly, confirm that the team may redistribute its
code, weights, sample, and training evidence. The full training dataset is not
duplicated here; one test fixture and approximately 19 MB of run evidence are.
