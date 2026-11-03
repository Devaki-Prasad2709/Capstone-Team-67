# Operational dashboard

## Navigation by user intent

The dashboard opens on **Operations**, so an incident responder sees the map,
top risks, pending actions, scenario timeline, and evidence freshness without
first navigating implementation details. The sidebar is organized into four
intent-based areas:

1. **Operations** — the common incident picture and responder actions.
2. **System Control** — infrastructure health, continuous processing workers,
   finite producers, and replay settings. This combines the former System
   Overview and Pipeline Control pages without removing their controls.
3. **Evidence** — Drone/YOLO, satellite, social audit, and persistent building
   classification details.
4. **Diagnostics** — Spark output, Kafka counts, the MinIO browser, logs, and
   API documentation for developers and troubleshooting.

The restructuring is presentation-only: the existing dashboard API routes and
the underlying detail views remain available.

## Satellite evidence presentation

Operations presents satellite evidence once: the georeferenced WGS84 change
overlay and a compact map summary containing changed, scored, unknown, and total
cell counts, scenario timestamp, tile ID, and provenance. The summary links to
the dedicated **Satellite Evidence** view instead of repeating the PRE/POST
images in the incident page.

The drill-down is the authoritative detailed presentation. It shows the full
PRE/POST pair, scored and unknown grid cells, shared footprint, safe relative
source paths and SHA-256 checksums, reference flood annotations, and Universal
Satellite Adapter provenance. Both presentations consume the same frontend
evidence adapter and the existing satellite result contracts. While the detail
view is active, it refreshes with the rest of the live dashboard.

Satellite evidence remains dashboard-only. Rendering or reviewing it does not
create graph observations, mutate graph state, or enter TGNN snapshots.

## Pipeline process controls

Pipeline control separates finite dataset **Producers** from continuous
**Processing Workers**. Event count and delay apply only to social, drone,
satellite, and GIS producer replays. Spark, YOLO, and satellite-change workers
start without an event limit and remain active until deliberately stopped. The
optional worker diagnostic maximum limits the YOLO and satellite-change workers
for troubleshooting only; Spark remains continuous.

The dashboard persists process metadata under `.runtime/`, retains exit codes
and the latest start failure, and inspects host command lines after a dashboard
restart. A live process not owned by the current dashboard instance is reported
as **externally running** and must be stopped where it was started. Infrastructure
shutdown has a separate confirmation prompt because it stops shared Kafka,
MinIO, and supporting services.

## Scenario readiness states

The global status is not a binary connectivity light. It reports five explicit
states: **healthy**, **degraded**, **stopped**, **unavailable**, and **unknown**.
The overall result answers whether the complete scenario can run and is composed
from independently evidenced groups:

- **Infrastructure health** uses Docker runtime and healthcheck state for Kafka,
  MinIO, and ZooKeeper, then verifies Kafka and MinIO through their APIs.
- **Processing readiness** requires Spark, the YOLO worker, satellite-change
  worker, and observation consumer. A deliberate stop is different from an
  unexpected process exit.
- **Source readiness** checks the required SpaceNet, ISBDA, and committed model
  paths. Standalone dataset producers are optional: stopped is visible but does
  not block scenario-runner publication.
- **Scenario readiness** runs strict frozen-package validation, including mounted
  external assets and registered checksums, without starting the replay.
- **Dashboard freshness** preserves and displays the last successful Kafka,
  MinIO, and TGNN update times. A failed current check does not erase the last
  success; it changes the freshness state to degraded or unavailable.

Every non-healthy component includes an actionable message. Absence of an
exception is insufficient by itself: containers must report their runtime and
healthcheck state, required workers must actually be running, required paths
must exist, and strict scenario validation must pass.

The default dashboard page is a common operating picture for the frozen
Louisiana flood scenario. It is designed to answer four questions in order:

1. What happened?
2. What is at risk?
3. Why is it considered at risk?
4. Which evidence is real, simulated, or model-derived?

## Presentation flow

The page presents:

1. an interactive OpenStreetMap basemap;
2. the real frozen OSM structural layer;
3. accepted YOLO damage observations;
4. checkpoint-backed TGNN relative-risk scores;
5. the five highest-ranked nodes and an explicit highest-risk marker;
6. confirmed social hotspots and a pending-report review queue;
7. the real SpaceNet pre/post change panel and toggleable map-grid overlay;
8. start, pause, resume, reset, and speed controls for the scenario runner;
9. provenance badges, layer controls, legend, and freshness timestamps;
10. persistent manual building classification.

Every evidence area has loading, empty, or error behavior. Kafka failures do
not erase the structural baseline; the page reports partial data and continues
showing what is available.

## Cached operational projection

The Operations page reads `GET /api/operations/snapshot`. One response contains
the GIS/risk projection, scenario clock, isolated scenario social workflow,
latest satellite evidence, building summary, map configuration, evidence
freshness, and source-specific errors. The scenario timestamp is captured once,
so all evidence in a response uses the same replay cutoff.

Operations does not depend on bounded Kafka tails. A local SQLite projection at
`.runtime/dashboard-projection.sqlite3` incrementally consumes each configured
partition from its durable next-offset checkpoint and stores source events by
`(topic, partition, offset)`. Repeated reads are idempotent, and a dashboard
restart reloads the complete materialized evidence before rebuilding graph state.
The detailed AI and satellite pages may still use short bounded-tail caches for
display-only tables; those tails are not the operational source of truth.

The graph projection keys TGNN inference by all materialized AI-observation and
telemetry inputs; a display-only building classification revision refreshes
overlays without rerunning the model. Accepted observation IDs, applied telemetry
IDs, social reviews, partition checkpoints, and replay boundaries are durable.
Snapshot, materialization, health, Parquet, and object-list responses expose
cache-hit, cache-timestamp, cache-age, and request-duration diagnostics.

Docker, Kafka-offset, and MinIO health are cached independently. Routine status
checks use `HeadBucket` plus canonical counts from the deduplication index; they
do not enumerate the bucket. Full MinIO listing remains available through the
Media page and is itself briefly cached. Parquet previews are also briefly cached
while the Spark detail page is open.

## View-scoped refresh behavior

The browser refreshes lightweight global health plus only the currently active
view. Operations, Spark, Drone/YOLO, Satellite, Social Audit, Buildings, Media,
and Logs each have a registered view loader and an in-view refresh button.
Satellite evidence, the building inventory, and the MinIO browser therefore
update automatically while visible, but never poll in the background merely
because their pages exist.

Auto-refresh is visible and configurable at 5, 10, 30, or 60 seconds. Switching
it off stops scheduled polling. Polling also pauses whenever the browser tab is
hidden and resumes with one current-view refresh when the tab becomes visible.
Requests use cancellation keys and timeouts so a newer refresh supersedes an
older request instead of overlapping it. A failed refresh leaves the last
successful rendering in place and marks it stale.

The header reports the successful **UI refresh time**. Operations separately
reports the latest **source event time**, immutable **graph snapshot time**, and
the time the current **TGNN inference** was produced. These timestamps describe
different stages and must not be interpreted as interchangeable freshness.

## Observer and operator boundary

The dashboard has two access modes. A **read-only observer** can view maps,
evidence, status, results, diagnostics, and API documentation. An
**authenticated operator** can additionally start or stop infrastructure and
workers, control scenario playback, review social reports, and assign building
classifications.

For simple development, an unconfigured dashboard grants operator access only
when the HTTP `Host` is `localhost`, `127.0.0.1`, or `::1`. Any ngrok or network
hostname remains read-only unless `DASHBOARD_OPERATOR_TOKEN` is set in the
untracked `.env` file. The operator enters that token in the dashboard; it is
kept only in page memory and sent as a bearer credential. It is not stored in
Git, local storage, session storage, URLs, or dashboard responses.

All POST routes share one server-side operator dependency. Mutations also
require the custom `X-Dashboard-Request` marker, and browser requests with an
`Origin` header must match the request `Host`. This blocks anonymous control and
ordinary cross-site form submissions; disabling buttons in observer mode is an
additional UI safeguard, not the security boundary.

Local MinIO console links are hidden when the dashboard is opened through a
non-loopback hostname. Object evidence remains available through the safe
dashboard preview API, and MinIO access keys, secret keys, and root passwords
are never returned by the access or status contracts. Detailed backend failures
are written to local logs while clients receive generic error messages.

## Social alert scope isolation

The dashboard presents independent workflows for the active scenario and for
general, non-scenario monitoring. Their source events and responder decisions
are persisted in the dashboard projection database. The scenario endpoint only
ingests records whose `scenario_id` matches the active replay; the general view
excludes those records. Responder decisions are routed to the one workflow that
owns the alert ID, and a cross-scope ID collision is rejected instead of being
resolved ambiguously. Reset creates a new replay generation and records current
Kafka offsets as its lower boundary. It does not delete Kafka or materialized
production history; earlier replay generations remain auditable.

The Operations queue and Social Audit queue use the same pending-alert renderer
and responder-decision handler. A decision refreshes both views. Confirmed
hotspots are rendered only by the georeferenced MapLibre layer on Operations;
there is no second static or manually bounded hotspot map. Social Audit can
switch between the isolated active-scenario and general-monitoring scopes and
shows lifecycle counts, duplicates, contradictions, responder identity, review
time, and exact GIS-source-to-graph-node associations.

## Building classification workflow

Building classification uses one movable component and one save workflow. A
building click on the Operations MapLibre layer opens the component as the
selected-feature panel. The component immediately shows its current assigned
type, operator, update time, graph node, and a collapsible append-only revision
history. The Buildings view is a searchable inventory, filtered by OSM source
ID, assigned type, or classified/unclassified status; selecting an inventory
row mounts that same component rather than opening a second form.

After a revision is saved, the structural map feature and corresponding risk
feature metadata are updated immediately and then reconciled with the cached
operational snapshot. Revisions continue to be appended to the SQLite ledger.
The frozen OSM GeoJSON is never rewritten.

## TGNN relative-risk presentation

TGNN output is displayed as a **Relative risk score** on a 0.000–1.000 scale.
The dashboard does not append a percent sign or present the value as a
real-world likelihood. Rank and change from the preceding graph snapshot are
shown alongside current damage, current stress, and status. Those state values
provide operational context only; they are not feature attributions. The model
also uses temporal and graph context.

An accessible explanation tooltip and visible calibration warning accompany the
top-five list, and the same warning appears in each risk-node map popup. Scores
are suitable only for relative ranking and temporal comparison.

## Drone damage-analysis presentation

The Damage Analysis view deliberately separates three kinds of evidence:

1. **Held-out test metrics** are the primary evaluation cards and come from the
   committed artifact manifest's held-out split.
2. **Training-run metrics** are shown in a secondary panel as offline validation
   evidence. They are never labelled as live accuracy.
3. **Live scenario inference** contains the actual checkpoint outcomes consumed
   from `ai-analysis-results`.

The page identifies the evaluation dataset, model architecture, and committed
checkpoint SHA-256. Live outcomes are cards rather than a wide raw table. Each
card shows its scenario time, detection classes, maximum confidence, target
association, GPS provenance, and processing status. The evidence thumbnail
links to the existing safe MinIO preview route when a canonical drone object is
available; a missing object remains an explicit **Image unavailable** state.

Class, target, confidence, and processing-status filters apply only to live
results. Analyzed, duplicate/skipped, filtered, and failed outcomes use distinct
status styling. Detection boxes are expandable, and the complete event JSON is
retained in a developer-only drill-down for contract inspection.

## Backend contracts

`GET /api/operations` and `GET /api/operations/snapshot` rebuild the projection
from all materialized `ai-analysis-results` and `infrastructure-telemetry` events
in the active replay generation. They use the existing:

```text
AI event -> observation validation -> GIS association -> observation log
telemetry event -> graph-state adapter
ordered graph snapshots -> committed TGNN checkpoint
graph + scores -> WGS84 GeoJSON overlay
```

It does not insert invented dashboard results. The TGNN scores are calculated
from the committed checkpoint and remain explicitly labelled as uncalibrated
relative scores, not probabilities.

Scenario control uses:

```http
GET  /api/scenario
POST /api/scenario/start
POST /api/scenario/pause
POST /api/scenario/resume
POST /api/scenario/reset
POST /api/scenario/speed
```

Starting the runner performs strict asset validation and publishes through the
same Kafka producer contracts used by normal drone, satellite, social, and
telemetry inputs. Configure `SPACENET8_DATASET_PATH` and `ISBDA_DATASET_PATH`
before starting it.

## Map layers and provenance

| Layer | Meaning | Provenance |
| --- | --- | --- |
| Structural GIS | Roads and building footprints | Real frozen OSM snapshot |
| Damage observations | Checkpoint-generated YOLO detections | Real ISBDA imagery; simulated Louisiana position and scenario time |
| TGNN risk | Relative node ranking from ordered graph snapshots | Model-derived, trained on synthetic simulations, uncalibrated |
| Social hotspots | Responder-confirmed reports | Simulated scenario report text, position, target, and time |
| Satellite change | Broad-area radiometric difference | Real SpaceNet pair; simulated scenario time |
| Telemetry | Load, capacity, damage, and recovery state | Simulated scenario telemetry |

Satellite change remains separate from TGNN node features.

The operational map renders the georeferenced 8x8 SpaceNet change cells below
the structural roads and buildings. Cell color represents the existing
`low`/`moderate`/`severe` radiometric-change categories, and opacity increases
with `diff_score`. Selecting a cell shows its score, grid position, and
provenance. A compact map card summarizes changed versus scored cells and
explicitly warns that this is broad-area radiometric change, not confirmed
damage to a particular road or building. The **Satellite change** layer toggle
controls this overlay without affecting the separate pre/post evidence panel.

## Basemap requirements

The browser loads MapLibre GL JS and OpenStreetMap raster tiles. Internet
access is required for the default external library and basemap tiles. A custom
MapLibre style can be supplied through `MAP_STYLE_URL`; structural and evidence
overlays still come from the local dashboard API.
