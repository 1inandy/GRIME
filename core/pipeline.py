"""
GRIME Core Pipeline
DEM → fill → flow direction → accumulation → stream network → candidate sites

This is the foundation. Get this working FIRST before anything else.
"""

import json
import argparse
import numpy as np
import geopandas as gpd
from shapely.geometry import LineString, Point, shape
from pathlib import Path

from core import ELLERBE_BBOX, UTM_CRS, WGS84


def fetch_dem(bbox, resolution=10):
    """
    Fetch DEM from USGS 3DEP via py3dep, then **reproject to UTM** (C1).

    The DEM is requested in WGS84, but pysheds D8/accumulation, catchment area, and
    channel slope are all metre-based. On a degree grid `pixel_size = transform[0]`
    is ~0.00009°, so catchment area collapsed to the 0.01 km² clamp and the 100 m
    slope reach walked ~1.1M cells off the bottom of the DEM. Reprojecting to UTM
    (metre, near-square cells) before any hydrology fixes both C1a and C1b.
    """
    import py3dep

    print(f"Fetching DEM for bbox={bbox} at {resolution}m resolution...")
    dem = py3dep.get_map("DEM", bbox, resolution=resolution, crs=WGS84)
    # Reproject to UTM so downstream pixel maths are in metres.
    dem = dem.rio.reproject(UTM_CRS)
    px = abs(dem.rio.transform()[0])
    print(f"  DEM shape: {dem.shape}, range: [{float(dem.min()):.1f}, {float(dem.max()):.1f}]m, "
          f"pixel≈{px:.1f} m ({UTM_CRS})")
    return dem


def process_hydrology(dem_raster, bbox):
    """
    Run pysheds hydrological processing:
    fill_pits → fill_depressions → resolve_flats → flowdir → accumulation

    Returns (grid, fdir, acc) objects.
    """
    from pysheds.grid import Grid

    print("Running hydrological processing...")

    # Initialize grid from the DEM raster
    # py3dep returns xarray DataArray — extract values and affine
    values = dem_raster.values.squeeze()
    transform = dem_raster.rio.transform()
    crs_str = str(dem_raster.rio.crs)

    grid = Grid()
    grid.add_gridded_data(
        values, data_name="dem",
        affine=transform,
        crs=crs_str,
        nodata=dem_raster.rio.nodata or -9999
    )

    # Condition the DEM
    print("  Filling pits...")
    pit_filled = grid.fill_pits("dem")
    grid.add_gridded_data(pit_filled, data_name="pit_filled", affine=transform, crs=crs_str)

    print("  Filling depressions...")
    flooded = grid.fill_depressions("pit_filled")
    grid.add_gridded_data(flooded, data_name="flooded", affine=transform, crs=crs_str)

    print("  Resolving flats...")
    inflated = grid.resolve_flats("flooded")
    grid.add_gridded_data(inflated, data_name="inflated", affine=transform, crs=crs_str)

    print("  Computing flow direction (D8)...")
    fdir = grid.flowdir("inflated")
    grid.add_gridded_data(fdir, data_name="fdir", affine=transform, crs=crs_str)

    print("  Computing flow accumulation...")
    acc = grid.accumulation("fdir")
    grid.add_gridded_data(acc, data_name="acc", affine=transform, crs=crs_str)

    max_acc = float(np.nanmax(acc))
    print(f"  Max accumulation: {max_acc:.0f} cells")

    return grid, fdir, acc, values, transform


def extract_streams(grid, fdir, acc, threshold=500):
    """
    Extract stream network as GeoJSON-like dict from flow accumulation.
    threshold: minimum accumulation cells to be considered a stream.
    """
    print(f"Extracting stream network (threshold={threshold})...")
    streams = grid.extract_river_network("fdir", "acc", threshold=threshold)
    n_features = len(streams.get("features", []))
    print(f"  Extracted {n_features} stream segments")
    return streams


def streams_to_geodataframe(streams_geojson, source_crs=WGS84):
    """Convert pysheds stream GeoJSON dict to a GeoDataFrame."""
    features = streams_geojson.get("features", [])
    if not features:
        return gpd.GeoDataFrame()

    geometries = [shape(f["geometry"]) for f in features]
    gdf = gpd.GeoDataFrame({"geometry": geometries}, crs=source_crs)
    return gdf


def generate_candidates(stream_gdf, spacing_m=200, buffer_m=20):
    """
    Generate candidate trap sites by sampling points along the stream network.
    spacing_m: distance between candidates along each stream segment.
    buffer_m: snap to nearest stream point.

    Returns GeoDataFrame of candidate points with stream metadata.
    """
    stream_utm = stream_gdf.to_crs(UTM_CRS) if str(stream_gdf.crs) != UTM_CRS else stream_gdf

    candidates = []
    for idx, row in stream_utm.iterrows():
        line = row.geometry
        order = int(row.get("stream_order", 2))  # carry order onto candidates (H3)
        if line.length < spacing_m:
            # Short segment — place one candidate at midpoint
            pt = line.interpolate(0.5, normalized=True)
            candidates.append({
                "geometry": pt,
                "segment_id": idx,
                "position_along": 0.5,
                "segment_length_m": line.length,
                "stream_order": order,
            })
        else:
            n_points = max(1, int(line.length / spacing_m))
            for i in range(n_points):
                frac = (i + 0.5) / n_points
                pt = line.interpolate(frac, normalized=True)
                candidates.append({
                    "geometry": pt,
                    "segment_id": idx,
                    "position_along": frac,
                    "segment_length_m": line.length,
                    "stream_order": order,
                })

    cand_gdf = gpd.GeoDataFrame(candidates, crs=UTM_CRS)
    print(f"Generated {len(cand_gdf)} candidate sites (spacing={spacing_m}m)")
    return cand_gdf


def compute_stream_order(stream_gdf, snap_m=5.0):
    """
    Compute a **stream order** = confluence node-degree heuristic (H3).

    This is NOT true Strahler order (which needs directed downstream topology and
    the "two order-i meet → i+1" rule); it is a junction-degree proxy capped at 5,
    so it is named `stream_order` / documented as such rather than "Strahler".
    Endpoints are snapped to a ~`snap_m` metre grid (was 0.1 m, which fragmented
    the graph so most segments fell to order 1).
    """
    import networkx as nx

    stream_utm = stream_gdf.to_crs(UTM_CRS) if str(stream_gdf.crs) != UTM_CRS else stream_gdf.copy()

    def snap(pt):
        return (round(pt[0] / snap_m) * snap_m, round(pt[1] / snap_m) * snap_m)

    G = nx.Graph()
    endpoints = {}
    for idx, row in stream_utm.iterrows():
        coords = list(row.geometry.coords)
        start = snap(coords[0])
        end = snap(coords[-1])
        G.add_edge(start, end, segment_id=idx, length=row.geometry.length)
        endpoints[idx] = (start, end)

    # Degree-based heuristic: confluence degree → order, capped at 5.
    orders = {}
    for idx in stream_utm.index:
        start, end = endpoints[idx]
        deg = max(G.degree(start), G.degree(end))
        orders[idx] = 1 if deg <= 1 else min(deg, 5)

    stream_utm["stream_order"] = stream_utm.index.map(orders).fillna(1).astype(int)
    return stream_utm


# Backwards-compat alias (the field/function was renamed from "strahler"; H3).
compute_strahler_order = compute_stream_order


def compute_catchment_area(grid, fdir, acc, candidate_row, candidate_col, pixel_size_m):
    """Compute upstream catchment area in km² for a candidate point."""
    acc_value = acc[candidate_row, candidate_col]
    area_km2 = acc_value * (pixel_size_m ** 2) / 1e6
    return max(area_km2, 0.01)


def run_pipeline(bbox=ELLERBE_BBOX, resolution=10, threshold=500,
                 spacing_m=200, output_dir="mock_data"):
    """
    Full pipeline: DEM → streams → candidates → GeoJSON output.
    This is what you run before the hackathon to validate everything.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch DEM
    dem = fetch_dem(bbox, resolution=resolution)

    # Step 2: Hydrological processing
    grid, fdir, acc, elevation, transform = process_hydrology(dem, bbox)

    # Step 3: Extract stream network
    streams = extract_streams(grid, fdir, acc, threshold=threshold)

    # Save raw stream network
    stream_path = output_path / "streams.geojson"
    with open(stream_path, "w") as f:
        json.dump(streams, f)
    print(f"Saved stream network to {stream_path}")

    # Step 4: Convert to GeoDataFrame and compute stream order (H3 heuristic)
    stream_gdf = streams_to_geodataframe(streams)
    if stream_gdf.empty:
        print("ERROR: No streams extracted. Try lowering the threshold.")
        return None, None

    stream_gdf = compute_stream_order(stream_gdf)

    # Step 5: Generate candidate sites (carrying stream_order onto each candidate)
    candidates = generate_candidates(stream_gdf, spacing_m=spacing_m)

    # Add pixel coordinates for DEM lookups. C1: the DEM grid is now UTM (metres),
    # and generate_candidates already works in UTM — so map candidates straight onto
    # the metric affine, no WGS84 round-trip, and pixel_size is metres.
    pixel_size = abs(transform[0])
    for idx, row in candidates.iterrows():
        pt = row.geometry  # UTM
        col = int((pt.x - transform[2]) / transform[0])
        r = int((pt.y - transform[5]) / transform[4])
        r = np.clip(r, 0, elevation.shape[0] - 1)
        col = np.clip(col, 0, elevation.shape[1] - 1)

        # Lat/lon for API lookups (StreamStats etc.) computed once per candidate.
        pt_wgs = gpd.GeoDataFrame(
            {"geometry": [pt]}, crs=UTM_CRS
        ).to_crs(WGS84).geometry.iloc[0]

        candidates.at[idx, "pixel_row"] = int(r)
        candidates.at[idx, "pixel_col"] = int(col)
        candidates.at[idx, "elevation_m"] = float(elevation[int(r), int(col)])
        candidates.at[idx, "accumulation"] = float(acc[int(r), int(col)])
        candidates.at[idx, "catchment_area_km2"] = compute_catchment_area(
            grid, fdir, acc, int(r), int(col), pixel_size
        )
        candidates.at[idx, "lat"] = pt_wgs.y
        candidates.at[idx, "lon"] = pt_wgs.x

    # Save candidates
    cand_wgs = candidates.to_crs(WGS84)
    cand_path = output_path / "candidates.geojson"
    cand_wgs.to_file(cand_path, driver="GeoJSON")
    print(f"Saved {len(candidates)} candidates to {cand_path}")

    return stream_gdf, candidates


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GRIME DEM Pipeline")
    parser.add_argument("--bbox", type=str, default="-79.05,35.90,-78.75,36.05",
                        help="Bounding box: west,south,east,north")
    parser.add_argument("--resolution", type=int, default=10, help="DEM resolution in meters")
    parser.add_argument("--threshold", type=int, default=500, help="Flow accumulation threshold")
    parser.add_argument("--spacing", type=int, default=200, help="Candidate spacing in meters")
    parser.add_argument("--output", type=str, default="mock_data", help="Output directory")

    args = parser.parse_args()
    bbox = tuple(float(x) for x in args.bbox.split(","))

    run_pipeline(
        bbox=bbox,
        resolution=args.resolution,
        threshold=args.threshold,
        spacing_m=args.spacing,
        output_dir=args.output,
    )
