"""
geolocator.py

Pixel -> GPS geolocation interface.

There is currently NO confirmed production tracking/geolocation implementation
and NO drone telemetry (GPS, altitude, gimbal orientation, camera calibration)
available in the existing YOLO Kafka contract. Per the explicit instruction
in the project's implementation context: "Do not fabricate GPS coordinates."

This module therefore provides:
  1. `GeoLocator` -- the interface every real implementation must satisfy.
  2. `NullGeoLocator` -- raises clearly if used, so a missing real
     implementation fails loudly instead of silently returning fake data.
  3. `ManualOverrideGeoLocator` -- for local testing ONLY. Every detection
     must be given an explicit lat/lon by the caller (e.g. a test script
     hand-placing synthetic detections). It computes nothing and infers
     nothing; it exists purely so the rest of the pipeline (Observation Log,
     Graph Builder, TGNN) can be exercised end-to-end before real drone
     telemetry exists.

When real telemetry becomes available, implement a new subclass of
GeoLocator (e.g. `HomographyGeoLocator`) that uses drone GPS + altitude +
gimbal angle + camera FOV to project the bbox center into WGS84. Nothing
else in the pipeline needs to change -- Observation Log and Graph Builder
only depend on the GeoLocator interface, not on any specific implementation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math


@dataclass
class Detection:
    """Mirrors one entry from the existing `detections` array in the
    ai-analysis-results Kafka contract, plus the frame-level fields needed
    to geolocate it."""
    frame_id: str
    class_id: int
    class_name: str
    confidence: float
    bbox: tuple            # (x1, y1, x2, y2) in pixel coordinates
    timestamp: str
    track_id: str = None   # filled in by a tracker upstream, if available


@dataclass
class GeolocatedDetection:
    detection: Detection
    latitude: float
    longitude: float


class GeoLocator(ABC):
    @abstractmethod
    def locate(self, detection: Detection, drone_telemetry: dict = None) -> GeolocatedDetection:
        """
        drone_telemetry is expected to eventually contain things like:
            {"gps": (lat, lon), "altitude_m": ..., "gimbal_pitch": ...,
             "gimbal_yaw": ..., "camera_fov_deg": ..., "frame_width": ...,
             "frame_height": ...}
        Real implementations use this + the detection's bbox center to
        compute a WGS84 position. This interface intentionally does not
        assume any particular telemetry schema yet, since none is confirmed
        to exist -- adjust the signature once the real telemetry contract
        is known.
        """
        raise NotImplementedError


class NullGeoLocator(GeoLocator):
    """Fails loudly. Use this in production wiring until a real
    implementation exists, so a missing geolocation step is impossible
    to miss."""

    def locate(self, detection: Detection, drone_telemetry: dict = None) -> GeolocatedDetection:
        raise NotImplementedError(
            "No real geolocation implementation is wired in yet. "
            "Do not fabricate GPS coordinates -- implement a real "
            "GeoLocator subclass (drone GPS + altitude + gimbal + FOV) "
            "before removing this guard."
        )


class ManualOverrideGeoLocator(GeoLocator):
    """
    TEST-ONLY. The caller supplies the lat/lon explicitly (e.g. a test
    script simulating what a real drone pass would have observed at a known
    location). This never infers a position -- it only carries one through,
    so tests exercise the real Observation Log / Graph Builder / TGNN code
    paths without pretending pixel-to-GPS math exists yet.
    """

    def locate(self, detection: Detection, drone_telemetry: dict = None) -> GeolocatedDetection:
        if not drone_telemetry or "override_lat" not in drone_telemetry or "override_lon" not in drone_telemetry:
            raise ValueError(
                "ManualOverrideGeoLocator requires drone_telemetry = "
                "{'override_lat': ..., 'override_lon': ...} for test detections."
            )
        return GeolocatedDetection(
            detection=detection,
            latitude=drone_telemetry["override_lat"],
            longitude=drone_telemetry["override_lon"],
        )


class ScenarioAssignedGeoLocator(GeoLocator):
    """Carry explicitly simulated scenario GPS into an observation.

    This does not perform pixel projection and must never be presented as real
    drone telemetry. It accepts only the provenance marker emitted by the
    frozen scenario producer/YOLO contract.
    """

    def locate(self, detection: Detection, drone_telemetry: dict = None) -> GeolocatedDetection:
        if not drone_telemetry or drone_telemetry.get("gps_provenance") != "simulated-scenario-assignment":
            raise ValueError("Scenario GPS requires simulated-scenario-assignment provenance")
        gps = drone_telemetry.get("gps")
        if not isinstance(gps, dict):
            raise ValueError("Scenario GPS must be an object")
        latitude, longitude = gps.get("latitude"), gps.get("longitude")
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
        ):
            raise ValueError("Scenario GPS requires numeric latitude and longitude")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Scenario GPS is outside WGS84 bounds")
        return GeolocatedDetection(detection, float(latitude), float(longitude))
