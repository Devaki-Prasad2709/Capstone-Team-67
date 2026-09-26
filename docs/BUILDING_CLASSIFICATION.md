# Persistent manual building classification

Responders can assign an operational type to a building selected by its stable
GIS source ID. The assignment is an overlay on the frozen OpenStreetMap
snapshot; the raw GeoJSON is never rewritten.

```text
dashboard selection
  -> POST classification API
  -> append-only SQLite revision
  -> GIS association / graph copy / building overlay
  -> dashboard retrieval
```

## Stored record

Each revision contains:

- `building_source_id`: stable source ID from the frozen GIS snapshot;
- `assigned_type`: normalized lowercase type, such as `field_hospital`;
- `operator`: responder or operator identifier;
- `timestamp`: server-generated ISO-8601 timestamp;
- `notes`: optional operator notes;
- `revision_id` and per-building `revision_number`.

Reclassification appends a revision instead of overwriting the old one. The
latest revision is used for current GIS and graph retrieval, while the full
history remains auditable.

## Persistence and configuration

The default database is:

```text
storage/gis/building_classifications.sqlite3
```

Set `BUILDING_CLASSIFICATION_DATABASE_PATH` to move it. Relative paths resolve
from the repository root. The database, WAL, and shared-memory files are local
runtime state and are ignored by Git. Preserve or back up this database when
moving a deployment; deleting it intentionally resets all manual assignments.

The store validates building IDs against the loaded GIS snapshot. Unknown IDs
are rejected, which prevents classifications from silently attaching to the
wrong scenario.

## Dashboard and API

Open the **Buildings** page in the dashboard, select a building, enter a type,
operator, and optional notes, then save. The page shows the current assignment,
exact graph node, and complete revision history.

The same workflow is available through the API:

```http
GET  /api/buildings
GET  /api/buildings/{building_source_id}
POST /api/buildings/{building_source_id}/classification
```

Example request body:

```json
{
  "assigned_type": "emergency shelter",
  "operator": "responder-17",
  "notes": "Primary evacuation point"
}
```

The response exposes the normalized assignment (`emergency_shelter`), its
revision metadata, and the building's current graph representation.

## Retrieval behavior

- Point-in-polygon association returns the current classification with the GIS
  source ID and graph node ID.
- Graph enrichment deep-copies the baseline graph and adds classification
  fields to building nodes only; the baseline graph remains unchanged.
- The building GeoJSON overlay includes the latest classification for map and
  dashboard clients.
- A process restart opens the same SQLite ledger and restores the latest state
  and all earlier revisions.
