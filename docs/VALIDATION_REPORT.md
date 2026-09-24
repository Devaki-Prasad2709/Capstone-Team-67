# Integration validation report

## Baseline repair validation — 2026-09-24

- `python -m pytest -q`: **53 passed, 2 real-data tests skipped**.
- `python -m scripts.verify_ai_artifacts`: serving YOLO26s checksum and
  embedded checkpoint metrics passed; legacy artifacts also matched hashes.
- Portable real-checkpoint test: tracked validation imagery produced three
  detections and updated the GIS observation graph.
- `python -m core.pipeline_demo`: GIS graph, observations, checkpoint-backed
  TGNN inference, node-ID mapping, and risk overlay passed.
- `python -m pip check`: no broken installed requirements.
- `python -m compileall`: all application packages compiled.
- Live Kafka and MinIO connectivity passed with all five topics.
- Live tracked-image path passed: producer -> MinIO -> `drone-video` ->
  YOLO26s -> `ai-analysis-results` (`analyzed`).

The two skipped tests require an external SpaceNet8 dataset and are designed
to skip when `SPACENET8_TEST_ROOT` is not configured.

Validated on 2026-08-21 using Python 3.10, Java 17, Spark 3.5.5, Kafka,
MinIO, Ultralytics 8.4.126, and PyTorch 2.13.0.

## Passed checks

- `python -m unittest discover -v`: **19/19 passed**.
- `python -m scripts.verify_ai_artifacts`: checkpoint and results checksums
  passed; epoch-50 metrics exactly matched the manifest.
- `python -m pip check`: no broken requirements.
- `node --check dashboard/static/app.js`: frontend syntax passed.
- Dashboard backend import: 14 routes loaded.
- Dashboard AI API: model available, Kafka results readable, no service errors.
- Real `best.pt` CPU smoke inference: three detections on the included test
  image (two class 0 and one class 1 at confidence threshold 0.25).
- MinIO upload/download connectivity passed.
- Kafka connectivity and all five canonical topics passed.
- Live MinIO -> `drone-video` -> YOLO -> `ai-analysis-results` run passed with
  status `analyzed` and three detections.
- Live `ai-analysis-results` -> Spark -> AI Parquet passed; the dashboard read
  back the nested classes, bounding box, confidence, Kafka metadata, and
  timestamps without serialization errors.

## Legacy validation metrics from 2026-08-21

| Precision | Recall | mAP@50 | mAP@50-95 |
| ---: | ---: | ---: | ---: |
| 0.44918 | 0.30059 | 0.25701 | 0.11756 |

These values are the original final row in the legacy YOLOv8 `results.csv`, not
the serving YOLO26s metrics. The serving metrics are documented in
`AI_INTEGRATION.md` and verified directly from `best.pt`.
