"""
app.py
------
Student 1 - Backend API
Flask REST API serving processed Pune infrastructure data
to the frontend visualization layer.

Endpoints:
  GET /api/health                         - health check
  GET /api/city/stats                     - city-level summary stats
  GET /api/infrastructure/<category>      - points for a category
  GET /api/grid                           - full grid with density scores
  GET /api/underserved                    - underserved area analysis
  GET /api/query/underserved              - parameterised underserved query
  GET /api/summary/by-area                - infrastructure count by grid area

Usage:
    python app.py
    Server runs at http://localhost:5000
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import json
import os
import math

# ─── INIT ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)  # Allow all origins so the frontend can call freely

DATA_DIR = os.path.join(os.path.dirname(__file__), "data/processed")

# ─── DATA LOADER ──────────────────────────────────────────────────────────────
_cache = {}

def load(filename):
    """Load a processed JSON file, with in-memory caching."""
    if filename not in _cache:
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, encoding="utf-8") as f:
            _cache[filename] = json.load(f)
    return _cache[filename]

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def error(msg, code=400):
    return jsonify({"error": msg}), code

def paginate(data, request):
    """Optional pagination via ?page=1&limit=100"""
    try:
        page  = max(1, int(request.args.get("page",  1)))
        limit = max(1, min(5000, int(request.args.get("limit", 5000))))
    except ValueError:
        page, limit = 1, 5000
    start = (page - 1) * limit
    end   = start + limit
    return data[start:end], len(data), page, limit

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    """Health check — also returns what data files are available."""
    files = {
        "hospitals":     os.path.exists(os.path.join(DATA_DIR, "hospitals_clean.json")),
        "schools":       os.path.exists(os.path.join(DATA_DIR, "schools_clean.json")),
        "traffic_nodes": os.path.exists(os.path.join(DATA_DIR, "traffic_nodes_clean.json")),
        "buildings":     os.path.exists(os.path.join(DATA_DIR, "buildings_clean.json")),
        "pharmacies":    os.path.exists(os.path.join(DATA_DIR, "pharmacies_clean.json")),
        "grid":          os.path.exists(os.path.join(DATA_DIR, "grid_analysis.json")),
        "underserved":   os.path.exists(os.path.join(DATA_DIR, "underserved_areas.json")),
    }
    return jsonify({
        "status": "ok",
        "data_files": files,
        "all_ready": all(files.values())
    })


@app.route("/api/city/stats")
def city_stats():
    """
    Overall city statistics.
    Used by the dashboard summary cards.
    """
    stats = load("city_stats.json")
    if stats is None:
        return error("city_stats.json not found", 404)
    return jsonify(stats)


@app.route("/api/infrastructure/<category>")
def infrastructure(category):
    """
    Return cleaned point data for a given infrastructure category.

    Path param:
      category: hospitals | schools | traffic_nodes | buildings | pharmacies

    Query params (all optional):
      limit  (int, default 5000) - max records to return
      page   (int, default 1)    - page number

    Example:
      GET /api/infrastructure/hospitals
      GET /api/infrastructure/schools?limit=200&page=2
    """
    valid = ["hospitals", "schools", "traffic_nodes", "buildings", "pharmacies"]
    if category not in valid:
        return error(f"Invalid category. Choose from: {', '.join(valid)}")

    data = load(f"{category}_clean.json")
    if data is None:
        return error(f"{category}_clean.json not found", 404)

    items, total, page, limit = paginate(data, request)
    return jsonify({
        "category": category,
        "total":    total,
        "page":     page,
        "limit":    limit,
        "count":    len(items),
        "data":     items
    })


@app.route("/api/grid")
def grid():
    """
    Return the full 25×25 grid with infrastructure counts and density scores.

    Query params (all optional):
      category  - filter density by: hospitals | schools | traffic_nodes | buildings | pharmacies
      min_density (float 0-100) - only return cells above this density score

    Example:
      GET /api/grid
      GET /api/grid?category=hospitals&min_density=50
    """
    data = load("grid_analysis.json")
    if data is None:
        return error("grid_analysis.json not found", 404)

    category    = request.args.get("category")
    min_density = request.args.get("min_density", type=float)

    filtered = data
    if category:
        density_key = f"{category}_density"
        valid_cats  = ["hospitals", "schools", "traffic_nodes", "buildings", "pharmacies"]
        if category not in valid_cats:
            return error(f"Invalid category. Choose from: {', '.join(valid_cats)}")
        if min_density is not None:
            filtered = [c for c in filtered if c.get(density_key, 0) >= min_density]

    return jsonify({
        "total_cells": len(data),
        "filtered_cells": len(filtered),
        "grid": filtered
    })


@app.route("/api/underserved")
def underserved():
    """
    Return all grid cells with underservice analysis.

    Query params (all optional):
      type  - filter by: hospital | school | pharmacy | any
              default: any

    Example:
      GET /api/underserved
      GET /api/underserved?type=hospital
    """
    data = load("underserved_areas.json")
    if data is None:
        return error("underserved_areas.json not found", 404)

    filter_type = request.args.get("type", "any")

    if filter_type == "hospital":
        filtered = [c for c in data if c.get("lacks_hospital")]
    elif filter_type == "school":
        filtered = [c for c in data if c.get("lacks_school")]
    elif filter_type == "pharmacy":
        filtered = [c for c in data if c.get("lacks_pharmacy")]
    else:  # "any"
        filtered = [c for c in data if c.get("is_underserved")]

    return jsonify({
        "filter": filter_type,
        "total_underserved": len(filtered),
        "cells": filtered
    })


@app.route("/api/query/underserved")
def query_underserved():
    """
    THE BONUS FEATURE — natural language-style query endpoint.
    "Which areas in Pune lack hospitals within X km?"

    Query params:
      facility  (str)   - hospital | school | pharmacy  (default: hospital)
      radius_km (float) - coverage radius in km         (default: 3.0)
      lat       (float) - optional: center point lat for proximity sort
      lon       (float) - optional: center point lon for proximity sort

    Example:
      GET /api/query/underserved?facility=hospital&radius_km=3
      GET /api/query/underserved?facility=school&radius_km=2
      GET /api/query/underserved?facility=pharmacy&radius_km=1.5&lat=18.52&lon=73.85
    """
    facility  = request.args.get("facility",   "hospital")
    radius_km = request.args.get("radius_km",  3.0, type=float)
    user_lat  = request.args.get("lat",  type=float)
    user_lon  = request.args.get("lon",  type=float)

    # Load the appropriate clean facility dataset
    facility_map = {
        "hospital": "hospitals_clean.json",
        "school":   "schools_clean.json",
        "pharmacy": "pharmacies_clean.json",
    }
    if facility not in facility_map:
        return error("facility must be one of: hospital, school, pharmacy")

    facilities = load(facility_map[facility])
    grid_data  = load("grid_analysis.json")
    if facilities is None or grid_data is None:
        return error("Required data files not found", 404)

    # For each grid cell, compute nearest facility distance on the fly
    results = []
    for cell in grid_data:
        clat = cell["center_lat"]
        clon = cell["center_lon"]

        nearest_km = min(
            (haversine_km(clat, clon, f["lat"], f["lon"]) for f in facilities),
            default=999
        )

        if nearest_km > radius_km:
            result = {
                "cell_id":         cell["cell_id"],
                "center_lat":      clat,
                "center_lon":      clon,
                f"nearest_{facility}_km": round(nearest_km, 2),
                "gap_km":          round(nearest_km - radius_km, 2),
                "hospitals":       cell.get("hospitals", 0),
                "schools":         cell.get("schools", 0),
                "pharmacies":      cell.get("pharmacies", 0),
                "buildings":       cell.get("buildings", 0),
            }
            # If user provided a point, add distance from that point
            if user_lat is not None and user_lon is not None:
                result["distance_from_query_km"] = round(
                    haversine_km(user_lat, user_lon, clat, clon), 2
                )
            results.append(result)

    # Sort by gap (largest gap first = most underserved)
    results.sort(key=lambda x: x["gap_km"], reverse=True)

    return jsonify({
        "query": f"Areas lacking {facility} within {radius_km} km",
        "facility":   facility,
        "radius_km":  radius_km,
        "total_underserved_cells": len(results),
        "results": results
    })


@app.route("/api/summary/by-area")
def summary_by_area():
    """
    Infrastructure count grouped by grid area — used for bar/histogram charts.

    Query params:
      category - hospitals | schools | traffic_nodes | buildings | pharmacies
                 (default: hospitals)
      top_n    - return only top N cells by count (default: 20)

    Example:
      GET /api/summary/by-area?category=schools&top_n=15
    """
    category = request.args.get("category", "hospitals")
    top_n    = request.args.get("top_n", 20, type=int)

    valid = ["hospitals", "schools", "traffic_nodes", "buildings", "pharmacies"]
    if category not in valid:
        return error(f"Invalid category. Choose from: {', '.join(valid)}")

    grid_data = load("grid_analysis.json")
    if grid_data is None:
        return error("grid_analysis.json not found", 404)

    # Sort by count descending and take top N
    sorted_cells = sorted(grid_data, key=lambda c: c.get(category, 0), reverse=True)
    top = sorted_cells[:top_n]

    return jsonify({
        "category": category,
        "top_n":    top_n,
        "data": [
            {
                "cell_id":    c["cell_id"],
                "center_lat": c["center_lat"],
                "center_lon": c["center_lon"],
                "count":      c.get(category, 0),
                "density":    c.get(f"{category}_density", 0),
            }
            for c in top
        ]
    })


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  Urban Intelligence Dashboard — Backend API")
    print("  Running at: http://localhost:5000")
    print("=" * 55)
    print("\n  Available endpoints:")
    print("  GET /api/health")
    print("  GET /api/city/stats")
    print("  GET /api/infrastructure/<category>")
    print("  GET /api/grid")
    print("  GET /api/underserved?type=hospital")
    print("  GET /api/query/underserved?facility=hospital&radius_km=3")
    print("  GET /api/summary/by-area?category=hospitals")
    print()
    app.run(debug=True, port=5000)
