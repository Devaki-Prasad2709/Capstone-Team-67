# Social/NLP responder-review workflow

The social workflow consumes the normal `social-posts` contract, validates its
optional WGS84 GPS, resolves text against the active GIS graph, and records this
explicit lifecycle:

```text
INCOMING -> PENDING_REVIEW -> CONFIRMED
                           -> REPORTED_FALSE
```

`SocialAlertWorkflow.ingest_event()` records both the incoming and pending
transitions. The two-step `AlertStore.receive_alert()` and
`AlertStore.queue_for_review()` interface remains available when a caller needs
to expose processing between those stages.

## State effects

| State | Confirmed hotspot | Graph-linked observation | Structural damage / TGNN |
|---|---:|---:|---:|
| Incoming | No | No | No |
| Pending review | No | No | No |
| Confirmed | Orange point | Yes | No |
| Reported false | No | No | No |

A confirmed graph observation records the source report, responder, review
timestamp, exact GIS source ID, and integer graph node ID. It is a human-evidence
layer, not a physical damage assertion. Rejected reports retain their original
event, NLP result, responder decision, and full transition history.

## Duplicates and contradictions

- Replaying the same source ID and content returns an explicit `duplicate`
  outcome without creating another alert.
- Identical evidence under another ID points to the first canonical alert.
- Reusing a source ID for different evidence raises `ContradictoryReport` and
  leaves the accepted record unchanged.
- Repeating a terminal decision or trying to replace confirmation with rejection
  raises `InvalidTransition` and leaves the audit record unchanged.

## Dashboard API

- `GET /api/social/alerts` reads the bounded Kafka tail, ingests unseen offsets,
  and returns all review queues, hotspots, graph observations, and audit records.
- `POST /api/social/alerts/{alert_id}/confirm`
- `POST /api/social/alerts/{alert_id}/reject`

Decision bodies use `{"responder_id": "..."}`. The dashboard Social alerts view
provides both actions and plots confirmed reports as orange markers.

State is currently held in the dashboard process. It survives repeated Kafka
tail reads through partition/offset tracking, but restarting the dashboard clears
review decisions. Production deployment should replace this adapter with durable
storage and authenticated responder identity.
