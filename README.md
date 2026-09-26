# Real-Time Post-Disaster Streaming and Assessment System

A distributed capstone platform that replays real disaster datasets as live
feeds, transports events through Kafka, stores imagery in MinIO, processes each
modality with Spark Structured Streaming, and exposes the complete workflow in a
local web dashboard.

The repository implements Layers 1-4, including a trained YOLO26s
drone-damage detector. Unique drone images can be analyzed in real time and
published to `ai-analysis-results`; duplicates remain auditable without
rerunning inference.

The canonical demonstration is now defined by
[`scenarios/louisiana_east_flood/scenario.json`](scenarios/louisiana_east_flood/scenario.json):
a bounded Louisiana flood scenario with a frozen OpenStreetMap GIS snapshot,
stable infrastructure IDs, and a deterministic twelve-step event timeline. See
[`docs/FINAL_SCENARIO.md`](docs/FINAL_SCENARIO.md) for its readiness and data
provenance.

## Current features

### Data ingestion

- CrisisMMD social records replayed as a live `social-posts` stream.
- Responder-reviewed social alerts with explicit incoming, pending, confirmed,
  and reported-false states; only confirmed reports create orange, node-linked
  human-evidence hotspots.
- Immutable temporal graph snapshots with freshness, explicit recovery,
  dependency-cascade propagation, and field-level before/after explanations.
- Persistent responder-assigned building types, stored as an append-only SQLite
  revision history and joined to GIS lookups, graph nodes, and dashboard data
  without changing the frozen raw OpenStreetMap snapshot.
- Checkpoint-pinned TGNN inference with validated feature/tensor contracts,
  stable GIS ID mapping, and explicitly uncalibrated relative failure-risk
  scores. These scores support ranking and temporal deltas, not probability claims.
- ISBDA drone JPG frames replayed through `drone-video`.
- xBD satellite TIFF images converted to JPEG and published through
  `satellite-imagery`.
- Real SpaceNet pre/post pairs downloaded from MinIO by a dedicated change
  worker and published as dashboard-only broad-area evidence on
  `satellite-change-results`.
- GIS JPG/PNG map imagery replayed through `gis-data`.
- Configurable stream delay and event limits for demonstrations.
- Recursive dataset discovery from paths configured in `.env`.

### Kafka and object storage

- Docker Compose stack with ZooKeeper, Kafka, and MinIO.
- Explicit Kafka topics for social, drone, satellite, and future GIS events.
- Two image transfer modes:

| Mode | Image location | Kafka payload | Recommended use |
| --- | --- | --- | --- |
| `base64` | Embedded in the event | Full encoded image | Simple initial smoke test |
| `object_storage` | MinIO or AWS S3 | URI, key, checksum, and metadata | Normal and remote workflow |

- MinIO image organization by source and content checksum.
- S3-compatible storage client that can later target AWS S3.
- Presigned download links plus durable object keys.

### Duplicate elimination

- SHA-256 exact duplicate detection for drone and satellite imagery.
- Perceptual-hash near-duplicate detection for drone frames by default.
- Persistent SQLite registry under
  `storage/dedup/image_fingerprints.sqlite3`.
- Canonical references allow later AI results to be reused without processing
  the same image twice.
- Satellite near-duplicate matching remains disabled by default because small
  before/after changes may contain the disaster signal.

### Spark processing

- One Structured Streaming application subscribes to all active data topics.
- Topic-specific schemas, validation, normalization, and timestamps.
- Social text normalization and disaster-keyword matching.
- Drone and satellite metadata validation without persisting Base64 payloads.
- `ready_for_ai` and `duplicate_skipped` processing states.
- Console output, Parquet output, and recovery checkpoints.
- Social counts grouped by disaster event and hazard label.
- AI result validation and durable Parquet output under `storage/processed/ai`.

### AI damage detection

- Image validation, resize, motion, blur-quality, and pHash preprocessing.
- Serving YOLO26s `best.pt` checkpoint for `Slight`, `Severe`, and `Debris`.
- Legacy YOLOv8 `last.pt`, CSV, and plots retained as historical training
  evidence but not used by the worker.
- Kafka/MinIO worker: `drone-video` -> inference -> `ai-analysis-results`.
- Per-detection image reference, scenario timestamp, simulated-GPS provenance,
  declared target association, and serving-checkpoint SHA-256.
- Serving-checkpoint metrics and training arguments, plus clearly labeled
  legacy plots and validation evidence.
- Portable inference, training, and evaluation commands with no drive-letter
  assumptions.
- SHA-256 and final-metric verification with `scripts.verify_ai_artifacts`.

### Operations dashboard

- Responsive FastAPI + HTML/CSS/JavaScript control center.
- Incident-first interactive MapLibre basemap combining the frozen structural
  GIS, accepted drone damage observations, TGNN relative-risk nodes, an
  explicit highest-risk marker, and confirmed social hotspots.
- Top-five risk explanation, pending-alert review actions, SpaceNet change
  evidence, scenario timeline controls, provenance badges, layer toggles,
  legends, freshness timestamps, and explicit loading/empty/error states.
- Docker, Kafka, MinIO, and process health indicators.
- Kafka topic event counts and MinIO object totals.
- Start/stop controls for infrastructure, Spark, AI, and all producers.
- Producer limit and delay controls.
- Spark Parquet tables and AI-readiness flow.
- Live detections and preserved precision, recall, and mAP metrics.
- MinIO image browser with previews.
- Building selection and manual classification with operator, timestamp, notes,
  and complete revision history that survives dashboard restarts.
- Deduplication totals and live process logs.
- REST API documentation at `/api/docs`.
- Optional ngrok sharing for demonstrations.

## System flow

```text
CrisisMMD / ISBDA / xBD datasets
                 |
                 v
       Python source producers
                 |
        SHA-256 + pHash checks
           /             \
          v               v
 MinIO image objects   Kafka events
                          |
                          v
              Spark Structured Streaming
                   /               \
                  v                 v
         Console + Parquet     AI-ready metadata
```

## Quick start

These commands assume the repository has already been configured once and
Docker Desktop is running.

```powershell
cd C:\Users\dibya\Downloads\disaster-streaming-system\disaster-streaming-system
.\.venv\Scripts\Activate.ps1
docker compose --env-file .env up -d
python -m scripts.create_topics
python -m uvicorn dashboard.server:app --host 127.0.0.1 --port 8088
```

Open <http://localhost:8088>.

Validate the scenario package independently of Docker:

```powershell
python -m scripts.validate_scenario scenarios\louisiana_east_flood\scenario.json
```

The real SpaceNet 8 satellite pair and three real ISBDA drone images are
registered. The `--strict` scenario package gate now passes; the drone GPS
assignments and scenario timestamps are explicitly simulated.

From **Pipeline control** in the dashboard:

1. Confirm Kafka, ZooKeeper, and MinIO are healthy.
2. Start **Spark processor**.
3. Start **AI damage worker**.
4. Wait until both report `Running` and check **Live logs** for
   `Spark queries started`.
5. Start the **Drone producer** (and any other producers).
6. Open **Damage analysis** for model output and **Spark output** for Parquet.
7. Open **Image storage** to inspect MinIO objects.

The order matters when `SPARK_STARTING_OFFSETS=latest`: start Spark before the
producers so it sees newly published events.

### Replay the frozen scenario

After Kafka/MinIO and the downstream Spark and AI consumers are running, replay
the twelve-step story at 10x speed:

```powershell
python -m scripts.run_scenario --speed 10
```

Start the satellite change worker before the replay so it receives both images:

```powershell
python -m core.satellite.change_worker
```

It may also be started from **Pipeline control** in the dashboard. Open
**Satellite change** to see the real pre/post images, scenario timestamp,
WGS84 footprint, 8x8 radiometric-change grid, and official SpaceNet flood-label
count. This result is intentionally not subscribed to by the TGNN path.

For operator controls, open the interactive runner:

```powershell
python -m scripts.run_scenario --interactive --speed 10
```

Its commands are `start`, `pause`, `resume`, `reset`, `speed <factor>`,
`status`, and `quit`. The runner publishes the real SpaceNet and ISBDA image
bytes through the existing satellite/drone producers, the simulated distress
report through the social producer schema, and state readings through the
validated `infrastructure-telemetry` contract. It never writes model output or
dashboard state directly; Spark, AI, graph, and TGNN consumers must derive
those results normally.

The persistent image deduplication registry is intentionally shared with the
normal producers. Therefore, replaying after `reset` can produce canonical
duplicate events unless you intentionally reset the dedup database as described
below.

## First-time local setup

### 1. Install prerequisites

- Windows 10/11
- Python 3.10 or 3.11
- Docker Desktop with Docker Compose
- Java 17 for Spark
- Dataset drive or local dataset directories

Python 3.13 is not recommended for the pinned NumPy/OpenCV stack.

### 2. Create the Python environment

```powershell
py -3.10 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-ai.txt
```

If Windows reports error `WinError 206` while installing PyTorch from a deeply
nested checkout, temporarily map the project to a short drive path:

```powershell
subst X: (Get-Location).Path
X:\.venv\Scripts\python.exe -m pip install -r X:\requirements-ai.txt
subst X: /D
```

### 3. Configure the environment

```powershell
Copy-Item .env.example .env
.\scripts\configure_windows.ps1
```

For a one-computer run, use:

```dotenv
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_ADVERTISED_HOST=localhost
IMAGE_TRANSFER_MODE=object_storage
OBJECT_STORAGE_ENDPOINT_URL=http://localhost:9000
```

Verify the three dataset paths in `.env` before starting producers.

### 4. Prepare Spark on Windows

Install Java 17, then download the project-local Hadoop filesystem helper:

```powershell
.\scripts\setup_spark_windows.ps1
```

The dashboard automatically selects an installed Java 17 JDK and the helper
under `.runtime/hadoop-3.3.5` when it launches Spark.

### 5. Validate the installation

```powershell
docker compose --env-file .env up -d
python -m scripts.create_topics
python -m scripts.test_kafka
python -m scripts.test_object_storage
python -m scripts.verify_ai_artifacts
python -m unittest discover -v
```

## Running without the dashboard

Open a separate activated PowerShell terminal for each long-running command.

Infrastructure:

```powershell
docker compose --env-file .env up -d
python -m scripts.create_topics
```

Spark:

```powershell
.\scripts\run_spark.ps1
```

Producers:

```powershell
python -m data_source.social.social_producer --limit 5 --delay 0.2
python -m data_source.drone.drone_producer --limit 5 --delay 0.5
python -m data_source.satellite.satellite_producer --limit 2 --delay 1
python -m data_source.gis.gis_producer --limit 5 --delay 1
```

Selective consumer:

```powershell
python -m consumer.interactive_consumer
```

Scenario detection observations (run before replaying drone events):

```powershell
python -m consumers.observation_consumer `
  --gis-path scenarios\louisiana_east_flood\gis\infrastructure.geojson `
  --allow-scenario-simulated-gps
```

## Outputs and local services

| Resource | Location |
| --- | --- |
| Operations dashboard | <http://localhost:8088> |
| Dashboard API docs | <http://localhost:8088/api/docs> |
| MinIO console | <http://localhost:9001> |
| Kafka broker | `localhost:9092` |
| Received drone images | `storage/received_images/drone/` |
| Received satellite images | `storage/received_images/satellite/` |
| Spark social output | `storage/processed/social/` |
| Spark drone output | `storage/processed/drone/` |
| Spark satellite output | `storage/processed/satellite/` |
| Spark GIS output | `storage/processed/gis/` |
| Spark AI output | `storage/processed/ai/` |
| Preserved YOLO model | `ai/computer_vision/artifacts/drone_detector/weights/best.pt` |
| AI results | Kafka topic `ai-analysis-results` |
| Spark checkpoints | `storage/checkpoints/` |
| Deduplication registry | `storage/dedup/image_fingerprints.sqlite3` |
| Building classification ledger | `storage/gis/building_classifications.sqlite3` |
| Dashboard-managed logs | `logs/dashboard/` |

Default development MinIO login from `.env.example`:

```text
Username: disasteradmin
Password: change-this-development-password
```

Change these credentials before sharing access.

## Sharing the dashboard

The dashboard can be shared temporarily with ngrok after configuring an ngrok
account token:

```powershell
ngrok config add-authtoken YOUR_TOKEN
ngrok http 8088
```

The generated `https://...ngrok-free.dev` address changes when the free tunnel
is recreated. The dashboard contains controls that can start and stop local
processes. Prefer ngrok traffic-policy authentication, Tailscale, or another
private network instead of publishing it anonymously. Never expose Kafka port
`9092` or MinIO API port `9000` through router port forwarding.

## AI layer integration

Install `requirements-ai.txt`, start the worker before publishing drone frames,
and it will download each unique MinIO object (or decode Base64), preprocess it,
run the preserved detector, and publish this implemented contract:

```json
{
  "frame_id": "image001.jpg",
  "content_hash": "ab12...",
  "model_name": "drone_detector_yolo26s",
  "status": "analyzed",
  "detection_count": 1,
  "max_confidence": 0.91,
  "damage_classes": ["Severe"],
  "detections": []
}
```

```powershell
python -m ai.computer_vision.worker
python -m ai.computer_vision.infer ai/computer_vision/samples/10_1530.jpg
```

For training/evaluation, supply the original Ultralytics dataset YAML:

```powershell
python -m ai.computer_vision.train --data D:/datasets/drone/data.yaml
python -m ai.computer_vision.evaluate --data D:/datasets/drone/data.yaml
```

Portable dataset utilities replace the upstream hard-coded paths:

```powershell
python -m ai.computer_vision.tools.convert_coco_to_yolo --annotations instances.json --output labels
python -m ai.computer_vision.tools.prepare_dataset --images images --labels labels --output dataset
python -m ai.computer_vision.tools.visualize_labels --image frame.jpg --label frame.txt --output preview.jpg
```

See [AI integration and lineage](docs/AI_INTEGRATION.md) for preserved metrics,
checksums, source provenance, and redistribution considerations.

## Project layout

```text
disaster-streaming-system/
|-- ai/                        Preprocessing, YOLO code, model, and run evidence
|-- common/                    Kafka, deduplication, and object-storage helpers
|-- config/                    Environment-backed settings
|-- consumer/                  Selective Kafka/MinIO consumer
|-- dashboard/
|   |-- server.py              FastAPI backend
|   |-- services.py            Kafka, Docker, MinIO, SQLite, and Parquet metrics
|   |-- process_manager.py     Managed Spark and producer processes
|   `-- static/                Responsive dashboard frontend
|-- data_source/               Social, drone, and satellite producers
|-- docs/ARCHITECTURE.md       Detailed layer and event-contract documentation
|-- scripts/                   Setup, validation, firewall, and Spark helpers
|-- spark/                     Topic-specific Structured Streaming processors
|-- storage/                   Received images, Parquet, checkpoints, and dedup DB
|-- docker-compose.yml         ZooKeeper, Kafka, and MinIO
|-- requirements.txt           Pinned Python dependencies
|-- requirements-ai.txt        YOLO inference and training dependencies
`-- .env.example               Configuration template
```

## Complete distributed setup guide

### Step 1 - Extract and open the ZIP

Extract the ZIP on both systems. Open PowerShell or a terminal inside the extracted `disaster-streaming-system` directory. All commands below are run from that directory.

System 2 does not need the removable dataset drive.

### Step 2 - Connect remote systems with Tailscale

Skip this step only when both machines are on the same reachable LAN.

1. Install Tailscale on System 1 and create a private tailnet.
2. Invite the teammate by email from the Tailscale administration page.
3. Install Tailscale on System 2 and accept the invitation.
4. Confirm both devices show as connected.
5. On System 1, obtain the stable Tailscale IPv4 address:

```powershell
tailscale ip -4
```

Example result:

```text
100.84.21.17
```

Use the real value everywhere this guide shows `100.84.21.17`. Tailscale's official quickstart is available at <https://tailscale.com/docs/how-to/quickstart>.

### Step 3 - Install Python dependencies on System 1

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Verify:

```powershell
python --version
docker version
docker compose version
```

### Step 4 - Create and configure `.env` on System 1

```powershell
Copy-Item .env.example .env
.\scripts\configure_windows.ps1
```

The helper displays IPv4 candidates. Enter:

- the Tailscale `100.x.x.x` IP for internet-separated systems;
- the active Wi-Fi IPv4 for same-LAN systems; or
- `localhost` for a one-computer test.

It configures Kafka on port `9092` and object storage on port `9000`.

Open `.env` and verify the removable-drive folders:

```dotenv
SOCIAL_DATASET_PATH=D:/Capstone-Team-67/datasets/Social/CrisisMMD_v2.0/crisismmd_datasplit_all/crisismmd_datasplit_all
DRONE_DATASET_PATH=D:/Capstone-Team-67/datasets/Drone/ISBDA/train
SATELLITE_DATASET_PATH=D:/Capstone-Team-67/datasets/Satellite/xBD/test/images
```

Discovery is recursive, so each setting may point to a parent directory containing the expected file type.

#### Choose the transfer mode

For the first smoke test:

```dotenv
IMAGE_TRANSFER_MODE=base64
```

For the efficient pipeline:

```dotenv
IMAGE_TRANSFER_MODE=object_storage
OBJECT_STORAGE_ENDPOINT_URL=http://100.84.21.17:9000
OBJECT_STORAGE_BUCKET=disaster-images
OBJECT_STORAGE_ACCESS_KEY=disasteradmin
OBJECT_STORAGE_SECRET_KEY=choose-a-long-unique-password
MINIO_ROOT_USER=disasteradmin
MINIO_ROOT_PASSWORD=choose-a-long-unique-password
```

The `OBJECT_STORAGE_*` credentials must match the `MINIO_ROOT_*` credentials for this capstone configuration. Change the example password before allowing teammate access. Do not commit or send the real `.env` publicly.

#### Configure duplicate elimination

Recommended defaults are already present:

```dotenv
IMAGE_DEDUPLICATION_ENABLED=true
NEAR_DUPLICATE_ENABLED=true
NEAR_DUPLICATE_SOURCES=drone
PHASH_DISTANCE_THRESHOLD=6
DEDUP_LOOKBACK_RECORDS=500
```

Lowering `PHASH_DISTANCE_THRESHOLD` makes matching stricter. Raising it removes more frames but increases the risk of treating a meaningful change as a duplicate. Keep satellite out of `NEAR_DUPLICATE_SOURCES` unless your team validates the effect.

### Step 5 - Configure the Windows firewall

Open PowerShell as Administrator and run:

```powershell
.\scripts\open_kafka_firewall.ps1
```

The script allows:

- Kafka TCP `9092` on a private LAN and from Tailscale addresses;
- object-storage TCP `9000` on a private LAN and from Tailscale addresses.

MinIO's management console uses `9001`; it is intended for local administration and is not required by the teammate.

### Step 6 - Start infrastructure on System 1

```powershell
docker compose --env-file .env up -d
docker compose ps
```

This starts:

- `disaster-zookeeper`
- `disaster-kafka`
- `disaster-minio`

Wait until the services are healthy, then create and verify topics:

```powershell
python -m scripts.create_topics
python -m scripts.test_kafka
```

When `IMAGE_TRANSFER_MODE=object_storage`, also verify upload and download:

```powershell
python -m scripts.test_object_storage
```

Required topics:

- `social-posts`
- `drone-video`
- `satellite-imagery`
- `gis-data`
- `ai-analysis-results`

When using MinIO, its local administration console is available on System 1 at <http://localhost:9001>. The image bucket is created automatically when the first unique image is uploaded.

### Step 7 - Prepare System 2

Extract the same ZIP and create a virtual environment.

Windows:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit System 2's `.env`:

```dotenv
KAFKA_BOOTSTRAP_SERVERS=100.84.21.17:9092
OBJECT_STORAGE_ENDPOINT_URL=http://100.84.21.17:9000
OBJECT_STORAGE_BUCKET=disaster-images
OBJECT_STORAGE_ACCESS_KEY=disasteradmin
OBJECT_STORAGE_SECRET_KEY=the-same-password-from-system-1
```

The dataset paths and `KAFKA_ADVERTISED_HOST` are not used on System 2.

Test the private connection.

Windows:

```powershell
Test-NetConnection 100.84.21.17 -Port 9092
Test-NetConnection 100.84.21.17 -Port 9000
python -m scripts.test_kafka
```

macOS/Linux:

```bash
nc -vz 100.84.21.17 9092
nc -vz 100.84.21.17 9000
python -m scripts.test_kafka
```

### Step 8 - Start the consumer on System 2

```bash
python -m consumer.interactive_consumer
```

Select:

```text
1. Social Media
2. Drone Feed
3. Satellite Imagery
4. All
5. AI Analysis Results
```

The consumer supports values such as `1`, `2`, `1,2`, `4` (all), or `5` (AI).

For a unique image it either decodes Base64 or downloads the object and saves it under:

```text
storage/received_images/drone/
storage/received_images/satellite/
```

For a duplicate, it prints the canonical image reference and does not download or save another copy.

### Step 9 - Start producers on System 1

Open a separate activated PowerShell terminal for each producer.

Social:

```powershell
python -m data_source.social.social_producer
```

Drone:

```powershell
python -m data_source.drone.drone_producer
```

Satellite:

```powershell
python -m data_source.satellite.satellite_producer
```

Use limits for the first test:

```powershell
python -m data_source.social.social_producer --limit 5 --delay 0.2
python -m data_source.drone.drone_producer --limit 5 --delay 0.5
python -m data_source.satellite.satellite_producer --limit 2 --delay 1
```

Producer logs show whether an image was transferred or skipped as an exact/near duplicate.

To inspect the persistent canonical-image registry at any time:

```powershell
python -m scripts.dedup_report
```

### Step 10 - Run Spark on System 2

Confirm Java 17:

```bash
java -version
```

Windows:

```powershell
.\scripts\run_spark.ps1
```

macOS/Linux:

```bash
chmod +x scripts/run_spark.sh
./scripts/run_spark.sh
```

The first Spark run downloads the matching Kafka connector package. Start Spark before the producers when `SPARK_STARTING_OFFSETS=latest`.

Spark outputs metadata—not Base64—to:

```text
storage/processed/social/
storage/processed/drone/
storage/processed/satellite/
```

Checkpoints are written to:

```text
storage/checkpoints/social/
storage/checkpoints/drone/
storage/checkpoints/satellite/
```

Unique image events receive `ready_for_ai`. Duplicate events receive `duplicate_skipped` and retain `canonical_image_id`, `duplicate_method`, and `content_hash`.

### Step 11 - Run the AI layer

Install `requirements-ai.txt`, then run this on the machine that can reach both
Kafka and MinIO:

```powershell
python -m scripts.verify_ai_artifacts
python -m ai.computer_vision.worker
```

Start it before the drone producer when `AI_STARTING_OFFSETS=latest`. It skips
producer-identified duplicates, records preprocessing filters and errors, and
publishes detections to `ai-analysis-results`. Spark consumes that topic and
writes `storage/processed/ai/`.

For delayed/asynchronous work, use object-storage credentials rather than relying only on presigned URLs. MinIO and Kafka remain reachable only while System 1, Docker, Tailscale, and the internet connection are active. For an always-available deployment, replace MinIO with AWS S3 and the local broker with an authenticated managed Kafka service.

## AWS S3 instead of MinIO

The code uses the S3 API and can target AWS directly:

```dotenv
IMAGE_TRANSFER_MODE=object_storage
OBJECT_STORAGE_ENDPOINT_URL=
OBJECT_STORAGE_REGION=ap-south-1
OBJECT_STORAGE_BUCKET=your-private-bucket
OBJECT_STORAGE_ACCESS_KEY=your-authorized-access-key
OBJECT_STORAGE_SECRET_KEY=your-authorized-secret-key
```

Use a dedicated least-privilege IAM identity. Never reuse or share personal/corporate credentials without authorization. The identity needs access to the configured bucket. S3 presigned URLs provide temporary object access without placing AWS secrets inside Kafka events.

## Resetting a demonstration

Stop processes with `Ctrl+C`, then stop containers:

```powershell
docker compose down
```

This preserves Kafka-independent project data and the named MinIO volume.

The fingerprint database intentionally persists. To replay every image as new, stop both image producers and move or delete:

```text
storage/dedup/image_fingerprints.sqlite3
storage/dedup/image_fingerprints.sqlite3-shm
storage/dedup/image_fingerprints.sqlite3-wal
```

Do this only for an intentional reset. Removing the Spark checkpoint directories similarly resets Spark recovery state and should not be done when demonstrating fault tolerance.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka client address |
| `KAFKA_ADVERTISED_HOST` | `localhost` | Address Kafka returns to clients |
| `IMAGE_TRANSFER_MODE` | `base64` | `base64` or `object_storage` |
| `OBJECT_STORAGE_ENDPOINT_URL` | `http://localhost:9000` | MinIO/S3 endpoint; blank uses AWS S3 |
| `OBJECT_STORAGE_BUCKET` | `disaster-images` | Image bucket |
| `OBJECT_STORAGE_PRESIGNED_EXPIRY` | `3600` | Download URL lifetime in seconds, capped at seven days |
| `IMAGE_DEDUPLICATION_ENABLED` | `true` | SHA-256 duplicate elimination |
| `NEAR_DUPLICATE_ENABLED` | `true` | Enables pHash comparison |
| `NEAR_DUPLICATE_SOURCES` | `drone` | Sources eligible for pHash suppression |
| `PHASH_DISTANCE_THRESHOLD` | `6` | Maximum pHash Hamming distance |
| `DEDUP_LOOKBACK_RECORDS` | `500` | Recent fingerprints compared per source |
| `BUILDING_CLASSIFICATION_DATABASE_PATH` | `storage/gis/building_classifications.sqlite3` | Persistent manual building-classification revision ledger |
| `SOCIAL_STREAM_DELAY` | `1` | Seconds between social records |
| `DRONE_STREAM_DELAY` | `0.5` | Seconds between drone frames |
| `SATELLITE_STREAM_DELAY` | `5` | Seconds between satellite images |
| `KAFKA_MAX_MESSAGE_BYTES` | `10485760` | Development Kafka event ceiling |
| `SPARK_STARTING_OFFSETS` | `latest` | Initial Spark offset without a checkpoint |
| `SATELLITE_CHANGE_STARTING_OFFSETS` | `latest` | Initial pair-worker offset |
| `SATELLITE_CHANGE_GRID_SIZE` | `8` | Coarse broad-area change grid dimension |
| `FINAL_SCENARIO_PATH` | `scenarios/louisiana_east_flood/scenario.json` | Frozen scenario manifest |
| `SPACENET8_DATASET_PATH` | empty | Root containing the registered SpaceNet 8 files |
| `ISBDA_DATASET_PATH` | empty | Root containing the registered ISBDA files |

## Troubleshooting

### Kafka is unavailable

```powershell
docker compose ps
python -m scripts.test_kafka
```

Confirm Kafka advertises the System 1 Tailscale/LAN address, not System 2's address. If `.env` changed, recreate containers:

```powershell
docker compose --env-file .env down
docker compose --env-file .env up -d
```

### Object upload fails

- Confirm `disaster-minio` is healthy.
- Open <http://localhost:9001> on System 1.
- Confirm both credential pairs in System 1's `.env` match.
- Confirm `OBJECT_STORAGE_ENDPOINT_URL` is reachable from System 1.
- When using Tailscale, test `<TAILSCALE_IP>:9000` from both machines.

### The temporary download URL expired

The consumer automatically attempts credential-based object download using `object_key`. Ensure System 2 has a reachable endpoint and valid read credentials. For long AI runs, consume the durable `image_uri`/`object_key`, not the temporary URL alone.

### Too many or too few near duplicates

- Lower `PHASH_DISTANCE_THRESHOLD` for stricter matching.
- Increase it cautiously for stronger suppression.
- Inspect duplicate events and canonical references before changing defaults.
- Keep satellite near-deduplication disabled unless tested on before/after imagery.

### A frame was incorrectly classified as a duplicate

The actual image is not uploaded in that event. Lower the pHash threshold and reset the dedup database for a clean replay. Exact SHA-256 matches are byte-identical; pHash matches are approximate.

### Satellite TIFF cannot be decoded

Some TIFF codecs are unsupported by a particular OpenCV build. The producer logs the file and continues without terminating the stream.

### Spark does not show old records

`SPARK_STARTING_OFFSETS=latest` reads events produced after Spark begins. Set it to `earliest` for an intentional initial replay. Existing checkpoints take precedence.

## Validation tests

Tests do not require Kafka, MinIO, Spark, or the datasets:

```bash
python -m unittest discover -v
```

They cover topic selection, social standardization, Base64 reconstruction, SHA-256/pHash behavior, canonical duplicate references, and object-reference events.

## Production direction

For production:

- use authenticated TLS Kafka rather than the development plaintext broker;
- use S3 or equivalent durable object storage;
- give the AI service read-only object access;
- use Kafka events for metadata and state changes, not large image bytes;
- use a shared database/vector index when multiple producer hosts perform global deduplication;
- preserve duplicate audit events instead of silently deleting disaster evidence;
- use embeddings only after inexpensive SHA-256 and pHash checks.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the complete data flow and Layer 4 extension points.
The manual classification data model and API are documented in
[docs/BUILDING_CLASSIFICATION.md](docs/BUILDING_CLASSIFICATION.md).
The incident-first presentation and operational API are documented in
[docs/OPERATIONAL_DASHBOARD.md](docs/OPERATIONAL_DASHBOARD.md).
The live drone/YOLO completion evidence is recorded in
[docs/DRONE_YOLO_VALIDATION.md](docs/DRONE_YOLO_VALIDATION.md).
Geolocation and exact graph-node traceability are recorded in
[docs/GEOLOCATION_GIS_VALIDATION.md](docs/GEOLOCATION_GIS_VALIDATION.md).

For repository handoff, see [create a branch and push](docs/BRANCH_AND_PUSH.md)
and the [integration validation report](docs/VALIDATION_REPORT.md).
