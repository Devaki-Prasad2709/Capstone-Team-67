"""
gis_overlay.py

Converts the current graph state (+ optional TGNN predictions) into the
GeoJSON contracts the dashboard already expects (see the dashboard
requirements spec, section 4.1 `/api/overlay/nodes`).

This is the SINGLE EXIT POINT for coordinate conversion: working CRS -> WGS84.
Nowhere else in the codebase should do this conversion (mirrors gis_loader.py
being the single entry point for WGS84 -> working CRS).
"""

import time

from core.gis.gis_loader import working_to_wgs84


def build_node_overlay(G, predicted_risk: dict = None) -> dict:
    """
    G: current graph (G(t)), node attrs include type/pos/damage/stress/status
       where `pos` is (x, y) in G.graph["working_crs"]. CRS metadata is
       required so graphs from different regions cannot be silently misread.
    predicted_risk: optional {node_id: risk_float} from the TGNN. If omitted,
       `predicted_risk` is left out of a node's properties entirely (the
       dashboard spec says: render as neutral/gray, not zero-risk, for a
       node that hasn't been scored yet).

    Returns a GeoJSON FeatureCollection matching the dashboard's
    `/api/overlay/nodes` contract exactly.
    """
    predicted_risk = predicted_risk or {}
    features = []
    working_crs = G.graph.get("working_crs")
    if not working_crs:
        raise ValueError('Overlay requires G.graph["working_crs"] from the GIS context')

    for node_id, data in G.nodes(data=True):
        x, y = data["pos"]
        lon, lat = working_to_wgs84(x, y, working_crs)

        properties = {
            "id": str(node_id),
            "node_type": data["type"],
            "damage": round(data["damage"], 4),
            "stress": round(data["stress"], 4),
            "last_updated": None,  # fill with real tick timestamp when available
        }
        if node_id in predicted_risk:
            properties["predicted_risk"] = round(predicted_risk[node_id], 4)

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": properties,
        })

    return {"type": "FeatureCollection", "features": features}


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from core.gis.gis_loader import load_gis
    from graphs.gis_graph_builder import build_graph_from_gis
    import json

    data = load_gis("gis/fixtures/demo_gis.geojson")
    G, _id_map = build_graph_from_gis(data, seed=42)
    overlay = build_node_overlay(G)
    print(json.dumps(overlay, indent=2)[:1500], "\n...")
