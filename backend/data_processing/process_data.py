"""
process_data.py
---------------
Student 1 - Data Processing & Analysis

What this script does:
  1. Loads all collected OSM JSON files
  2. Cleans and deduplicates each dataset
  3. Divides Pune into a grid and counts infrastructure per cell
  4. Computes density metrics (hospital, school, building, traffic)
  5. Detects underserved areas (grids lacking hospitals within 3 km)
  6. Saves structured outputs for the backend API to serve

Output files (in ../data/processed/):
  - hospitals_clean.json
  - schools_clean.json
  - traffic_nodes_clean.json
  - buildings_clean.json
  - pharmacies_clean.json
  - grid_analysis.json        ← density per grid cell
  - underserved_areas.json    ← areas lacking infrastructure
  - city_stats.json           ← summary statistics

Usage:
    python process_data.py
"""

import json
import os
import math
from collections import defaultdict

# ─── CONFIG ───────────────────────────────────────────────────────────────────
INPUT_DIR  = "../data"
OUTPUT_DIR = "../data/processed"

# Pune bounding box
PUNE_BOUNDS = {
    "south": 18.40,
    "west":  73.75,
    "north": 18.65,
    "east":  73.98
}

# Grid resolution — divides Pune into ~25x25 = 625 cells
# Each cell is roughly 1.1km x 1.1km
GRID_ROWS = 25
GRID_COLS = 25

# Distance threshold for "underserved" detection (in km)
HOSPITAL_COVERAGE_KM  = 3.0
SCHOOL_COVERAGE_KM    = 2.0
PHARMACY_COVERAGE_KM  = 1.5

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_json(filename):
    filepath = os.path.join(INPUT_DIR, filename)
    if not os.path.exists(filepath):
        print(f"  ⚠️  File not found: {filepath}")
        return []
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)

def save_json(data, filename):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size = len(data) if isinstance(data, list) else len(data.get("grid_cells", data))
    print(f"  💾 {filename}  ({size} records)")

def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two lat/lon points in kilometres."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def latlon_to_cell(lat, lon):
    """Return (row, col) grid index for a given lat/lon."""
    b = PUNE_BOUNDS
    row = int((lat - b["south"]) / (b["north"] - b["south"]) * GRID_ROWS)
    col = int((lon - b["west"])  / (b["east"]  - b["west"])  * GRID_COLS)
    row = max(0, min(GRID_ROWS - 1, row))
    col = max(0, min(GRID_COLS - 1, col))
    return row, col

def cell_center(row, col):
    """Return the lat/lon of the center of a grid cell."""
    b = PUNE_BOUNDS
    lat = b["south"] + (row + 0.5) * (b["north"] - b["south"]) / GRID_ROWS
    lon = b["west"]  + (col + 0.5) * (b["east"]  - b["west"])  / GRID_COLS
    return round(lat, 5), round(lon, 5)

def cell_id(row, col):
    return f"{row:02d}_{col:02d}"

# ─── STEP 1: CLEAN DATA ───────────────────────────────────────────────────────

def clean_dataset(records, category):
    """
    Remove records:
      - missing lat/lon
      - outside Pune bounding box
      - duplicate OSM ids
    """
    b = PUNE_BOUNDS
    seen_ids = set()
    cleaned  = []

    for r in records:
        lat = r.get("lat")
        lon = r.get("lon")

        # Must have valid coordinates
        if lat is None or lon is None:
            continue

        # Must be inside Pune bounds
        if not (b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]):
            continue

        # No duplicate IDs
        rid = r.get("id")
        if rid in seen_ids:
            continue
        seen_ids.add(rid)

        cleaned.append(r)

    removed = len(records) - len(cleaned)
    print(f"  {category:<20} {len(records):>7} raw  →  {len(cleaned):>7} clean"
          f"  (removed {removed} duplicates/out-of-bounds)")
    return cleaned

# ─── STEP 2: BUILD GRID ───────────────────────────────────────────────────────

def build_grid(hospitals, schools, traffic_nodes, buildings, pharmacies):
    """
    For each grid cell compute:
      - count of each infrastructure type
      - center lat/lon
      - density scores (normalised 0–100)
    """
    grid = defaultdict(lambda: {
        "hospitals":     0,
        "schools":       0,
        "traffic_nodes": 0,
        "buildings":     0,
        "pharmacies":    0,
    })

    for cat, records in [
        ("hospitals",     hospitals),
        ("schools",       schools),
        ("traffic_nodes", traffic_nodes),
        ("buildings",     buildings),
        ("pharmacies",    pharmacies),
    ]:
        for r in records:
            row, col = latlon_to_cell(r["lat"], r["lon"])
            grid[cell_id(row, col)][cat] += 1
            grid[cell_id(row, col)]["_row"] = row
            grid[cell_id(row, col)]["_col"] = col

    # Convert to list and add metadata
    cells = []
    for cid, counts in grid.items():
        row = counts.pop("_row", None)
        col = counts.pop("_col", None)
        if row is None:
            parts = cid.split("_")
            row, col = int(parts[0]), int(parts[1])
        lat, lon = cell_center(row, col)
        cells.append({
            "cell_id": cid,
            "row": row,
            "col": col,
            "center_lat": lat,
            "center_lon": lon,
            **counts
        })

    # Normalise counts to density scores (0–100)
    for cat in ["hospitals", "schools", "traffic_nodes", "buildings", "pharmacies"]:
        values = [c[cat] for c in cells]
        max_val = max(values) if values else 1
        for c in cells:
            c[f"{cat}_density"] = round(c[cat] / max_val * 100, 1)

    return cells

# ─── STEP 3: UNDERSERVED AREA DETECTION ──────────────────────────────────────

def find_underserved(grid_cells, hospitals, schools, pharmacies):
    """
    For each grid cell, find the nearest hospital/school/pharmacy.
    Mark as underserved if nearest facility is beyond threshold distance.
    """
    print("\n  Computing nearest-facility distances for each grid cell...")

    underserved = []

    for cell in grid_cells:
        clat = cell["center_lat"]
        clon = cell["center_lon"]

        # Nearest hospital
        nearest_hospital_km = min(
            (haversine_km(clat, clon, h["lat"], h["lon"]) for h in hospitals),
            default=999
        )

        # Nearest school
        nearest_school_km = min(
            (haversine_km(clat, clon, s["lat"], s["lon"]) for s in schools),
            default=999
        )

        # Nearest pharmacy
        nearest_pharmacy_km = min(
            (haversine_km(clat, clon, p["lat"], p["lon"]) for p in pharmacies),
            default=999
        )

        lacks_hospital  = nearest_hospital_km  > HOSPITAL_COVERAGE_KM
        lacks_school    = nearest_school_km    > SCHOOL_COVERAGE_KM
        lacks_pharmacy  = nearest_pharmacy_km  > PHARMACY_COVERAGE_KM

        # Compute overall underservice score (higher = more underserved)
        underservice_score = round(
            (nearest_hospital_km  / HOSPITAL_COVERAGE_KM  * 0.5 +
             nearest_school_km    / SCHOOL_COVERAGE_KM    * 0.3 +
             nearest_pharmacy_km  / PHARMACY_COVERAGE_KM  * 0.2), 3
        )

        record = {
            **cell,
            "nearest_hospital_km":  round(nearest_hospital_km,  2),
            "nearest_school_km":    round(nearest_school_km,    2),
            "nearest_pharmacy_km":  round(nearest_pharmacy_km,  2),
            "lacks_hospital":       lacks_hospital,
            "lacks_school":         lacks_school,
            "lacks_pharmacy":       lacks_pharmacy,
            "underservice_score":   underservice_score,
            "is_underserved":       lacks_hospital or lacks_school or lacks_pharmacy,
        }
        underserved.append(record)

    # Sort — most underserved first
    underserved.sort(key=lambda x: x["underservice_score"], reverse=True)
    return underserved

# ─── STEP 4: CITY-LEVEL STATS ─────────────────────────────────────────────────

def compute_city_stats(hospitals, schools, traffic_nodes, buildings, pharmacies, underserved):
    total_cells    = GRID_ROWS * GRID_COLS
    underserved_h  = sum(1 for u in underserved if u["lacks_hospital"])
    underserved_s  = sum(1 for u in underserved if u["lacks_school"])
    underserved_p  = sum(1 for u in underserved if u["lacks_pharmacy"])
    fully_underserved = sum(1 for u in underserved if u["is_underserved"])

    return {
        "city": "Pune",
        "bbox": PUNE_BOUNDS,
        "grid_size": f"{GRID_ROWS}x{GRID_COLS}",
        "total_grid_cells": total_cells,
        "infrastructure_counts": {
            "hospitals":     len(hospitals),
            "schools":       len(schools),
            "traffic_nodes": len(traffic_nodes),
            "buildings":     len(buildings),
            "pharmacies":    len(pharmacies),
        },
        "coverage_thresholds_km": {
            "hospital":  HOSPITAL_COVERAGE_KM,
            "school":    SCHOOL_COVERAGE_KM,
            "pharmacy":  PHARMACY_COVERAGE_KM,
        },
        "underserved_cells": {
            "no_hospital_within_3km":  underserved_h,
            "no_school_within_2km":    underserved_s,
            "no_pharmacy_within_1_5km": underserved_p,
            "any_underserved":         fully_underserved,
            "pct_underserved":         round(fully_underserved / total_cells * 100, 1),
        }
    }

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Urban Intelligence Dashboard — Data Processing")
    print("=" * 55)

    # ── Load ──────────────────────────────────────────────────────────────────
    print("\n[1/5] Loading raw data...")
    hospitals     = load_json("pune_hospitals.json")
    schools       = load_json("pune_schools.json")
    traffic_nodes = load_json("pune_traffic_nodes.json")
    buildings     = load_json("pune_buildings.json")
    pharmacies    = load_json("pune_pharmacies.json")

    # ── Clean ─────────────────────────────────────────────────────────────────
    print("\n[2/5] Cleaning and deduplicating...")
    hospitals     = clean_dataset(hospitals,     "hospitals")
    schools       = clean_dataset(schools,       "schools")
    traffic_nodes = clean_dataset(traffic_nodes, "traffic_nodes")
    buildings     = clean_dataset(buildings,     "buildings")
    pharmacies    = clean_dataset(pharmacies,    "pharmacies")

    # ── Save clean datasets ───────────────────────────────────────────────────
    print("\n[3/5] Saving clean datasets...")
    save_json(hospitals,     "hospitals_clean.json")
    save_json(schools,       "schools_clean.json")
    save_json(traffic_nodes, "traffic_nodes_clean.json")
    save_json(buildings,     "buildings_clean.json")
    save_json(pharmacies,    "pharmacies_clean.json")

    # ── Grid analysis ─────────────────────────────────────────────────────────
    print("\n[4/5] Building grid and computing density...")
    grid_cells = build_grid(hospitals, schools, traffic_nodes, buildings, pharmacies)
    save_json(grid_cells, "grid_analysis.json")
    print(f"  Grid: {GRID_ROWS}×{GRID_COLS} = {len(grid_cells)} populated cells")

    # ── Underserved detection ─────────────────────────────────────────────────
    print("\n[5/5] Detecting underserved areas...")
    underserved = find_underserved(grid_cells, hospitals, schools, pharmacies)
    save_json(underserved, "underserved_areas.json")

    # ── City stats ────────────────────────────────────────────────────────────
    stats = compute_city_stats(
        hospitals, schools, traffic_nodes, buildings, pharmacies, underserved
    )
    save_json(stats, "city_stats.json")

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  RESULTS SUMMARY")
    print("=" * 55)
    ic = stats["infrastructure_counts"]
    for k, v in ic.items():
        print(f"  {k:<20} {v:>7,} records")
    uc = stats["underserved_cells"]
    print(f"\n  Underserved grid cells:")
    print(f"    No hospital within 3km  : {uc['no_hospital_within_3km']:>4} cells")
    print(f"    No school within 2km    : {uc['no_school_within_2km']:>4} cells")
    print(f"    No pharmacy within 1.5km: {uc['no_pharmacy_within_1_5km']:>4} cells")
    print(f"    Any underserved         : {uc['any_underserved']:>4} cells "
          f"({uc['pct_underserved']}% of city grid)")
    print(f"\n✅ Processing complete!")
    print(f"📁 Outputs saved to: {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()
