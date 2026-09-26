# Repository cleanup and clean-clone verification

Cleanup date: 2026-09-26  
Branch: `dibsei/baseline-blocker-fixes`

## Outcome

The indexed repository was reduced from 3,382 files and roughly 475 MB of
application content to 223 files and 38.07 MB (including the committed
evaluation evidence). The cleanup removes 3,171 tracked files. Raw datasets,
reproducible run directories, caches, logs, and local state are excluded. Git
history was not rewritten, so old commits still contain the files that existed
at those revisions.

## Obsolete-version and duplicate inventory

| Removed item | Reason | Canonical replacement |
|---|---|---|
| `ai/computer_vision/dataset/` (3,091 files, 423.6 MB) | Raw train/validation/test data was incorrectly tracked | External ISBDA path configured by `ISBDA_DATASET_PATH` |
| `ai/computer_vision/runs/` | Duplicate generated Ultralytics run | `ai/computer_vision/artifacts/drone_detector/` |
| `runs/` | Second duplicate generated Ultralytics run | `ai/computer_vision/artifacts/drone_detector/` |
| `yolov8n.pt` | Re-downloadable training seed, unused for serving | Committed serving `best.pt` |
| `producers/` and `utils/kafka_config.py` | Hard-coded, old path-based producer implementation | `data_source/*/*_producer.py` and `common/kafka_utils.py` |
| `docker/docker-compose.yml` | Obsolete Kafka-only stack | Root `docker-compose.yml` with health checks, Kafka, and MinIO |
| `ai/computer_vision/scripts/` | Hard-coded drive-letter utilities | Portable `ai/computer_vision/tools/` CLIs |
| Legacy CV pipeline/demo modules | Parallel, unused inference and preprocessing implementations | `detector.py`, `infer.py`, `worker.py`, and `ai/preprocessing/pipeline.py` |
| `verify_pipeline.py` | Checked obsolete paths and old YOLOv8 output layout | `scripts/verify_repository.py` and scenario validation |
| `datasets.dvc` and `.dvc/` | Stale 8.16 GB pointer with no configured remote | Explicit external dataset environment variables |
| Empty `notebooks/testing.ipynb` | Empty placeholder | None |

The selective `consumer/` and graph-observation `consumers/` packages were
both retained: they have different responsibilities and are not duplicates.
The canonical artifact directory intentionally retains a legacy checkpoint,
CSV, and plots as training-lineage evidence; none are used for serving.

## Generated-output and ignore policy

`.gitignore` now excludes Python/test/editor caches, logs, SQLite state,
runtime helpers, received objects, Spark Parquet/checkpoints, raw datasets,
Ultralytics run trees, and arbitrary model downloads. Explicit exceptions
allow only the two registered serving artifacts:

- `ai/computer_vision/artifacts/drone_detector/weights/best.pt`
- `tgnn/models/tgnn.pth`

The canonical legacy `last.pt` is also excepted because its hash is registered
in the artifact manifest and it is retained solely as lineage evidence.
Evaluation evidence under `results/evaluation_2026-09-26/` is an intentional
versioned deliverable, not transient runtime output.

`.gitattributes` fixes normal text files to LF and explicitly preserves CRLF
for the frozen GIS snapshot and registered training CSV. This prevents Windows
checkout conversion from invalidating byte-level scenario/model checksums.

## Model verification

`python -m scripts.verify_repository` verifies:

- every registered YOLO artifact checksum;
- YOLO metrics embedded in the serving checkpoint;
- the TGNN checkpoint SHA-256 and byte size;
- absence of tracked raw/generated/local-state paths;
- coverage of direct runtime and AI dependencies;
- third-party import availability; and
- structural validity of the frozen scenario package.

Registered serving identities:

| Model | SHA-256 | Size |
|---|---|---:|
| YOLO26s damage detector | `780241f6b42f9f0b0d83a8be8d3168e1ca864a756ff93907e72bbb7f9fe3cbf9` | 20,298,181 bytes |
| TGNN | `ad40a02e91cfe414da23f585dcf237d7fd2b5f646f3ebc47c20cb7d73640cc88` | 969,915 bytes |

## Dependency reconstruction

- `requirements.txt`: ingestion, storage, Spark, GIS, satellite, and dashboard
  runtime.
- `requirements-ai.txt`: includes the runtime file and adds YOLO/TGNN.
- `requirements-dev.txt`: includes the complete application and adds pytest.
- Component requirement files redirect to the repository-level authoritative
  files instead of maintaining unpinned duplicates.

Pandas, Pillow, and Pydantic are now declared directly because repository code
imports them directly; they are no longer left to transitive dependencies.
The unused ImageHash dependency was removed because the active pHash
implementation uses OpenCV and NumPy.

## Clean-clone acceptance procedure

Use CPython 3.10 on Windows. Keep the clone path short because the PyTorch wheel
contains deeply nested license files; a long path can fail with `WinError 206`.
From a new clone:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.verify_repository
.\.venv\Scripts\python.exe -m pytest -q
```

The repository intentionally does not redistribute the raw SpaceNet or ISBDA
datasets. Copy `.env.example` to `.env`, set `SPACENET8_DATASET_PATH` and
`ISBDA_DATASET_PATH`, and then verify those external bytes:

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m scripts.validate_scenario `
  scenarios\louisiana_east_flood\scenario.json --strict
```

With Docker Desktop running, complete the runnable scenario gate:

```powershell
docker compose --env-file .env up -d
.\.venv\Scripts\python.exe -m scripts.create_topics
```

Start Spark, the YOLO worker, satellite worker, observation consumer, and
dashboard using the commands in the root README, then run:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_scenario --speed 10
```

This is the reproducible boundary of a clean clone: code, frozen GIS, model
artifacts, and manifests are versioned; licensed raw imagery remains external
and is checksum-validated before replay.

## Acceptance evidence

The procedure above was executed from an exported clean-tree snapshot in a
new `C:\dss-fi-*\.venv` on 2026-09-26:

- all pinned packages installed from `requirements-dev.txt`;
- `pip check` reported no broken requirements;
- repository verification and all YOLO/TGNN hashes passed;
- strict scenario validation passed;
- the fresh environment completed 117 tests with 2 optional real-data smoke
  tests skipped;
- the scenario runner completed all 12 events at 100x through the live
  Kafka/MinIO producer interfaces with `error: null`.

An initial install under the much longer `%TEMP%\disaster-clean-install-*`
path reproduced `WinError 206`. Repeating the same install at the documented
short path succeeded, confirming that this is a Windows path-length constraint
rather than an unresolved dependency.
