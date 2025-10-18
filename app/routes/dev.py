from flask import Blueprint, render_template, Response, abort, current_app, jsonify, request
import os
import math
import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import ijson  # type: ignore
except Exception:  # pragma: no cover
    ijson = None

bp = Blueprint("dev", __name__)


@bp.get("/dev")
def home_page():
    return render_template("dev.html", title="WellBeingVilnius")


# Helpers for heat-points endpoint

def _static_data_path(rel_name: str) -> str:
    root = current_app.root_path
    return os.path.join(root, "static", "data", rel_name)


def _coerce_lon_lat(x: Any, y: Any) -> Optional[Tuple[float, float]]:
    """Coerce inputs to (lon, lat) floats. Auto-correct if swapped. Validate ranges."""
    try:
        fx = float(x)
        fy = float(y)
    except Exception:
        return None
    # Determine likely order; prefer (lon, lat)
    if abs(fx) <= 180 and abs(fy) <= 90:
        lon, lat = fx, fy
    elif abs(fx) <= 90 and abs(fy) <= 180:
        # looks swapped (lat, lon)
        lon, lat = fy, fx
    else:
        # out of range
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (lon, lat)


def _ring_centroid(ring: Iterable[Iterable[Any]]) -> Optional[Tuple[float, float]]:
    """Centroid of a ring using the shoelace formula (planar). Input ring is list of [lon, lat]."""
    pts: List[Tuple[float, float]] = []
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            c = _coerce_lon_lat(pt[0], pt[1])
            if c is not None:
                pts.append(c)
    n = len(pts)
    if n < 3:
        # not a polygon, fallback to average
        if n == 0:
            return None
        sx = sum(p[0] for p in pts)
        sy = sum(p[1] for p in pts)
        return (sx / n, sy / n)
    # Ensure closed ring
    if pts[0] != pts[-1]:
        pts.append(pts[0])  # type: ignore
    twice_area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        cross = x0 * y1 - x1 * y0
        twice_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(twice_area) < 1e-12:
        # degenerate, fallback to average
        sx = sum(p[0] for p in pts[:-1])
        sy = sum(p[1] for p in pts[:-1])
        m = len(pts) - 1
        return (sx / m, sy / m) if m > 0 else None
    factor = 1.0 / (3.0 * twice_area)
    return (cx * factor, cy * factor)


def _avg_centroid(coords: Iterable[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    sx = sy = n = 0
    for x, y in coords:
        sx += x
        sy += y
        n += 1
    if n == 0:
        return None
    return (sx / n, sy / n)


def _geom_to_points(geom: Dict[str, Any]) -> List[Tuple[float, float]]:
    if not geom:
        return []
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    out: List[Tuple[float, float]] = []
    if gtype == "Point":
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            c = _coerce_lon_lat(coords[0], coords[1])
            if c is not None:
                lon, lat = c
                out.append((lat, lon))
    elif gtype == "MultiPoint":
        for p in coords or []:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                c = _coerce_lon_lat(p[0], p[1])
                if c is not None:
                    lon, lat = c
                    out.append((lat, lon))
    elif gtype == "Polygon":
        ring = (coords[0] if coords and isinstance(coords, list) and coords else [])
        centroid = _ring_centroid(ring)
        if centroid:
            lon, lat = centroid
            out.append((lat, lon))
    elif gtype == "MultiPolygon":
        if coords and isinstance(coords, list):
            for poly in coords:
                if isinstance(poly, list) and poly:
                    ring = poly[0]
                    centroid = _ring_centroid(ring)
                    if centroid:
                        lon, lat = centroid
                        out.append((lat, lon))
    elif gtype == "GeometryCollection":
        for g in geom.get("geometries", []) or []:
            out.extend(_geom_to_points(g))
    return out


def _pick_weight_key(props: Dict[str, Any], preferred: Optional[str]) -> Optional[str]:
    if preferred and preferred in props:
        try:
            if math.isfinite(float(props.get(preferred))):
                return preferred
        except Exception:
            pass
    for k in ("population", "pop", "density", "value", "count"):
        try:
            if k in props and math.isfinite(float(props.get(k))):
                return k
        except Exception:
            pass
    for k, v in props.items():
        try:
            if math.isfinite(float(v)):
                return k
        except Exception:
            continue
    return None


@bp.get("/dev/heat-points")
def heat_points() -> Response:
    """Return sampled heat points from a large GeoJSON in static/data.

    Query params:
    - file: filename under static/data (default population_ltu_2019-07-01.geojson)
    - max: max number of points (default 50000)
    - method: 'reservoir' or 'first' (default 'reservoir')
    - weight: property to use as weight (optional)
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
    reservoir: List[Tuple[float, float, float]] = []  # (lat, lon, weight)
    total_seen = 0
    chosen_weight_key: Optional[str] = None

    try:
        with open(path, "rb") as f:
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
                        continue
                    if max_w_val is not None and weight_val is not None and weight_val > max_w_val:
                        weight_val = max_w_val

                    pts = _geom_to_points(geom)
                    for lat, lon in pts:
                        w = weight_val if (weight_val is not None and math.isfinite(weight_val)) else 1.0
                        total_seen += 1
                        if len(reservoir) < max_points:
                            reservoir.append((lat, lon, w))
                        else:
                            if method == "reservoir":
                                j = rng.randint(0, total_seen - 1)
                                if j < max_points:
                                    reservoir[j] = (lat, lon, w)
                            else:  # 'first'
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
