
"""

ingest.py



The missing bridge identified in your handoff summary (section22): a function that

takes a real `ai-analysis-results` Kafka event -- the ACTUAL contract your

YOLO worker already produces -- and turns each detection inside it into a

geolocated, node-associated, logged Observation.



This is deliberately a PURE FUNCTION with no Kafka dependency, per your own

"pure core first, Kafka adapter second" plan. A Kafka consumer later just

does:



    for message in consumer:

        ingest_ai_analysis_result(json.loads(message.value), geolocator, spatial_index, obs_log)



and nothing in this file changes when that wrapper is added.



WHAT THIS DOES NOT DO: it does not geolocate. It does not fabricate GPS. It

calls whatever GeoLocator implementation is injected -- today that's

ManualOverrideGeoLocator (test-only), tomorrow it's a real

telemetry-based implementation. This file is 100% swappable on that point

without changes here.

"""



from dataclasses import dataclass, field

import math

from typing import Optional, Callable



from core.observation.geolocator import Detection, GeoLocator

from core.observation.observation_log import ObservationRecord, ObservationLog

from core.gis.gis_loader import wgs84_to_working

from core.gis.building_association import SpatialIndex





# ---------------------------------------------------------------------------

# Class-name normalization

# ---------------------------------------------------------------------------

# The existing ai-analysis-results contract (per your handoff notes) has been

# observed with BOTH real class names ("Slight"/"Severe"/"Debris") and

# placeholder names ("damage_class_1"/"damage_class_2") depending on which

# part of the pipeline generated the sample. The Observation Log's

# SEVERITY_MAP (in observation_log.py) only recognizes the real class names.

#

# This table is the ONE place that reconciles the two. Update it the moment

# you confirm the production worker's actual class_name strings -- do not

# scatter this mapping into multiple files.

# AUTHORITATIVE, confirmed against the actual YOLO26s training script

# (train_isbda_yolo26s.py, ISBDA+RescueNet, 3-class model):

#     0 = Slight, 1 = Severe, 2 = Debris

# The diagnostic inference script's CLASS_NAMES dict confirms class_name

# strings are already exactly "Slight"/"Severe"/"Debris" -- so this is now

# an identity mapping, not a guess. The two placeholder entries from the

# earlier investigation (damage_class_1/2) are REMOVED: they were an

# unconfirmed assumption from a different sample payload and are no longer

# needed now that the real model's class list is authoritative.

#

# ONE THING STILL UNCONFIRMED: whether the actual production event

# producer (not yet located -- see the search task) emits class_name as

# these exact strings, or emits only class_id (0/1/2) with class_name

# constructed elsewhere, or something else. This table assumes the

# ai-analysis-results contract's class_name field matches the diagnostic

# script's convention. If the real producer turns out to only supply

# class_id, add an INT_CLASS_ID_MAP here rather than guessing string

# formatting -- do not modify this table again until that's confirmed.

CLASS_NAME_NORMALIZATION = {

    "Slight": "Slight",

    "Severe": "Severe",

    "Debris": "Debris",

}



# Fallback in case the real producer supplies class_id (int) instead of, or

# in addition to, class_name. Matches the authoritative YOLO26s mapping.

# Not yet wired into detection_from_ai_result_entry() below -- add that

# once the real producer's actual field is confirmed, so this isn't

# integrated against a guess.

CLASS_ID_MAP = {

    0: "Slight",

    1: "Severe",

    2: "Debris",

}





def normalize_class_name(raw_class_name: str) -> str:

    normalized = CLASS_NAME_NORMALIZATION.get(raw_class_name)

    if normalized is None:

        print(f"[ingest] WARNING: unrecognized class_name '{raw_class_name}' -- "

              f"treating as zero-severity. Add it to CLASS_NAME_NORMALIZATION "

              f"if it should count as damage evidence.")

        return raw_class_name  # passed through; SEVERITY_MAP will default it to 0.0

    return normalized





@dataclass

class IngestResult:

    """What `ingest_ai_analysis_result` reports back, so a caller (test

    script or future Kafka consumer) can log/monitor without needing to

    inspect the ObservationLog directly."""

    frame_id: str

    detections_seen: int

    detections_ingested: int

    detections_skipped_no_node: int

    touched_node_ids: list

    detections_duplicate: int = 0

    accepted_observation_ids: list = field(default_factory=list)





def detection_from_ai_result_entry(event: dict, entry: dict) -> Detection:

    """

    event: the top-level ai-analysis-results dict (has frame_id, timestamp, etc.)

    entry: one item from event["detections"]



    Maps directly from the real contract fields -- see the schema in your

    handoff notes:

        event: {frame_id, source, source_topic, source_timestamp, ...,

                detections: [{class_id, class_name, confidence, bbox}]}

    """

    return Detection(

        frame_id=event["frame_id"],

        class_id=entry["class_id"],

        class_name=normalize_class_name(entry["class_name"]),

        confidence=entry["confidence"],

        bbox=tuple(entry["bbox"]),

        timestamp=event.get("source_timestamp") or event.get("processed_at"),

        track_id=None,  # tracking not yet implemented -- see geolocator.py note

    )





def ingest_ai_analysis_result(

    event: dict,

    geolocator: GeoLocator,

    spatial_index: SpatialIndex,

    obs_log: ObservationLog,

    drone_telemetry: Optional[dict] = None,

    timestamp_to_epoch: Optional[Callable[[str], float]] = None,

) -> IngestResult:

    """

    Processes ONE ai-analysis-results event (which may contain multiple

    detections) into the Observation Log.



    drone_telemetry: passed straight through to geolocator.locate(). With

        the current ManualOverrideGeoLocator this MUST contain

        {"override_lat": ..., "override_lon": ...} -- i.e. today, every

        call site still has to say where the drone was, because no real

        telemetry-based geolocation exists yet. This is intentionally

        loud rather than silently defaulted.



    timestamp_to_epoch: converts the event's timestamp string into a unix

        epoch float for ObservationRecord/decay math. Defaults to

        `float(timestamp)` if the contract already uses epoch seconds as a

        string; pass a real parser (e.g. `datetime.fromisoformat(...).timestamp()`)

        if the production contract uses ISO-8601 strings instead -- confirm

        the actual format against a live sample before wiring this to Kafka.

    """

    if timestamp_to_epoch is None:

        timestamp_to_epoch = lambda ts: float(ts)



    detections = event.get("detections", [])

    touched = []

    ingested = 0

    skipped = 0

    duplicates = 0

    accepted_ids = []



    if event.get("status") != "analyzed":

        # Matches the existing contract's status/reason fields -- don't

        # treat a failed/skipped frame as evidence of anything.

        return IngestResult(

            frame_id=event.get("frame_id", "unknown"),

            detections_seen=len(detections),

            detections_ingested=0,

            detections_skipped_no_node=len(detections),

            touched_node_ids=[],

            detections_duplicate=0,

            accepted_observation_ids=[],

        )



    for i, entry in enumerate(detections):

        detection = detection_from_ai_result_entry(event, entry)



        geo = geolocator.locate(detection, drone_telemetry=drone_telemetry)

        if (
            isinstance(geo.latitude, bool)
            or isinstance(geo.longitude, bool)
            or not isinstance(geo.latitude, (int, float))
            or not isinstance(geo.longitude, (int, float))
            or not math.isfinite(geo.latitude)
            or not math.isfinite(geo.longitude)
            or not -90 <= geo.latitude <= 90
            or not -180 <= geo.longitude <= 180
        ):
            raise ValueError("Detection GPS must be finite WGS84 latitude/longitude")



        working_x, working_y = wgs84_to_working(
            geo.longitude, geo.latitude, spatial_index.gis_data.working_crs
        )

        resolved = spatial_index.resolve(working_x, working_y)



        if resolved["node_id"] is None:

            skipped += 1

            continue



        try:

            ts_epoch = timestamp_to_epoch(detection.timestamp)

        except (TypeError, ValueError):

            print(f"[ingest] WARNING: could not parse timestamp "

                  f"'{detection.timestamp}' for frame {detection.frame_id}; "

                  f"skipping this detection rather than guessing a time.")

            skipped += 1

            continue



        observation_key = event.get("content_hash") or detection.frame_id
        record = ObservationRecord(

            observation_id=f"{observation_key}_{i}",

            timestamp=ts_epoch,

            frame_id=detection.frame_id,

            track_id=detection.track_id,

            source=event.get("source", "drone"),

            class_id=detection.class_id,

            class_name=detection.class_name,

            confidence=detection.confidence,

            bbox=detection.bbox,

            latitude=geo.latitude,

            longitude=geo.longitude,

            working_x=working_x,

            working_y=working_y,

            building_id=resolved["building_id"],

            node_id=resolved["node_id"],

            image_reference=entry.get("image_reference"),

            scenario_timestamp=entry.get("scenario_timestamp"),

            location_provenance=entry.get("gps_provenance"),

            declared_target_id=(entry.get("target_association") or {}).get("declared_target_id"),

            target_association_provenance=(entry.get("target_association") or {}).get("provenance"),

            matched_gis_source_id=resolved.get("gis_source_id"),

            association_kind=resolved.get("association_kind"),

            association_distance_m=resolved.get("distance_m"),

            source_content_hash=event.get("content_hash"),

        )

        if not obs_log.add(record):

            duplicates += 1

            continue

        touched.append(resolved["node_id"])

        accepted_ids.append(record.observation_id)

        ingested += 1



    return IngestResult(

        frame_id=event.get("frame_id", "unknown"),

        detections_seen=len(detections),

        detections_ingested=ingested,

        detections_skipped_no_node=skipped,

        touched_node_ids=touched,

        detections_duplicate=duplicates,

        accepted_observation_ids=accepted_ids,

    )

