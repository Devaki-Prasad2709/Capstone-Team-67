# Drone/YOLO integration validation

Validated live on 2026-09-26 using the frozen Louisiana scenario, local Kafka,
MinIO, and the committed serving checkpoint. No detection arrays are stored in
the scenario package or substituted by the runner.

## Verified route

```text
ISBDA JPEG
  -> drone producer
  -> SHA-256/pHash deduplicator
  -> MinIO object + drone-video event
  -> YOLO26s worker
  -> ai-analysis-results
  -> observation consumer
```

The real `5_9240.jpg` source SHA-256 and producer `content_hash` both equalled
`632ea63506f8e7a452434f8daa0caa93d2c676692966500fb993a227f5a66f52`.
The MinIO object was 666,848 bytes. Exact replay was detected by SHA-256; a
JPEG-recompressed copy had a different SHA-256 and was detected by pHash at
Hamming distance 0.

The worker loaded `best.pt` with SHA-256
`780241f6b42f9f0b0d83a8be8d3168e1ca864a756ff93907e72bbb7f9fe3cbf9`.
Live inference produced 18 Slight detections for `5_9240.jpg`. A second live
run on `6_270.jpg` produced 10 Slight/Severe detections with confidences from
0.281148 to 0.789474; the observation consumer ingested all 10.

Every emitted scenario detection contained:

- frame, MinIO object, image URI, and content-hash reference;
- model class, confidence, and bounding box;
- scenario timestamp;
- GPS plus `simulated-scenario-assignment` provenance;
- declared GIS target plus simulated-assignment provenance.

The result contract also records `live-checkpoint-inference` and
`prerecorded_output=false`. The observation consumer validates these fields,
requires explicit simulated-GPS opt-in, and stores the provenance with each
observation.
