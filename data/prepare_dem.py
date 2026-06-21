"""
Prepare a GeoTIFF DEM for the flood analysis module.

The flood engine reads data/<area>/dem.npy. It can resample the DEM at runtime,
but preparing it at the usual analysis resolution keeps results fastest and
most predictable.

Usage:
    python data/prepare_dem.py hangzhou_core path/to/dem.tif --resolution 100
    python data/prepare_dem.py hangzhou_core --resolution 100
    python data/prepare_dem.py all --resolution 100

When the GeoTIFF path is omitted, the script reads data/<area>/dem.tif, then
falls back to data/dem.tif.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "backend" / "app" / "config.py"


def _load_areas() -> dict:
    """Read AREAS from backend/app/config.py without importing backend deps."""
    tree = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"), filename=str(CONFIG_PATH))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "AREAS":
                    return ast.literal_eval(node.value)
    raise SystemExit(f"Could not find AREAS in {CONFIG_PATH}")


AREAS = _load_areas()


def bbox_grid(bbox: list[float], n: int) -> tuple[np.ndarray, np.ndarray]:
    min_lon, min_lat, max_lon, max_lat = bbox
    lon_edges = np.linspace(min_lon, max_lon, n + 1)
    lat_edges = np.linspace(min_lat, max_lat, n + 1)
    lon_centers = (lon_edges[:-1] + lon_edges[1:]) / 2
    lat_centers = (lat_edges[:-1] + lat_edges[1:]) / 2
    grid_lon, grid_lat = np.meshgrid(lon_centers, lat_centers)
    return grid_lon.ravel(), grid_lat.ravel()


def _import_rasterio():
    try:
        import rasterio
        from rasterio.warp import transform
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: rasterio\n"
            "Install it in the environment you use for this project, for example:\n"
            "  python -m pip install rasterio\n"
            "Then rerun this script."
        ) from exc
    return rasterio, transform


def _fill_nodata(dem: np.ndarray) -> np.ndarray:
    """Fill sampled nodata gaps with nearest valid values, then mean fallback."""
    invalid = ~np.isfinite(dem)
    if not invalid.any():
        return dem

    valid = ~invalid
    if not valid.any():
        raise SystemExit("The sampled DEM contains no valid elevation values.")

    try:
        from scipy import ndimage

        _, indices = ndimage.distance_transform_edt(invalid, return_indices=True)
        return dem[tuple(indices)]
    except ImportError:
        filled = dem.copy()
        filled[invalid] = float(np.nanmean(dem))
        return filled


def convert_dem(area: str, src_tif: Path, resolution: int, out_npy: Path) -> np.ndarray:
    if area not in AREAS:
        valid = ", ".join(sorted(AREAS))
        raise SystemExit(f"Unknown area '{area}'. Valid areas: {valid}")
    if not src_tif.exists():
        raise SystemExit(f"GeoTIFF not found: {src_tif}")

    rasterio, transform = _import_rasterio()
    bbox = AREAS[area]["bbox"]
    lons, lats = bbox_grid(bbox, resolution)

    with rasterio.open(src_tif) as ds:
        if ds.crs is None:
            raise SystemExit(f"GeoTIFF has no CRS metadata: {src_tif}")

        if ds.crs.to_string() == "EPSG:4326":
            xs, ys = lons, lats
        else:
            xs_list, ys_list = transform("EPSG:4326", ds.crs, lons.tolist(), lats.tolist())
            xs = np.asarray(xs_list, dtype=float)
            ys = np.asarray(ys_list, dtype=float)

        samples = list(ds.sample(zip(xs, ys), indexes=1, masked=True))
        values = np.ma.asarray(samples, dtype=float).reshape(resolution, resolution)
        dem = values.filled(np.nan)

        if ds.nodata is not None:
            dem = np.where(dem == ds.nodata, np.nan, dem)

    dem = _fill_nodata(dem).astype(np.float32)
    out_npy.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_npy, dem)
    return dem


def default_src_tif(area: str) -> Path:
    area_tif = DATA_DIR / area / "dem.tif"
    if area_tif.exists():
        return area_tif
    return DATA_DIR / "dem.tif"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a GeoTIFF DEM to data/<area>/dem.npy.")
    parser.add_argument(
        "area",
        choices=["all", *sorted(AREAS)],
        help="Project area id, e.g. hangzhou_core, or 'all' for every configured area.",
    )
    parser.add_argument(
        "src_tif",
        nargs="?",
        type=Path,
        help="Source DEM GeoTIFF. Defaults to data/<area>/dem.tif, then data/dem.tif.",
    )
    parser.add_argument(
        "--resolution",
        "-r",
        type=int,
        default=100,
        help="Output grid size. Matching the usual flood resolution is recommended. Default: 100.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output .npy path. Defaults to data/<area>/dem.npy.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.resolution < 2:
        raise SystemExit("--resolution must be >= 2")

    if args.area == "all" and args.output:
        raise SystemExit("--output can only be used when preparing one area.")

    targets = sorted(AREAS) if args.area == "all" else [args.area]
    for area in targets:
        src_tif = args.src_tif or default_src_tif(area)
        out_npy = args.output or (DATA_DIR / area / "dem.npy")
        dem = convert_dem(area, src_tif, args.resolution, out_npy)

        valid = np.isfinite(dem)
        print(f"Wrote: {out_npy}")
        print(f"Area: {area}")
        print(f"Shape: {dem.shape}")
        print(f"Valid cells: {int(valid.sum())}/{dem.size}")
        print(f"Elevation range: {float(np.nanmin(dem)):.2f} m to {float(np.nanmax(dem)):.2f} m")
        print("-" * 48)
    print("Restart the backend, then use the same flood resolution.")


if __name__ == "__main__":
    main()
