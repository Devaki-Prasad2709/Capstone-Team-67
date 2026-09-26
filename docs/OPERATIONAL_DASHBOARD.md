# Operational dashboard

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
7. the real SpaceNet pre/post change panel;
8. start, pause, resume, reset, and speed controls for the scenario runner;
9. provenance badges, layer controls, legend, and freshness timestamps;
10. persistent manual building classification.

Every evidence area has loading, empty, or error behavior. Kafka failures do
not erase the structural baseline; the page reports partial data and continues
showing what is available.

## Backend contracts

`GET /api/operations` rebuilds the current projection from the bounded tails of
`ai-analysis-results` and `infrastructure-telemetry`. It uses the existing:

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

## Basemap requirements

The browser loads MapLibre GL JS and OpenStreetMap raster tiles. Internet
access is required for the default external library and basemap tiles. A custom
MapLibre style can be supplied through `MAP_STYLE_URL`; structural and evidence
overlays still come from the local dashboard API.
