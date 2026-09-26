"""
landmark_alert_resolver.py

Answers your "OH NO PERSON TRAPPED UNDER BRIDGE/HOSPITAL" requirement:
takes free text, finds a landmark mention (by name or by type), resolves it
to a real node in the GIS graph, and produces a priority record the
dashboard's priority/people overlay layer can render -- attached to the
correct map location, not a guess.

DELIBERATELY NOT AN LLM. This is pattern/keyword matching + name lookup
against the SAME GIS data the Graph Builder already uses, per the locked
"no LLM anywhere" rule. It is not a general-purpose NLP model -- it is a
narrow, deterministic resolver for exactly this one task (landmark mention
-> node). Treat it as a first pass; if your real social-media text is much
messier than the patterns below anticipate, that's a sign to add more
patterns/keywords here, not to reach for an LLM.

NEVER CLAIMS "TRAPPED PERSON CONFIRMED" -- output is always framed as a
potential/priority signal with its resolution method disclosed, per the
same labeling discipline used for the drone-based priority score.

The resolved node may become a graph-linked social observation only after a
responder confirms it. It never becomes structural damage or a TGNN feature.
"""

import re
import difflib
from typing import Optional

from core.gis.gis_loader import GISData


# Keyword -> node type hint. Kept intentionally small and explicit rather
# than trying to be clever -- add entries as real report text reveals
# patterns this doesn't catch yet.
TYPE_KEYWORDS = {
    "bridge": "road",           # bridges are represented as road nodes
    "overpass": "road",
    "road": "road",
    "street": "road",
    "hospital": "hospital",
    "clinic": "hospital",
    "medical center": "hospital",
    "substation": "power",
    "power station": "power",
    "power plant": "power",
    "transformer": "power",
    "water plant": "water",
    "pump station": "water",
    "reservoir": "water",
    "cell tower": "telecom",
    "phone tower": "telecom",
    "tower": "telecom",
}

# Distress phrasing this resolver looks for. Deliberately simple/explicit
# patterns, not a trained classifier -- this is the "lightweight" end of
# the spectrum on purpose.
DISTRESS_PATTERN = re.compile(
    r"\b(trapped|stuck|stranded|buried|collapsed on|pinned)\b",
    re.IGNORECASE,
)

NAME_MATCH_THRESHOLD = 0.6  # difflib similarity ratio to accept a name match

# Only simple negation immediately before a distress phrase is handled.
# Evaluate each occurrence separately so a later positive report still counts.
NEGATED_DISTRESS_PREFIX = re.compile(
    r"\b(?:nobody|no\s+one|none|no\s+(?:people|person|persons))"
    r"(?:\s+(?:is|are|was|were|has|have|been|currently|still|actually)){0,3}\s*$"
    r"|\b(?:not|never|no\s+longer|isn't|aren't|wasn't|weren't)"
    r"(?:\s+(?:currently|still|actually|being)){0,2}\s*$",
    re.IGNORECASE,
)


def _has_distress(text: str) -> bool:
    text = text.replace("\u2019", "'")
    # "Oh no, person trapped" is an alarm, not "no person trapped".
    text = re.sub(r"\boh\s+no\b", "oh", text, flags=re.IGNORECASE)
    return any(
        not NEGATED_DISTRESS_PREFIX.search(text[:match.start()])
        for match in DISTRESS_PATTERN.finditer(text)
    )


class ResolvedPriorityAlert(dict):
    """JSON-ready record; field attribute access is retained for existing callers.

    As a dict, this passes through dashboard.services.serialize() without
    requiring dashboard-specific imports or a separate encoding step.
    """
    raw_text: str
    is_distress: bool
    node_id: Optional[int]
    resolved_via: str          # "name_match" | "type_hint_nearest" | "unresolved"
    matched_entity_name: Optional[str]
    confidence: float          # heuristic, NOT a calibrated probability -- see note below

    def __init__(self, raw_text: str, is_distress: bool, node_id: Optional[int],
                 resolved_via: str, matched_entity_name: Optional[str], confidence: float):
        super().__init__(
            raw_text=raw_text, is_distress=is_distress, node_id=node_id,
            resolved_via=resolved_via, matched_entity_name=matched_entity_name,
            confidence=confidence,
        )

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        if name not in self:
            raise AttributeError(name)
        self[name] = value


def _all_named_entities(gis_data: GISData, id_map: dict) -> list:
    """Returns [(name, node_id, node_type)] for every infra point and road
    segment that has a name -- the pool of things a text mention can match
    against by name."""
    entities = []
    for p in gis_data.infra_points:
        if p.id in id_map:
            entities.append((p.name, id_map[p.id], p.node_type))
    for r in gis_data.road_segments:
        if r.id in id_map:
            entities.append((r.name, id_map[r.id], "road"))
    return entities


def _try_name_match(text: str, entities: list):
    """Fuzzy-matches the text against every known entity name using
    difflib (stdlib, no extra dependency -- deliberately simple). Returns
    the best match above threshold, or None."""
    text_lower = text.lower()
    best = None
    best_score = 0.0

    for name, node_id, node_type in entities:
        name_lower = name.lower()
        if name_lower in text_lower:
            # Direct substring match -- strongest possible signal.
            return name, node_id, node_type, 1.0
        score = difflib.SequenceMatcher(None, name_lower, text_lower).ratio()
        if score > best_score:
            best, best_score = (name, node_id, node_type), score

    if best and best_score >= NAME_MATCH_THRESHOLD:
        return best[0], best[1], best[2], best_score
    return None


def _try_type_hint(text: str) -> Optional[str]:
    """Returns the first matching node type hint found in the text, or None."""
    text_lower = text.lower()
    for keyword, node_type in TYPE_KEYWORDS.items():
        phrase = r"\s+".join(re.escape(word) for word in keyword.split())
        if re.search(r"\b" + phrase + r"\b", text_lower):
            return node_type
    return None


def _nearest_node_of_type(node_type: str, reference_pos: tuple, id_map: dict, gis_data: GISData):
    """Falls back to 'nearest node of this type to a reference point' when
    no specific name matched. reference_pos is a (working_x, working_y)
    tuple -- e.g. the AOI centroid, or wherever the report's rough area is
    otherwise known from (city/neighborhood-level geoparsing, out of scope
    for this module -- see caller note)."""
    candidates = []
    for p in gis_data.infra_points:
        if p.node_type == node_type and p.id in id_map:
            candidates.append((id_map[p.id], p.geometry))
    if node_type == "road":
        for r in gis_data.road_segments:
            if r.id in id_map:
                candidates.append((id_map[r.id], r.geometry))

    if not candidates:
        return None

    rx, ry = reference_pos
    best_id, best_dist = None, float("inf")
    for node_id, geom in candidates:
        centroid = geom.centroid
        d = ((centroid.x - rx) ** 2 + (centroid.y - ry) ** 2) ** 0.5
        if d < best_dist:
            best_id, best_dist = node_id, d
    return best_id


def resolve_alert(
    text: str,
    gis_data: GISData,
    id_map: dict,
    reference_pos: Optional[tuple] = None,
) -> ResolvedPriorityAlert:
    """
    Main entry point. `reference_pos` is a (working_x, working_y) fallback
    point used ONLY when a type hint matches (e.g. "trapped under a
    bridge") but no specific named entity does -- without it, "nearest
    bridge to WHERE?" has no answer, so type-hint-only resolution requires
    this parameter. If you don't have a reasonable reference point (e.g.
    the disaster AOI's centroid, or the geoparsed neighborhood centroid),
    pass None -- the resolver will still attempt a name match, but will
    return unresolved rather than pick an arbitrary node of the right type.
    """
    is_distress = _has_distress(text)

    entities = _all_named_entities(gis_data, id_map)
    name_match = _try_name_match(text, entities)

    if name_match:
        name, node_id, node_type, score = name_match
        return ResolvedPriorityAlert(
            raw_text=text, is_distress=is_distress, node_id=node_id,
            resolved_via="name_match", matched_entity_name=name,
            confidence=round(min(1.0, score), 3),
        )

    type_hint = _try_type_hint(text)
    if type_hint and reference_pos is not None:
        node_id = _nearest_node_of_type(type_hint, reference_pos, id_map, gis_data)
        if node_id is not None:
            return ResolvedPriorityAlert(
                raw_text=text, is_distress=is_distress, node_id=node_id,
                resolved_via="type_hint_nearest", matched_entity_name=None,
                confidence=0.4,  # deliberately low -- type-only resolution
                                 # is a much weaker signal than a name match
            )

    return ResolvedPriorityAlert(
        raw_text=text, is_distress=is_distress, node_id=None,
        resolved_via="unresolved", matched_entity_name=None, confidence=0.0,
    )


if __name__ == "__main__":
    from pathlib import Path
    from core.gis.gis_loader import load_gis
    from core.graphs.gis_graph_builder import build_graph_from_gis

    fixture_path = Path(__file__).resolve().parents[1] / "gis" / "fixtures" / "demo_gis.geojson"
    gis_data = load_gis(str(fixture_path))
    _, id_map = build_graph_from_gis(gis_data, seed=42)

    hospital_pos = None
    for p in gis_data.infra_points:
        if p.name == "General Hospital":
            hospital_pos = (p.geometry.x, p.geometry.y)

    test_cases = [
        "OH NO PERSON TRAPPED UNDER BRIDGE NEAR GENERAL HOSPITAL",
        "person trapped inside General Hospital right now",
        "someone stuck under a bridge, not sure exactly where",
        "just checking in, everything is fine here",  # no distress, no landmark
        "3 people trapped near Substation A, please send help",
    ]

    for text in test_cases:
        result = resolve_alert(text, gis_data, id_map, reference_pos=hospital_pos)
        print(f"\ntext: '{text}'")
        print(f"  distress={result.is_distress}, node_id={result.node_id}, "
              f"via={result.resolved_via}, matched='{result.matched_entity_name}', "
              f"confidence={result.confidence}")
