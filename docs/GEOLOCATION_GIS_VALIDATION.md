# Geolocation and GIS association validation

The association path is:

```text
YOLO detection + explicitly provenanced GPS
  -> WGS84 validation
  -> projected working CRS
  -> building coverage or nearest-road lookup
  -> stable GIS source ID
  -> integer graph node ID
  -> idempotent observation log
```

Automated tests cover:

- a point inside a building;
- a point near a road;
- a point equidistant between multiple road candidates;
- no candidate within the 150-metre fallback radius;
- non-finite and out-of-range coordinates;
- duplicate observation delivery;
- exact building and road-distance boundaries.

Building coverage uses `covers`, making footprint boundaries inclusive. If
overlapping footprints cover the same point, the smallest footprint wins and
stable GIS source ID breaks equal-area ties. Equal-distance roads are also
resolved by stable source ID. No-candidate detections are counted and skipped;
invalid coordinates fail before any observation is appended.

## Live scenario trace

The live Kafka result for real ISBDA image `6_270.jpg` contained 10 detections.
All 10 followed this trace:

```text
MinIO object:
drone/67/67d3bbb81c5b5b10a61246b5c7164011b4d9b50aef8c6ce19f170728639cbb87_6_270.jpg

GPS: -90.08543330768804, 29.763329185970875
association: building
GIS source ID: osm-way-1064972993
graph node ID: 19
graph node gis_source_id: osm-way-1064972993
```

Replaying the same 10 detections appended zero new observations and reported
10 duplicates. Observation IDs derive from the source content hash plus the
detection index, so every accepted record traces back to its original image.
