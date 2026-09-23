# Responder review contract

`AlertStore(graph)` snapshots node positions and `graph.graph["working_crs"]`.
Use one store per GIS/graph context; integer node IDs are local to that context.
The existing NLP resolver is unchanged. Nothing writes to G(t), ObservationLog,
or TGNN. No new dependency or HTTP endpoint is introduced.

```python
from core.nlp.alert_state import AlertStore

store = AlertStore(graph)
store.create_alert("alert-1", resolved_alert, source="social", source_id="post-123")
store.approve_alert("alert-1", responder_id="authenticated-responder-id")
# Alternatively, while still pending:
# store.report_false("alert-1", responder_id="authenticated-responder-id")
payload = store.dashboard_state()
```

`create_alert` optionally accepts a caller-supplied `priority` string; no priority
is inferred. Original resolver fields, source, source ID, timestamps and review
history are retained. NLP confidence is heuristic evidence, never human certainty.

Only `PENDING_REVIEW -> CONFIRMED` and `PENDING_REVIEW -> REPORTED_FALSE` are
allowed. Every repeated or conflicting decision raises `InvalidTransition`.
Unknown IDs raise `KeyError`; duplicate IDs and invalid inputs raise `ValueError`.
Failed operations do not overwrite records. Approval of unresolved/absent nodes
is rejected and leaves the report pending; reporting false remains possible.

`dashboard_state()` returns JSON-ready data:

- `pending_alerts`: list of review records for the two responder buttons.
- `confirmed_hotspots`: WGS84 GeoJSON Point FeatureCollection, confirmed only.
- `reported_false_alerts`: original reports and rejection history.

`get_alert(id)` and `list_alerts(status=None)` expose copies for audit/history.
Hotspot properties contain `hotspot_id`, `alert_id`, `node_id`, `status`,
`category`, optional `priority`, `original_text`, `nlp_confidence`, `source`,
`source_id`, `responder_confirmation`, full `report`, and `history`. Point
coordinates are longitude/latitude of the resolved graph node, not an asserted
exact incident GPS. Conversion occurs only in `confirmed_hotspots()` using the
captured working CRS and the existing GIS conversion helper.

A confirmed hotspot means **a responder confirmed the reported incident**. It is
not a collapse prediction. Render this separately from TGNN and satellite layers.

This is an in-memory component with a lock for atomic review actions in one
process. History is lost on restart. The integrating service must supply durable
storage, authenticated/authorized responder identity and cross-process concurrency
control if needed. No frontend buttons or authentication are implemented here.

Validation from the repository root (PowerShell):

```powershell
$env:SPACENET8_TEST_ROOT = 'E:\Capstone-Team-67\datasets\Satellite\spacenet8\Spacenet8_Louisiana-East_Trainingtar'
& 'D:\CAPSTONE\.venv\Scripts\python.exe' -m unittest tests.test_nlp_alert_state tests.test_gis_crs tests.test_gis_graph_builder -v
& 'D:\CAPSTONE\.venv\Scripts\python.exe' -m core.nlp.landmark_alert_resolver
git diff --check
```

The real-data smoke test is skipped when `SPACENET8_TEST_ROOT` is unset.
