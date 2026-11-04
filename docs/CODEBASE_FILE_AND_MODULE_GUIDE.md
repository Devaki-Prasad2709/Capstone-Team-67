# Codebase file and module guide

This guide explains what the repository contains, why each file or file family
exists, what reads it, and whether it is authoritative source code, an input
contract, model evidence, a test, or a generated runtime location.

## 1. System in one picture

```text
Real datasets                         Frozen scenario metadata
CrisisMMD / ISBDA / SpaceNet / OSM   JSON + GeoJSON timeline and provenance
              \                         /
               data-source producers / scenario runner
                          |
                  SHA-256 and pHash
                     /          \
              MinIO objects    Kafka events
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
   Spark validation       YOLO drone worker      Satellite change worker
   and Parquet output          |                       |
                              observations       broad-area GeoJSON
                                  |
                        GIS association and
                       temporal graph snapshots
                                  |
                         TGNN relative risk
          +-----------------------+-----------------------+
                                  |
                     FastAPI/MapLibre dashboard
```

The repository deliberately keeps satellite evidence separate from the graph
and TGNN. Drone detections and telemetry may update graph state after validation
and GIS association; satellite change is a broad-area dashboard overlay only.

## 2. File-format concepts

### Python (`.py`)

Python files contain executable logic. A file can be imported as a module, and
many files with a `main()` function can also be run using `python -m ...`.
`__init__.py` marks a directory as a Python package. An empty `__init__.py` is
therefore intentional: it provides package/import structure, not missing code.

### YAML (`.yaml`)

YAML is a human-readable configuration format. In this repository the tracked
YOLO YAML is:

- `ai/computer_vision/artifacts/drone_detector/args.yaml`: the exact arguments
  recorded by Ultralytics for the completed training run—model type, task,
  epochs, image size, optimizer settings, augmentation, device, batch size and
  original paths. It is valuable for reproducibility and audit, but the live
  worker does **not** read it to perform inference.

YOLO training also requires an external dataset `data.yaml`. That file maps
train/validation/test image locations and numeric class IDs to class names.
`train.py` and `evaluate.py` accept its path with `--data`. It is not committed
because the raw dataset is external. Without the correct dataset YAML, labels
such as class `3` can be interpreted incorrectly even if the images exist.

The former `ai/computer_vision/configs/training.yaml` was an unused, zero-byte
placeholder and has been removed. Runtime AI settings—checkpoint path,
confidence, image size, device and preprocessing thresholds—come from `.env`.

### JSON (`.json`)

JSON files are machine-validated contracts and manifests. Scenario JSON tells
the runner what to publish and when. Artifact manifests bind model files to
checksums, architectures, class semantics and limitations. Unlike YAML training
arguments, these manifests are actively checked by verification code.

### GeoJSON (`.geojson`)

GeoJSON stores geographically located features. The scenario GIS file is the
frozen structural truth used to build graph nodes. Dashboard overlays are also
GeoJSON because browsers and MapLibre can render it directly. Coordinates in
dashboard contracts are WGS84 longitude/latitude (EPSG:4326); distance and
nearest-feature calculations use a suitable projected working CRS.

### PyTorch checkpoints (`.pt`, `.pth`)

- `best.pt` is the selected YOLO serving checkpoint and contains learned
  weights plus Ultralytics metadata.
- `last.pt` is the final training-epoch checkpoint retained only for lineage;
  production inference does not select it.
- `tgnn.pth` contains the TGNN `state_dict`. Its architecture must be recreated
  by `tgnn/models/tgnn.py` before the weights are loaded.

Checkpoints are not source code and are not understandable without their model
class, feature order, class mapping and manifest.

### CSV, PNG and JPG evidence

`results.csv` contains epoch-by-epoch YOLO training metrics. Training curves,
confusion matrices and batch images visually document the run. They do not
drive inference. The JPG under `samples/` is a lightweight inference example.

### Markdown (`.md`)

Markdown files are documentation, validation evidence and operating guides.
They should describe actual behavior but are not executed by the application.

## 3. Root files

| File | Purpose and importance |
|---|---|
| `.env.example` | Safe configuration template for Kafka, dataset paths, MinIO, deduplication, Spark, scenario paths and AI thresholds. Copy it to the untracked `.env`; never commit real credentials. |
| `.gitignore` | Prevents raw datasets, local secrets, databases, logs, caches, Spark output and arbitrary model downloads from entering Git while explicitly allowing registered artifacts. |
| `.gitattributes` | Controls line endings and protects checksum-sensitive frozen files from accidental Windows conversion. |
| `docker-compose.yml` | Defines ZooKeeper, Kafka and MinIO, their ports, health checks, persistence and environment-driven credentials. This is local infrastructure, not application processing logic. |
| `requirements.txt` | Pinned base runtime: Kafka, MinIO/S3, Spark, GIS, raster, image and dashboard dependencies. |
| `requirements-ai.txt` | Includes the base runtime and adds PyTorch, PyTorch Geometric, TorchVision and Ultralytics. |
| `requirements-dev.txt` | Includes the complete AI environment plus pytest. Use this for development and full validation. |
| `README.md` | Main setup, run, architecture and troubleshooting entry point. |
| `CHANGELOG.md` | Human-readable history of significant repository changes. |

## 4. Shared configuration and transport modules

| File | What it does |
|---|---|
| `config/__init__.py` | Declares the configuration package. |
| `config/settings.py` | Loads `.env`, converts string settings to typed booleans/numbers/paths, exposes one shared `Settings` object, creates component loggers and rejects missing dataset directories. This is the central runtime configuration authority. |
| `common/__init__.py` | Declares shared helpers. |
| `common/topics.py` | Defines canonical Kafka topic names (`social-posts`, `drone-video`, `satellite-imagery`, `gis-data`, `ai-analysis-results`, `infrastructure-telemetry`, and `satellite-change-results`). Centralization prevents producer/consumer spelling drift. |
| `common/kafka_utils.py` | Builds JSON Kafka producers with configured message limits and provides a send-and-log helper. |
| `common/object_storage.py` | Wraps the S3 API used by MinIO: bucket creation, upload, download, listing and presigned URLs. `StoredObject` is the durable reference returned after upload. |
| `common/image_transfer.py` | Implements the switch between Base64-in-Kafka and object-storage references. It adds the same event fields in either mode so downstream consumers do not need separate contracts. |
| `common/deduplication.py` | Computes SHA-256 for byte-identical images and a 64-bit DCT pHash for visually similar images. `ImageDeduplicator` stores canonical fingerprints in SQLite, uses atomic transactions and returns why an event is a duplicate. pHash is normally enabled for drone imagery but not satellite pairs. |

SHA-256 is exact: one changed byte produces a different digest. pHash reduces an
image to its low-frequency visual structure and compares hashes by Hamming
distance, so resizing or JPEG recompression can still match. This avoids paying
for repeated inference while retaining a canonical audit reference.

## 5. Upstream data producers

| File | What it does |
|---|---|
| `data_source/__init__.py` | Labels the package as replayed static datasets acting like live sources. |
| `data_source/social/__init__.py` | Declares the CrisisMMD source package. |
| `data_source/social/social_producer.py` | Recursively finds TSVs, standardizes tweet ID/text/timestamp/hazard fields, skips unusable rows and publishes timed `social-posts` events. Social records have no image payload. |
| `data_source/drone/__init__.py` | Declares the ISBDA source package. |
| `data_source/drone/drone_producer.py` | Discovers JPG/PNG images, decodes them for pHash, performs persistent deduplication, transfers unique bytes through Base64 or MinIO and publishes the `drone-video` contract. Duplicate events retain canonical IDs and are still auditable. |
| `data_source/satellite/__init__.py` | Declares the general satellite producer package. |
| `data_source/satellite/satellite_producer.py` | Discovers TIFF/JPEG/PNG imagery. TIFF pixels are converted to display/transport JPEG bytes while real SpaceNet source provenance remains in scenario metadata. It performs exact deduplication and publishes `satellite-imagery`. Near-dedup is avoided because subtle pre/post differences are meaningful. |
| `data_source/gis/__init__.py` | Declares the optional GIS imagery producer. |
| `data_source/gis/gis_producer.py` | Replays map-image files through the same deduplication, MinIO/Base64 and Kafka pattern on `gis-data`. This is different from the structural GeoJSON loader used by the graph. |

## 6. Spark layer

Spark is schema-based preprocessing, not AI. It parses Kafka JSON, rejects
invalid records, derives timestamps/status fields, prints stream output and
writes restartable Parquet streams. It does not run YOLO or TGNN.

| File | What it does |
|---|---|
| `spark/__init__.py` | Declares the Spark package. |
| `spark/spark_streaming.py` | Creates the Spark session, subscribes to the configured Kafka topics once, splits records by topic, calls the processors below and starts console and Parquet queries with independent checkpoints. |
| `spark/social_processor.py` | Parses the social schema, normalizes text, matches configured disaster keywords and produces aggregate counts by event/hazard. Keyword matching is rule-based. |
| `spark/drone_processor.py` | Validates drone metadata, timestamps and transfer/dedup fields; marks unique records `ready_for_ai` and duplicates `duplicate_skipped`. It deliberately drops bulky Base64 image data from Parquet. |
| `spark/satellite_processor.py` | Validates satellite metadata, transport references and scenario pre/post provenance; emits processing state without performing change analysis. |
| `spark/gis_processor.py` | Validates GIS imagery references and derives ready/duplicate state. |
| `spark/ai_processor.py` | Parses YOLO result metadata and nested detections, including confidence, boxes/polygons, GPS provenance, target association, scenario IDs and checkpoint hash; timestamps the durable AI output. |

## 7. Computer-vision and preprocessing layer

### Executable modules

| File | What it does |
|---|---|
| `ai/__init__.py` | Declares the AI layer. |
| `ai/preprocessing/__init__.py` | Exports reusable preprocessing components. |
| `ai/preprocessing/pipeline.py` | Validates/decode frames and composes sampling, batching, motion filtering, blur-quality filtering and optional in-memory pHash filtering. It returns an explicit accepted/rejected reason rather than silently discarding frames. Producer-level SQLite dedup and worker-level preprocessing solve different problems. |
| `ai/computer_vision/__init__.py` | Declares the YOLO package. |
| `ai/computer_vision/detector.py` | Lazily loads the configured Ultralytics `best.pt`, runs inference at the configured size/confidence/device and converts boxes and segmentation polygons into serializable detections. Lazy loading keeps imports/tests lightweight. |
| `ai/computer_vision/contracts.py` | Constructs the stable `ai-analysis-results` event. It preserves image reference, checkpoint hash, live-inference provenance, detections, scenario time, GPS provenance and target association. |
| `ai/computer_vision/worker.py` | Long-running Kafka consumer: reads unique drone events, downloads MinIO bytes or decodes Base64, preprocesses the image, runs `DamageDetector`, publishes the result and emits explicit skip/failure contracts. It never uses prerecorded scenario detections. |
| `ai/computer_vision/infer.py` | CLI for running the serving model on one local image; useful for demos and debugging outside Kafka. |
| `ai/computer_vision/train.py` | Portable Ultralytics training CLI. Requires an external dataset `data.yaml`; writes a new training run rather than changing the serving checkpoint automatically. |
| `ai/computer_vision/evaluate.py` | Evaluates a selected checkpoint against a dataset YAML/split and reports Ultralytics metrics. |
| `ai/computer_vision/requirements.txt` | Compatibility include pointing to the authoritative root `requirements-ai.txt`. |

### Dataset tools

| File | What it does |
|---|---|
| `ai/computer_vision/tools/__init__.py` | Declares the portable tools package. |
| `tools/convert_coco_to_yolo.py` | Converts COCO bounding-box annotations into normalized YOLO label text files. Class-offset handling matters because COCO category IDs need not start at zero. |
| `tools/prepare_dataset.py` | Creates deterministic train/validation/test directory splits from image and label folders. The seed makes the split reproducible. |
| `tools/visualize_labels.py` | Draws YOLO labels on an image so class indices and normalized coordinates can be visually checked before training. |

### Model and training evidence

| File or family | What it means |
|---|---|
| `artifact_manifest.json` | Authoritative YOLO lineage: architecture, RescueNet classes, Ultralytics version, metrics, held-out metrics and SHA-256 hashes. Verification scripts use it to detect artifact replacement/corruption. |
| `artifacts/drone_detector/args.yaml` | Ultralytics snapshot of the completed training configuration. Important for reproducibility; not read by the worker. Original absolute paths are historical provenance and are not expected to work on another machine. |
| `weights/best.pt` | Production serving checkpoint selected by validation performance and referenced by `.env`. |
| `weights/last.pt` | Last epoch checkpoint retained for lineage/comparison only. |
| `results.csv` | Per-epoch losses and metrics used to inspect convergence and create plots. |
| `results.png` | Combined training/validation metric curves. |
| `BoxF1_curve.png`, `BoxPR_curve.png`, `BoxP_curve.png`, `BoxR_curve.png` | Bounding-box F1, precision-recall, precision and recall curves across thresholds. |
| `MaskF1_curve.png`, `MaskPR_curve.png`, `MaskP_curve.png`, `MaskR_curve.png` | Equivalent curves for segmentation masks. |
| `confusion_matrix.png`, `confusion_matrix_normalized.png` | Raw and normalized class-confusion views for error analysis. |
| `labels.jpg` | Dataset label distribution/geometry visualization produced by Ultralytics. |
| `train_batch0.jpg`, `train_batch1.jpg`, `train_batch2.jpg` | Early augmented training batches used to inspect labels and augmentation. |
| `train_batch323550.jpg`, `train_batch323551.jpg`, `train_batch323552.jpg` | Late-run training batch visualizations. |
| `val_batch0_labels.jpg`, `val_batch1_labels.jpg`, `val_batch2_labels.jpg` | Validation images with ground-truth labels. |
| `val_batch0_pred.jpg`, `val_batch1_pred.jpg`, `val_batch2_pred.jpg` | Matching validation images with predictions for qualitative comparison. |
| `samples/10_1530.jpg` | Small committed input for local inference smoke tests; it is not training evidence or a scenario source asset. |

## 8. Satellite normalization and broad-area change

| File | What it does |
|---|---|
| `core/satellite/normalizer.py` | Universal PRE/POST adapter. It validates files, CRS, transforms, sensor/band compatibility and NoData; finds true geographic intersection; chooses a working CRS/resolution; uses a fast path for already aligned rasters; otherwise reprojects both onto one grid; creates a combined valid mask; normalizes required optical bands for the classifier; and preserves provenance. Domain-specific exceptions explain which requirement failed. It never scores change. |
| `core/satellite/broad_area_classifier.py` | Owns change scoring. It divides the normalized overlap into cells, computes valid-pixel absolute-difference scores, marks low-coverage cells `unknown`, assigns severity and transforms cell polygons to WGS84 GeoJSON. |
| `core/satellite/change_contract.py` | Validates satellite event fields, accumulates out-of-order PRE/POST events into one pair and builds the dashboard result contract with summaries, source references and architectural non-integration markers. |
| `core/satellite/change_worker.py` | Consumes `satellite-imagery`, loads source bytes from MinIO/Base64, pairs phases, calls the universal normalizer/classifier and publishes `satellite-change-results`. Errors propagate as evidence errors instead of fabricated change. |
| `core/satellite/spacenet_adapter.py` | Backward-compatible SpaceNet entry points. SpaceNet is now one supported optical raster source; this wrapper delegates to the universal normalizer and generic classifier. |

The normalized data is an aligned numeric array plus mask and geospatial
metadata. The dashboard result is GeoJSON; it is not converted back into TIFF.
The source TIFFs remain source evidence, while browser previews can be JPEG.

## 9. GIS, observation and graph-state core

### GIS and graph construction

| File | What it does |
|---|---|
| `core/gis/gis_loader.py` | Reads the accepted GeoJSON schema, validates feature types, converts WGS84 geometry to a metric working CRS, chooses an appropriate local UTM CRS and returns typed roads, buildings and infrastructure points. |
| `core/gis/building_association.py` | Builds spatial indexes and maps a detection point to an containing building or nearby road with deterministic tie-breaking. The result includes source GIS ID and graph node ID for traceability. |
| `core/gis/building_classification.py` | Stores manual building types in an append-only SQLite revision ledger with operator, timestamp and notes. It overlays the latest classification without modifying frozen OSM GeoJSON. |
| `core/gis/fixtures/demo_gis.geojson` | Small 17-feature synthetic/fixture GIS used by tests and local demonstrations; it is not the canonical Louisiana scenario snapshot. |
| `core/graphs/gis_graph_builder.py` | Converts loaded GIS features into a directed NetworkX graph, assigns baseline capacity/load/state, preserves source IDs, adds spatial edges and adds service-dependency edges only when type and distance rules justify them. |

### Observation ingestion and temporal state

| File | What it does |
|---|---|
| `core/observation/geolocator.py` | Defines the detection/geolocation interface and implementations for no location, manual overrides and explicitly simulated scenario GPS. This prevents simulated coordinates being mistaken for sensor metadata. |
| `core/observation/ingest.py` | Validates YOLO result/detection contracts, normalizes model class names, derives stable observation IDs, geolocates detections, performs GIS association and appends accepted records idempotently. |
| `core/observation/observation_log.py` | Stores immutable observation records and calculates time-decayed per-node damage evidence. Severity mapping converts accepted CV classes into graph damage contributions. |
| `core/observation/telemetry.py` | Validates telemetry ID, target, timestamp, load/capacity/damage and recovery semantics; applies updates only to the stable mapped graph node. Only explicit recovery may reduce damage. |
| `core/observation/state_update.py` | Deep-copies snapshots, aggregates observations, calculates intrinsic/effective capacity, utilization, stress, status and freshness, propagates dependency availability to a fixed point and produces field-level before/after explanations. The baseline graph remains immutable. |

### Overlays and demonstrations

| File | What it does |
|---|---|
| `core/overlay/gis_overlay.py` | Converts graph nodes and optional TGNN scores into dashboard-ready WGS84 GeoJSON with state/freshness properties. |
| `core/overlay/building_overlay.py` | Joins frozen building footprints with the latest manual classifications and graph IDs for map rendering. |
| `core/pipeline_demo.py` | Standalone core-layer demonstration that builds GIS/graph state, applies sample evidence and exercises model integration without the full streaming stack. It is a diagnostic/demo entry point, not the scenario runner. |
| `core/README.md` | Explains the merged graph/GIS/observation subsystem and its usage. |
| Empty `core/**/__init__.py` files | `core`, `gis`, `graphs`, `integration`, `observation` and `overlay` package markers. They are intentionally empty. |

## 10. Social/NLP workflow

| File | What it does |
|---|---|
| `core/nlp/landmark_alert_resolver.py` | Rule-based distress detection plus landmark/type resolution against known GIS names and positions. It avoids negated distress phrases and returns resolution confidence/provenance. It is NLP in the broad sense, but not a trained language model. |
| `core/nlp/alert_state.py` | Auditable state machine and store for incoming, pending review, confirmed and reported-false alerts. It enforces legal transitions and preserves responder decisions. |
| `core/nlp/social_alert_workflow.py` | Connects social event normalization, duplicate/contradiction detection, landmark resolution, review state, confirmed orange hotspots and human-evidence graph observations. Pending/rejected reports cannot alter confirmed state. |
| `core/nlp/ALERT_STATE.md` | Documents the social state model and safety rules. |

## 11. TGNN layer

| File | What it does |
|---|---|
| `tgnn/models/tgnn.py` | Defines the neural architecture: node-type embeddings, two graph-attention layers using edge attributes, residual connection, GRU across time and an MLP output per node/timestep. It excludes current `status` from inputs because status is the next-step training target. |
| `tgnn/utils/helpers.py` | Converts NetworkX graphs to PyTorch Geometric tensors in the exact feature order `x, y, load, capacity, damage, stress, status`; maps node types and encodes edge weight/delay/type. |
| `core/integration/pyg_bridge.py` | Production-grade sequence bridge: uses one position extent for all snapshots and validates stable node order, edge order, tensor dimensions and temporal consistency. |
| `core/integration/tgnn_predictor.py` | Validates checkpoint checksum/manifest, loads the model, performs finite inference, converts operational logits to relative failure-risk scores, ranks nodes and maps tensor rows back to graph and original GIS IDs. It warns that scores are uncalibrated. |
| `tgnn/graphs/synthetic_graph_builder.py` | Generates random typed infrastructure graphs for training; this is why the checkpoint is not a Louisiana-calibrated probability model. |
| `tgnn/graphs/simulator.py` | Simulates load/damage, dependency influence, failures and stochastic recovery to create temporal training labels. |
| `tgnn/training/train.py` | Trains the TGNN on generated sequences using next-timestep operational status and writes `tgnn.pth`. |
| `tgnn/inference/predict.py` | Standalone synthetic inference/debug script that injects stress, simulates snapshots and prints risk rankings. Production dashboard inference uses `core/integration/tgnn_predictor.py` instead. |
| `tgnn/models/checkpoint_manifest.json` | Authoritative architecture, checksum, feature order, target encoding and limitation contract. It explicitly says scores are not calibrated probabilities. |
| `tgnn/models/tgnn.pth` | Committed trained weights loaded only after manifest/checksum verification. |
| `tgnn/requirements.txt` | Compatibility include pointing to root AI dependencies. |
| Empty `tgnn/**/__init__.py` files | Package markers for root, graphs, inference, models, training and utils. |

## 12. Scenario package and runner

### Runtime modules

| File | What it does |
|---|---|
| `scenario_runtime/__init__.py` | Exposes scenario runtime types. |
| `scenario_runtime/controller.py` | Thread-safe simulation clock and state machine with start, pause, resume, reset, speed, current event, completed events and upcoming events. Dispatch failures become visible controller errors. |
| `scenario_runtime/publisher.py` | Reads registered scenario assets/events and builds ordinary production wire contracts. It loads real image bytes, attaches explicit simulated fields and routes only through Kafka/MinIO producer interfaces. |
| `scripts/run_scenario.py` | CLI wrapper around the controller/dispatcher, including interactive controls and status display. |

### Louisiana scenario files

| File | What it defines |
|---|---|
| `scenarios/louisiana_east_flood/README.md` | Human guide to provenance, readiness, validation and replay. |
| `scenario.json` | Master manifest: scenario identity, Louisiana boundary/clock, nine registered assets, bindings and links to timeline files. This is the scenario package entry point. |
| `timeline.json` | Locked 12-step story. Every step states input, timestamp, target, expected graph effect and expected dashboard effect, including deliberately derived/non-published steps. |
| `drone/selection.json` | Registers three real ISBDA images, checksums, original paths/classes, model selection evidence and explicitly simulated Louisiana GPS/timestamps. |
| `drone/.gitkeep` | Keeps the empty directory in Git; raw licensed images stay on the external drive. |
| `drone_events.json` | Three stream envelopes targeting Slight, Severe and Debris scenario observations. |
| `satellite/pair.json` | Registers SpaceNet 8 tile `2_23_44`: real PRE/POST paths/checksums, annotation, CRS, footprint and 22-of-40 flood-label reference. |
| `satellite/README.md` | Explains where external SpaceNet files belong and how their provenance is handled. |
| `gis/infrastructure.geojson` | Frozen OSM snapshot with 21 stable roads/buildings for the exact scenario boundary. It is the structural graph source and must not be edited by manual classification. |
| `social_events.json` | One simulated distress report and its explicit simulated fields/location. |
| `responder_decisions.json` | Primary confirmation event plus the alternative rejection branch used to demonstrate distinct behavior. |
| `telemetry.json` | Two simulated telemetry events: road degradation and explicit recovery. |
| `expected_results.json` | Machine-readable acceptance statements and non-claims used to keep evaluation honest. |

## 13. Dashboard

### Backend

| File | What it does |
|---|---|
| `dashboard/server.py` | FastAPI application and REST routes for health, process controls, logs, Spark output, AI output, satellite evidence, social review, buildings, MinIO previews, operational map and scenario controls. It serves the static UI and API docs. |
| `dashboard/process_manager.py` | Starts/stops named local subprocesses, records PIDs/log paths and prepares the Windows Java/Spark environment. It does not manage Docker containers internally. |
| `dashboard/services.py` | Read-only adapters for Docker status, Kafka counts/events, MinIO inventory, dedup statistics, Parquet preview and AI manifest information. |
| `dashboard/map_api.py` | Computes scenario map center/extent and returns the MapLibre/OpenStreetMap basemap contract. |
| `dashboard/operational_service.py` | Replays accepted evidence into immutable snapshots, invokes TGNN inference and builds the incident-first layers, top-five risk explanation, freshness and provenance response. |
| `dashboard/social_alert_service.py` | Reads social Kafka records exactly once per offset and exposes pending/confirmed/rejected workflow state to the dashboard. |
| `dashboard/building_classification_service.py` | Loads frozen GIS and persistent classification ledger for list/detail/revision API operations. |
| `dashboard/scenario_service.py` | Owns the dashboard's controller thread and translates API lifecycle commands into scenario controller operations. |
| `dashboard/__init__.py` | Declares the dashboard package. |

### Frontend

| File | What it does |
|---|---|
| `dashboard/static/index.html` | Accessible page structure: navigation, map, risks, social review, satellite evidence, timeline, process controls, Spark tables, AI metrics, storage browser, classifications and logs. |
| `dashboard/static/app.js` | Browser controller. Calls API routes, initializes MapLibre layers, renders risk/damage/hotspot/satellite evidence, handles confirmation/classification/control actions and periodically refreshes state. |
| `dashboard/static/styles.css` | Global tokens, layout, navigation, cards, tables, process controls and responsive rules. |
| `dashboard/static/operations.css` | Incident map, risk ranking, provenance, timeline and operational overview styling. |
| `dashboard/static/ai.css` | AI metrics/results styling. |
| `dashboard/static/building.css` | Building classification form and revision-history styling. |
| `dashboard/static/satellite.css` | PRE/POST evidence cards and severity-grid styling. |
| `dashboard/static/social.css` | Pending/rejected alert cards, controls and hotspot/audit styling. |

## 14. Consumers and operator tools

| File | What it does |
|---|---|
| `consumer/interactive_consumer.py` | Human-facing Kafka consumer that lets an operator select topics, prints social data and reconstructs Base64 or MinIO images into `storage/received_images`. Useful for transport verification, not graph mutation. |
| `consumers/observation_consumer.py` | Production adapter from `ai-analysis-results` to the observation log and temporal graph. It validates finite/provenance fields, loads GIS, associates detections and keeps duplicate processing idempotent. |
| `consumer/__init__.py` | Marks remote/operator consumers as a package. |

The old zero-byte `consumers/test_consumer.py` was not a test implementation and
had no callers; it was removed during cleanup.

## 15. Scripts

| File | Purpose |
|---|---|
| `scripts/configure_windows.ps1` | Creates/updates local environment settings for Windows networking and dataset locations. |
| `scripts/setup_spark_windows.ps1` | Installs/configures the project-local Hadoop helper needed by Spark on Windows. |
| `scripts/run_spark.ps1` | Supported Windows Spark launcher with Java/Hadoop environment preparation. |
| `scripts/run_spark.sh` | Linux/macOS shell equivalent. |
| `scripts/open_kafka_firewall.ps1` | Adds the Windows firewall rule needed when another authorized machine connects to Kafka. |
| `scripts/create_topics.py` | Idempotently creates every canonical Kafka topic with explicit configuration. |
| `scripts/test_kafka.py` | Connectivity/topic smoke test; it does not run the pipeline. |
| `scripts/test_object_storage.py` | Upload/presign/download MinIO smoke test. |
| `scripts/dedup_report.py` | Read-only totals from the SQLite fingerprint registry. |
| `scripts/fetch_osm_scenario_gis.py` | Queries Overpass for one bounding box and converts accepted OSM roads/buildings/infrastructure into the project GeoJSON schema while preserving source IDs. Running it changes the frozen source and therefore requires deliberate review. |
| `scripts/validate_scenario.py` | Checks schema, timestamps, cross-file IDs, bounding boxes, provenance, checksums and strict availability of external SpaceNet/ISBDA assets. |
| `scripts/verify_ai_artifacts.py` | Verifies YOLO hashes and embedded metrics against the artifact manifest. |
| `scripts/verify_segmentation_result.py` | Compares one predicted segmentation geometry with its YOLO polygon label for focused evidence. |
| `scripts/verify_repository.py` | Clean-clone gate: rejects tracked generated/raw files, verifies model artifacts/scenario structure/dependencies and imports runtime libraries. |
| `scripts/verify_satellite_e2e.py` | Runs real SpaceNet images through MinIO, Kafka, change worker and dashboard API, then verifies timestamps, keys, bounds, finite scores, unknown coverage and the dashboard-only boundary. |
| `scripts/generate_evaluation_results.py` | Reads live topics/checkpoints, reconstructs graph/TGNN/cascade/social/dedup/satellite metrics, measures latency and writes evaluation evidence/screenshots. It reports results; it is not an operational consumer. |

## 16. Tests

Each test file protects a distinct contract:

| File | Contract protected |
|---|---|
| `tests/__init__.py` | Test package marker. |
| `test_ai.py` | Preprocessing, dataset tools, AI contracts, artifact integrity and dashboard serialization. |
| `test_yolo26s_ingest.py` | Real serving-checkpoint output can enter the observation contract with required provenance. |
| `test_segmentation_contract.py` | Segmentation polygons/classes remain valid and serializable. |
| `test_building_classification.py` | Classification persistence, revision history and GIS/graph/dashboard retrieval. |
| `test_coordinate_scale.py` | Diagnostic position normalization and TGNN response across coordinate scales. |
| `test_core.py` | Topic selection, social transforms, image decoding, SHA/pHash dedup and transfer modes. |
| `test_geolocation_association.py` | Inside-building, near-road, tie, no-candidate, invalid/boundary and duplicate association behavior. |
| `test_gis_crs.py` | CRS choice, reprojection and WGS84/working-coordinate correctness. |
| `test_gis_graph_builder.py` | Stable GIS IDs, node creation, topology and dependency/spatial edge rules. |
| `test_graph_state_cascade.py` | Damage/stress equations, dependency propagation, recovery, freshness and immutable snapshots. |
| `test_ingest.py` | AI result validation, class normalization, observation creation and idempotency. |
| `test_nlp_alert_state.py` | Legal alert-state transitions and audit persistence. |
| `test_social_alert_workflow.py` | Pending versus confirmed/rejected behavior, duplicates, contradictions and hotspots. |
| `test_observation_consumer.py` | Kafka AI-result adapter validation and state integration. |
| `test_operational_dashboard.py` | Layer order, risk/provenance response, classifications, telemetry replay and satellite rendering. |
| `test_pyg_normalization.py` | Diagnostic PyG normalization consistency. |
| `test_repository_hygiene.py` | No raw/generated tracked data and exact TGNN manifest/checkpoint match. |
| `test_satellite_architecture.py` | Only the change worker produces and dashboard consumes satellite change; graph/TGNN/social cannot mutate from it. |
| `test_satellite_change.py` | Pairing, duplicate/invalid handling, result contract and safe dashboard previews. |
| `test_satellite_failures.py` | Descriptive failure policy for missing/corrupt/geospatial/band/sensor/coverage/reprojection errors. |
| `test_satellite_normalizer.py` | Fast path, CRS/grid/resolution alignment, overlap, bands, dtype, NoData and classifier compatibility. |
| `test_spacenet_adapter.py` | Legacy SpaceNet wrapper compatibility. |
| `test_spacenet_regression.py` | Guarded real-data regression for contract and active SpaceNet pairs; skips honestly if the drive is absent. |
| `test_scenario.py` | Frozen assets, provenance, shared footprint, timeline completeness and OSM conversion. |
| `test_scenario_runner.py` | Clock/control transitions, real wire contracts, routing and telemetry recovery. |
| `test_stress_sensitivity_tgnn.py` | Diagnostic risk response to stress/damage changes. |
| `test_temporal.py` | Ordered snapshots, decay/freshness and temporal behavior. |
| `test_tgnn_predictor.py` | Checkpoint/feature/node mapping, finite results, ordering and honest risk interpretation. |

## 17. Documentation files

| File | Focus |
|---|---|
| `docs/FINAL_SYSTEM_DOCUMENTATION.md` | Authoritative full architecture, contracts, setup, operation, limitations and troubleshooting. |
| `docs/ARCHITECTURE.md` | Technical layer and event-flow reference. |
| `docs/UPSTREAM_AND_PREPROCESSING_FOR_PPT.md` | Presentation-friendly ingestion-to-Spark explanation. |
| `docs/UPSTREAM_ARCHITECTURE_DEEP_DIVE.md` | Detailed upstream reference covering configuration, producers, scenario replay, provenance, deduplication, MinIO, Kafka, GIS preparation, Spark, failure semantics, limitations, and presentation questions. |
| `docs/UPSTREAM_DESIGN_DECISIONS.md` | Decision-rationale companion explaining why each upstream technology and boundary was selected, rejected alternatives, trade-offs, and field-deployment replacements. |
| `docs/COMPLETE_ARCHITECTURE_DIAGRAMS.md` | Mermaid-based visual reference for the full source-to-dashboard architecture, topic routing, scenario replay, modality processing, GIS/graph/TGNN, dashboard projection, deployment, security, and persistence boundaries. |
| `docs/FRONTEND_ARCHITECTURE_GUIDE.md` | Beginner-friendly guide to the dashboard page shell, JavaScript modules, MapLibre layers, navigation, refresh lifecycle, API client, evidence views, security modes, and common user interactions. |
| `docs/SIMULATION_WORKFLOW_FOR_PPT.md` | Presentation-friendly simulation clock, provenance and event story. |
| `docs/FINAL_SCENARIO.md` | Frozen Louisiana scenario selection and provenance. |
| `docs/AI_INTEGRATION.md` | YOLO architecture, model lineage, metrics and serving path. |
| `docs/DRONE_YOLO_VALIDATION.md` | Evidence that real images pass through dedup, MinIO/Kafka and live checkpoint inference. |
| `docs/SEGMENTATION_VERIFICATION.md` | Focused segmentation-result validation. |
| `docs/UNIVERSAL_SATELLITE_ADAPTER.md` | Raster input/output, CRS/grid/band/mask/failure policy and dashboard-only boundary. |
| `docs/GEOLOCATION_GIS_VALIDATION.md` | Image-to-GPS-to-source-ID-to-graph-node traceability. |
| `docs/GRAPH_STATE_VALIDATION.md` | State equations, cascade/recovery and temporal safety. |
| `docs/TGNN_VALIDATION.md` | Feature order, checkpoint interpretation, scenario response and non-calibration warning. |
| `docs/BUILDING_CLASSIFICATION.md` | Persistent classification design and API/workflow. |
| `docs/OPERATIONAL_DASHBOARD.md` | Dashboard information order, layers and operator actions. |
| `docs/END_TO_END_ACCEPTANCE.md` | Full-stack acceptance cases and captured evidence. |
| `docs/RESULTS_AND_EVALUATION.md` | Validated-real, simulated and architecturally-supported conclusions. |
| `docs/VALIDATION_REPORT.md` | Earlier consolidated integration validation report. |
| `docs/REPOSITORY_CLEANUP.md` | What was removed/retained and how clean-clone installation was verified. |
| `docs/BRANCH_AND_PUSH.md` | Branch/push workflow for the combined codebase. |

## 18. Results, storage and runtime placeholders

| File or directory | Meaning |
|---|---|
| `results/evaluation_2026-09-26/metrics.json` | Versioned evaluation snapshot covering model, dedup, satellite, GIS, graph/TGNN, social, latency and event counts. |
| `dashboard_degraded.png`, `dashboard_recovery.png` | Dashboard evidence before and after recovery. |
| `yolo_5_9240.png`, `yolo_6_270.png`, `yolo_8_90.png` | Rendered YOLO evidence for the selected scenario images. |
| `results/satellite_e2e_2026-10-01.json` | Local, currently untracked real satellite end-to-end evidence; retained during cleanup but not part of the committed repository until deliberately added. |
| `logs/.gitkeep` | Keeps the runtime log directory present in a clean clone. Log files themselves are ignored/generated. |
| `storage/checkpoints/.gitkeep` | Keeps the Spark checkpoint root. Contents are generated offsets/state and may be cleared only when a replay reset is intended. |
| `storage/processed/.gitkeep` | Keeps the Parquet output root. Contents are generated Spark products. |
| `storage/dedup/.gitkeep` | Keeps the dedup directory; the real SQLite registry is local persistent state. |
| `storage/received_images/{drone,gis,satellite}/.gitkeep` | Keeps consumer reconstruction folders without committing received imagery. |

## 19. Which files are authoritative?

When two files appear to describe the same thing, use this precedence:

1. Runtime contracts and validation in Python code.
2. Frozen scenario JSON/GeoJSON and model manifests/checksums.
3. `FINAL_SYSTEM_DOCUMENTATION.md` and focused validation documents.
4. README quick-start summaries.
5. Generated plots/screenshots/logs as evidence only.

Examples:

- YOLO classes/checkpoint identity: `artifact_manifest.json` + `best.pt`, not a
  label guessed from a plot.
- Live YOLO thresholds: `.env`/`settings.py`, not `args.yaml`.
- TGNN feature order: `checkpoint_manifest.json`, `helpers.py` and the validated
  bridge, not column order inferred from a tensor printout.
- Scenario location: `scenario.json`, `pair.json` and frozen GeoJSON together.
- Manual building type: SQLite classification ledger joined at runtime, never
  a modification to raw OSM GeoJSON.
- Satellite map result: classifier-produced WGS84 GeoJSON. Source TIFF and JPEG
  preview remain evidence formats, not TGNN inputs.

## 20. Safe change map

- Adding a Kafka stream requires a topic constant, producer contract, Spark
  schema/processor, topic creation, dashboard/reporting decisions and tests.
- Replacing a YOLO checkpoint requires class-contract review, manifest/hash and
  metric updates, inference tests and documentation—not just copying a `.pt`.
- Adding a satellite source requires compatible optical band semantics and
  georeferencing plus normalizer regressions; do not resize by array dimensions.
- Changing graph features requires synchronized bridge, manifest, model
  architecture/checkpoint and tests. Existing weights cannot accept an arbitrary
  new feature order.
- Changing the scenario boundary requires re-freezing SpaceNet, OSM, GPS
  assignments, source IDs, graph bindings, checksums and expected results as one
  coherent package.
