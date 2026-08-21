# Integration validation report

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

## Preserved recorded metrics

| Precision | Recall | mAP@50 | mAP@50-95 |
| ---: | ---: | ---: | ---: |
| 0.44918 | 0.30059 | 0.25701 | 0.11756 |

These values are the original final row in the preserved `results.csv`, not a
claim that retraining was performed. Re-evaluation needs the original dataset.
