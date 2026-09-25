import json
from pathlib import Path

import pytest

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from scripts.fetch_osm_scenario_gis import convert, parse_bbox
from scripts.validate_scenario import validate_scenario


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "louisiana_east_flood"


def test_final_scenario_contract_has_no_pending_assets():
    result = validate_scenario(SCENARIO / "scenario.json")
    assert result["valid"] is True
    assert result["gis_ids"] == 21
    assert result["pending_assets"] == []
    assert result["asset_count"] == 9
    assert result["timeline_events"] == 12
    assert result["stream_events"] == 7


def test_strict_scenario_gate_passes_for_frozen_asset_package():
    result = validate_scenario(SCENARIO / "scenario.json", strict=True)
    assert result["valid"] is True
    assert result["strict"] is True


def test_drone_assets_disclose_real_images_and_simulated_locations():
    selection = json.loads((SCENARIO / "drone" / "selection.json").read_text(encoding="utf-8"))
    assert {image["asset_id"] for image in selection["images"]} == {
        "drone-slight-001", "drone-severe-001", "drone-debris-001",
    }
    for image in selection["images"]:
        assert image["input_origin"] == "real-isbda-image"
        assert image["geographic_footprint"] == "not_provided_by_source_dataset"
        assert {"assigned_location", "target_id", "scenario_timestamp"} <= set(image["simulation_fields"])


def test_scenario_snapshot_builds_graph_and_preserves_stable_ids():
    data = load_gis(str(SCENARIO / "gis" / "infrastructure.geojson"))
    graph, ids = build_graph_from_gis(data, seed=42)
    assert data.working_crs.startswith("EPSG:326")
    assert graph.number_of_nodes() == 21
    assert graph.number_of_edges() > 0
    assert "osm-way-791288888" in ids
    assert "osm-way-794401251" in ids
    assert "osm-way-1064972993" in ids


def test_spacenet_pair_and_gis_share_the_scenario_footprint():
    manifest = json.loads((SCENARIO / "scenario.json").read_text(encoding="utf-8"))
    pair = json.loads((SCENARIO / "satellite" / "pair.json").read_text(encoding="utf-8"))
    gis = json.loads((SCENARIO / "gis" / "infrastructure.geojson").read_text(encoding="utf-8"))
    assert pair["tile_id"] == "2_23_44"
    assert pair["reference"]["flooded_feature_count"] == 22
    assert pair["pre_event"]["bbox"] == manifest["location"]["bbox"]
    assert gis["metadata"]["bbox"] == pytest.approx(manifest["location"]["bbox"], abs=1e-9)


def test_locked_timeline_defines_every_transition_before_runner_exists():
    timeline = json.loads((SCENARIO / "timeline.json").read_text(encoding="utf-8"))
    assert [event["sequence"] for event in timeline["events"]] == list(range(1, 13))
    for event in timeline["events"]:
        assert {"kind", "source", "reference"} <= event["input"].keys()
        assert event["target"]["ids"]
        assert event["expected_graph_effect"]["assertions"]
        assert event["expected_dashboard_effect"]["assertions"]
    cascade = timeline["events"][9]
    assert cascade["expected_graph_effect"]["mutation"] == "none"
    assert any("zero dependency edges" in item for item in cascade["expected_graph_effect"]["assertions"])


def test_primary_story_confirms_alert_and_documents_rejection_branch():
    decisions = json.loads((SCENARIO / "responder_decisions.json").read_text(encoding="utf-8"))
    assert decisions["events"][0]["action"] == "confirm"
    assert decisions["alternative_demo_branch"]["action"] == "report_false"
    assert decisions["alternative_demo_branch"]["graph_effect"] == "none"


def test_osm_converter_preserves_source_ids_and_filters_power_lines():
    payload = {"elements": [
        {"type": "node", "id": 1, "lat": 29.74, "lon": -90.02, "tags": {"power": "tower"}},
        {"type": "way", "id": 2, "center": {"lat": 29.74, "lon": -90.02}, "tags": {"power": "line"}},
        {"type": "way", "id": 3, "geometry": [{"lat": 29.74, "lon": -90.02}, {"lat": 29.741, "lon": -90.019}], "tags": {"highway": "residential"}},
    ]}
    result = convert(payload, parse_bbox("-90.032,29.730,-90.012,29.748"))
    ids = {
        props.get("infrastructure_id") or props.get("road_id")
        for props in (feature["properties"] for feature in result["features"])
    }
    assert ids == {"osm-node-1", "osm-way-3"}
