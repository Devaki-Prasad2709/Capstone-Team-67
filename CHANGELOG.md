# Changelog

## 4.0 - Scenario completion and repository cleanup

- Added the frozen Louisiana flood scenario, controller, satellite change
  analysis, GIS association, temporal graph state, TGNN ranking, social review,
  operational dashboard, acceptance suite, and measured evaluation package.
- Removed tracked raw training data, duplicate Ultralytics runs, hard-coded
  legacy implementations, stale DVC metadata, and obsolete compose/config files.
- Consolidated pinned runtime, AI, and development dependency entry points.
- Added repository/model/import verification and clean-clone acceptance tests.
- Pinned checkout line endings so byte-frozen scenario and model artifacts keep
  stable SHA-256 identities on Windows.
- Added one authoritative final system document covering architecture,
  contracts, operation, evaluation boundaries, deployment, and troubleshooting.

## 3.0 - Integrated computer vision and GIS pipeline

- Integrated the Capstone-Team-67 preprocessing and trained YOLOv8 Nano model.
- Preserved checkpoints, training evidence, recorded metrics, and provenance.
- Added a Kafka/MinIO AI worker and `ai-analysis-results` topic.
- Added Spark validation and Parquet persistence for AI results.
- Added live AI controls, model metrics, results, and logs to the dashboard.
- Ported COCO conversion, deterministic dataset splitting, label visualization,
  training, evaluation, and inference to configurable CLI tools.
- Integrated the GIS imagery producer and Spark output.
- Added artifact-integrity and AI contract/preprocessing regression tests.
- Added branch/push, model-lineage, and validation documentation.

## 2.0 - Efficient image pipeline

- Added switchable Base64 and S3-compatible object-storage transfer.
- Added pinned MinIO service for local/Tailscale demonstrations.
- Added persistent SHA-256 exact duplicate detection.
- Added configurable pHash near-duplicate detection for drone frames.
- Added canonical duplicate events without retransmitting image bytes.
- Updated the consumer for Base64, presigned URL, and S3 object download.
- Updated Spark schemas and Parquet metadata for object references and duplicates.
- Added object-storage connectivity and deduplication-report scripts.
- Expanded the README with complete one-system, LAN, Tailscale, MinIO, S3, and AI-layer instructions.
