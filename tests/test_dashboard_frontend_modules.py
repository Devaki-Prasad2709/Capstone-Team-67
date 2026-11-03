"""Static regression checks for the framework-free dashboard module boundary."""

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "dashboard" / "static"


def test_frontend_is_split_by_user_facing_responsibility():
    expected = {
        "api-client.js",
        "router.js",
        "operations-map.js",
        "scenario-controls.js",
        "social-alerts.js",
        "satellite-evidence.js",
        "building-classifications.js",
        "diagnostics.js",
    }
    assert expected <= {path.name for path in (STATIC / "modules").glob("*.js")}
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '<script type="module" src="/app.js?v=' in (STATIC / "index.html").read_text(encoding="utf-8")
    assert all(f"./modules/{name}" in app for name in expected)


def test_navigation_does_not_abort_global_scenario_registry_request():
    api_client = (STATIC / "modules" / "api-client.js").read_text(encoding="utf-8")
    router = (STATIC / "modules" / "router.js").read_text(encoding="utf-8")
    scenarios = (STATIC / "modules" / "scenario-controls.js").read_text(encoding="utf-8")

    assert "global:scenarios" in scenarios
    assert "startsWith('global:')" in api_client
    assert "cancelRequests({includeGlobal: false})" in router
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (!$('#scenarioSelector')?.options.length) work.push(loadScenarioRegistry())" in app


def test_dynamic_actions_use_delegation_not_inline_handlers():
    sources = [STATIC / "index.html", STATIC / "app.js", *(STATIC / "modules").glob("*.js")]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert not re.search(r"\bonclick\s*=", combined, flags=re.IGNORECASE)
    assert ".onclick" not in combined
    assert "addEventListener" in combined
    assert "data-alert-action" in combined
    assert "data-process-action" in combined


def test_accessible_status_keyboard_and_responsive_contracts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    router = (STATIC / "modules" / "router.js").read_text(encoding="utf-8")
    assert 'aria-label="Primary dashboard navigation"' in html
    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert ":focus-visible" in css
    assert "@media (max-width: 720px)" in css
    assert "@media (min-width: 1600px)" in css
    assert "ArrowDown" in router and "ArrowUp" in router


def test_maplibre_runtime_assets_are_local_and_pinned():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    vendor = STATIC / "vendor"
    assert 'href="/vendor/maplibre-gl.css"' in html
    assert 'src="/vendor/maplibre-gl.js"' in html
    assert "unpkg.com/maplibre" not in html
    assert (vendor / "maplibre-gl.js").stat().st_size > 500_000
    assert (vendor / "maplibre-gl.css").stat().st_size > 20_000
    assert "4.7.1" in (vendor / "README.md").read_text(encoding="utf-8")


def test_large_renderers_skip_unchanged_html_and_escape_dynamic_content():
    core = (STATIC / "modules" / "core.js").read_text(encoding="utf-8")
    modules = "\n".join(path.read_text(encoding="utf-8") for path in (STATIC / "modules").glob("*.js"))
    assert "setHTMLIfChanged" in core
    assert "renderCache" in core
    assert modules.count("setHTMLIfChanged") >= 15
    assert modules.count("escapeHtml") >= 15


def test_risk_scores_are_legible_and_satellite_overlay_clears_without_current_result():
    css = (STATIC / "operations.css").read_text(encoding="utf-8")
    satellite = (STATIC / "modules" / "satellite-evidence.js").read_text(
        encoding="utf-8"
    )
    assert "button.risk-row" in css
    assert "grid-template-columns: 34px minmax(0, 1fr) minmax(108px, auto)" in css
    assert "background: #0d192a !important" in css
    assert "color: #ffbd70 !important" in css
    assert "font-size: 18px" in css
    assert "lastOperationalSatelliteEvidence" not in satellite
    assert "awaiting current replay evidence" not in satellite
    assert "setMapData?.('satellite-change', {type: 'FeatureCollection', features: []})" in satellite
    assert "card?.classList.add('hidden')" in satellite


def test_drone_evidence_does_not_add_a_second_circle_over_the_risk_node():
    operations = (STATIC / "modules" / "operations-map.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "id: 'damage-observations'" not in operations
    assert "map.removeLayer('damage-observations')" in operations
    assert "map.removeSource('damage')" in operations
    assert 'data-map-layer="damage"' not in html
    assert "drone/YOLO detection" not in html
    assert "TGNN risk (teal → yellow → orange)" in html
    assert "'#ff6d75'" not in operations
    assert "'#d3133a'" not in operations


def test_map_building_footprints_and_risk_nodes_share_classification_workflow():
    operations = (STATIC / "modules" / "operations-map.js").read_text(
        encoding="utf-8"
    )

    assert "function buildingSourceId" in operations
    assert "properties.building_id" in operations
    assert "properties.building_id || properties.gis_source_id" in operations
    assert "state.buildings.some" in operations
    assert "function selectMapBuilding" in operations
    assert "selectOperationalBuilding(sourceId)" in operations
    assert "'structural-buildings', event =>" in operations
    assert "selectMapBuilding(properties); popup" in operations
    assert "'risk-nodes', event =>" in operations
    assert "selectMapBuilding(properties); showNode" in operations


def test_minio_object_rows_have_explicit_dark_high_contrast_styles():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "diagnostics.css").read_text(encoding="utf-8")

    assert "/diagnostics.css?v=20261103-minio-contrast-1" in html
    assert "button.object-row" in css
    assert "background: #0d192a" in css
    assert "color: var(--text)" in css
    assert "button.object-row strong" in css
    assert "color: #eef5ff" in css
    assert "button.object-row:focus-visible" in css
