from __future__ import annotations

import math
import os
import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Blueprint, Response, abort, current_app, jsonify, request

try:
    import ijson  # type: ignore
except Exception as e:  # pragma: no cover
    ijson = None  # will error on first use with a clear message

bp = Blueprint("heat", __name__)


def _static_data_path(rel_name: str) -> str:
    root = current_app.root_path  # app package path
    return os.path.join(root, "static", "data", rel_name)


def _avg_centroid(coords: Iterable[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """Simple centroid approximation: mean of vertices (lon,lat)."""
    sx = sy = n = 0
    for x, y in coords:
        if math.isfinite(x) and math.isfinite(y):
            sx += x
            sy += y
            n += 1
    if n == 0:
        return None
    return (sx / n, sy / n)


def _geom_to_points(geom: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Return list of (lat, lon) points from a GeoJSON geometry. For polygons, return centroid of exterior ring."""
    if not geom:
        return []
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    out: List[Tuple[float, float]] = []
    if gtype == "Point":
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            lon, lat = coords[0], coords[1]
            if math.isfinite(lon) and math.isfinite(lat):
                out.append((lat, lon))
    elif gtype == "MultiPoint":
        for p in coords or []:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                lon, lat = p[0], p[1]
                if math.isfinite(lon) and math.isfinite(lat):
                    out.append((lat, lon))
    elif gtype == "Polygon":
        # exterior ring is coords[0]
        ring = (coords[0] if coords and isinstance(coords, list) and coords else [])
        centroid = _avg_centroid(((pt[0], pt[1]) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2))
        if centroid:
            lon, lat = centroid
            out.append((lat, lon))
    elif gtype == "MultiPolygon":
        # take centroid of first polygon's exterior ring
        if coords and isinstance(coords, list) and coords and isinstance(coords[0], list) and coords[0]:
            ring = coords[0][0]
            centroid = _avg_centroid(((pt[0], pt[1]) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2))
            if centroid:
                lon, lat = centroid
                out.append((lat, lon))
    elif gtype == "GeometryCollection":
        for g in geom.get("geometries", []) or []:
            out.extend(_geom_to_points(g))
    return out


def _pick_weight_key(props: Dict[str, Any], preferred: Optional[str]) -> Optional[str]:
    if preferred and preferred in props and math.isfinite(float(props.get(preferred, float("nan")))):
        return preferred
    for k in ("population", "pop", "density", "value", "count"):
        if k in props and math.isfinite(float(props.get(k, float("nan")))):
            return k
    for k, v in props.items():
        try:
            if math.isfinite(float(v)):
                return k
        except Exception:
            continue
    return None


@bp.get("/dev/heat-points")
def heat_points() -> Response:
    """Stream a large GeoJSON file from static/data and return sampled heat points.

    Query params:
    - file: relative filename under static/data (required if default missing)
    - max: maximum number of points to return (default 50000)
    - method: 'reservoir' (default) or 'first' sampling strategy
    - weight: property name to use for weighting (optional)
    - min_weight, max_weight: clamp weights before normalization (optional)
    """
    if ijson is None:
        abort(500, description="ijson is required. Please install ijson in requirements.txt")

    filename = request.args.get("file", "population_ltu_2019-07-01.geojson")
    max_points = int(request.args.get("max", 50000))
    method = (request.args.get("method") or "reservoir").lower()
    if method not in ("reservoir", "first"):
        method = "reservoir"
    weight_key_param = request.args.get("weight")
    min_w = request.args.get("min_weight")
    max_w = request.args.get("max_weight")

    min_w_val = float(min_w) if min_w is not None else None
    max_w_val = float(max_w) if max_w is not None else None

    path = _static_data_path(filename)
    if not os.path.isfile(path):
        abort(404, description=f"File not found: {filename}")

    rng = random.Random(42)

    # Reservoir sampling containers
    reservoir: List[Tuple[float, float, float]] = []  # (lat, lon, weight)
    total_seen = 0
    chosen_weight_key: Optional[str] = None

    try:
        with open(path, "rb") as f:
            # Detect whether it's a FeatureCollection by trying to iterate features
            try:
                items = ijson.items(f, "features.item")
                for feat in items:
                    if not isinstance(feat, dict):
                        continue
                    geom = feat.get("geometry")
                    props = feat.get("properties") or {}

                    if chosen_weight_key is None:
                        chosen_weight_key = _pick_weight_key(props, weight_key_param)

                    weight_val = None
                    if chosen_weight_key is not None:
                        try:
                            weight_val = float(props.get(chosen_weight_key))
                        except Exception:
                            weight_val = None

                    if min_w_val is not None and weight_val is not None and weight_val < min_w_val:
                        # below min threshold, skip
                        continue
                    if max_w_val is not None and weight_val is not None and weight_val > max_w_val:
                        # above max threshold, cap at max to preserve inclusion
                        weight_val = max_w_val

                    pts = _geom_to_points(geom)
                    for lat, lon in pts:
                        w = weight_val if (weight_val is not None and math.isfinite(weight_val)) else 1.0
                        total_seen += 1
                        if len(reservoir) < max_points:
                            reservoir.append((lat, lon, w))
                        else:
                            if method == "reservoir":
                                # Reservoir sampling: replace element with decreasing probability
                                j = rng.randint(0, total_seen - 1)
                                if j < max_points:
                                    reservoir[j] = (lat, lon, w)
                            else:  # method == "first": stop early once we have max_points
                                break
                    if method == "first" and len(reservoir) >= max_points:
                        break
            except ijson.common.IncompleteJSONError:
                abort(400, description="Invalid GeoJSON (incomplete JSON)")
    except Exception as e:  # pragma: no cover
        abort(500, description=f"Failed to process file: {e}")

    if not reservoir:
        return jsonify({
            "points": [],
            "count": 0,
            "weightKey": chosen_weight_key,
            "total": total_seen,
        })

    # Normalize weights to [0.1, 1]
    weights = [w for _, _, w in reservoir if math.isfinite(w)]
    if weights:
        wmin = min(weights)
        wmax = max(weights)
    else:
        wmin = wmax = 1.0

    if not math.isfinite(wmin) or not math.isfinite(wmax) or wmin == wmax:
        points = [[lat, lon, 1.0] for (lat, lon, _w) in reservoir]
        norm_min = norm_max = 1.0
    else:
        span = wmax - wmin
        points = []
        for lat, lon, w in reservoir:
            i = (w - wmin) / span if math.isfinite(w) else 1.0
            i = max(0.1, min(1.0, i))
            points.append([lat, lon, i])
        norm_min, norm_max = wmin, wmax

    return jsonify({
        "points": points,
        "count": len(points),
        "weightKey": chosen_weight_key,
        "total": total_seen,
        "weightMin": norm_min,
        "weightMax": norm_max,
        "file": filename,
        "max": max_points,
        "method": method,
    })
