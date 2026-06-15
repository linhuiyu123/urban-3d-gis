"""
把内置样例数据落地为磁盘上的 GeoJSON 文件（可选）。

后端在文件缺失时会自动在内存里生成同样的数据，所以本脚本并非必需；
但如果你想查看 / 手动编辑这些数据，运行它即可把文件写到 data/<city>/。

用法：
    cd data
    python generate_sample.py
"""
import json
import sys
from pathlib import Path

# 让脚本能 import 到 backend.app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import CITIES, DATA_DIR          # noqa: E402
from app.sample_data import generate_all          # noqa: E402


def main():
    for city in CITIES:
        data = generate_all(city)
        out_dir = DATA_DIR / city
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, fc in [("pois", data["pois"]), ("roads", data["roads"]),
                         ("shelters", data["shelters"]), ("water", data["water"])]:
            path = out_dir / f"{name}.geojson"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fc, f, ensure_ascii=False, indent=1)
            print(f"  写出 {path}  （{len(fc['features'])} 个要素）")
        print(f"[完成] {CITIES[city]['name']}")


if __name__ == "__main__":
    main()
