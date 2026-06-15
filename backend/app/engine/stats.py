"""
空间统计引擎：全局空间自相关（Moran's I）与热点分析（Getis-Ord Gi*）。

对价值评估网格（或任意带数值属性的点/面集合）做：
  - 全局 Moran's I：判断高/低值是否在空间上聚集（正自相关）还是随机分布；
  - 局部 Getis-Ord Gi*：识别"热点"（高值簇）与"冷点"（低值簇）。

优先使用 esda/libpysal（PySAL 生态，课程推荐）；若环境未安装，
自动降级到本模块内置的等价 NumPy 实现，保证功能可用。
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

try:                       # 优先用 PySAL
    from esda.moran import Moran
    from esda.getisord import G_Local
    from libpysal.weights import KNN
    _HAS_PYSAL = True
except Exception:          # 环境缺失则降级
    _HAS_PYSAL = False


def _extract(fc: dict, attr: str = "score") -> tuple[np.ndarray, np.ndarray]:
    """从 FeatureCollection 提取代表点坐标与数值。"""
    coords, vals = [], []
    for f in fc.get("features", []):
        p = f["properties"]
        if "lon" in p and "lat" in p:
            coords.append([p["lon"], p["lat"]])
        else:  # 面要素取第一个外环的平均作为代表点
            ring = f["geometry"]["coordinates"][0]
            arr = np.array(ring)
            coords.append([arr[:, 0].mean(), arr[:, 1].mean()])
        vals.append(float(p.get(attr, 0)))
    return np.array(coords, dtype=float), np.array(vals, dtype=float)


def _knn_weights(coords: np.ndarray, k: int = 8) -> list[np.ndarray]:
    """K 近邻空间权重：返回每个点的邻居下标数组。"""
    tree = cKDTree(coords)
    _, idx = tree.query(coords, k=k + 1)   # 含自身
    return [row[1:] for row in idx]        # 去掉自身


def _moran_manual(vals: np.ndarray, neighbors: list[np.ndarray]) -> float:
    """手写全局 Moran's I（行标准化二值权重）。"""
    n = len(vals)
    z = vals - vals.mean()
    num, w_sum = 0.0, 0.0
    for i, nb in enumerate(neighbors):
        w = 1.0 / len(nb)
        for j in nb:
            num += w * z[i] * z[j]
            w_sum += w
    denom = (z ** 2).sum()
    return (n / w_sum) * (num / denom) if denom > 0 else 0.0


def _gistar_manual(vals: np.ndarray, neighbors: list[np.ndarray]) -> np.ndarray:
    """手写 Getis-Ord Gi*（含自身），返回 z 分数。"""
    n = len(vals)
    mean = vals.mean()
    s = vals.std()
    z = np.zeros(n)
    for i, nb in enumerate(neighbors):
        idx = np.append(nb, i)             # Gi* 含自身
        wn = len(idx)
        local_sum = vals[idx].sum()
        num = local_sum - mean * wn
        denom = s * np.sqrt((n * wn - wn ** 2) / (n - 1))
        z[i] = num / denom if denom > 0 else 0.0
    return z


def hotspot(fc: dict, attr: str = "score", k: int = 8) -> dict:
    """
    对输入要素集做 Moran's I + Gi* 热点分析。

    返回：在原 FeatureCollection 基础上，为每个要素追加 gi_z（z 分数）与
    hot_class（hot/cold/none），并在 meta 中给出全局 Moran's I 与显著性。
    """
    coords, vals = _extract(fc, attr)
    if len(vals) < k + 1:
        return {**fc, "meta": {**fc.get("meta", {}), "error": "样本过少，无法做空间统计"}}

    neighbors = _knn_weights(coords, k)

    engine = "fallback-numpy"
    moran_p = float("nan")                    # 降级实现不做置换检验
    if _HAS_PYSAL:
        try:                                  # PySAL 不仅可能抛异常，还可能在数值退化时
            import warnings                    # 静默返回 NaN（如 Gi* 的除零）——两种都要回退，
            with warnings.catch_warnings():   # 否则 NaN 进 JSON 序列化会直接 500。
                warnings.simplefilter("ignore")   # 屏蔽 esda 的 divide/Gi* 提示（噪声日志）
                w = KNN.from_array(coords, k=k)
                w.transform = "r"
                mi = Moran(vals, w)
                moran_i, moran_p = float(mi.I), float(mi.p_sim)
                gi = G_Local(vals, w, star=True)
                gi_z = np.array(gi.Zs, dtype=float)
            if not (np.isfinite(moran_i) and np.isfinite(gi_z).all()):
                raise ValueError("PySAL 返回非有限值(NaN/inf)，改用内置实现")
            engine = "PySAL"
        except Exception:
            moran_i = _moran_manual(vals, neighbors)
            moran_p = float("nan")
            gi_z = _gistar_manual(vals, neighbors)
            engine = "fallback-numpy"
    else:
        moran_i = _moran_manual(vals, neighbors)
        gi_z = _gistar_manual(vals, neighbors)

    # 最终兜底：任何残余的 NaN/inf 都清掉，确保 JSON 可序列化、绝不 500
    gi_z = np.nan_to_num(gi_z, nan=0.0, posinf=0.0, neginf=0.0)
    if not np.isfinite(moran_i):
        moran_i = 0.0

    # |z| > 1.96 对应 95% 置信度
    out_feats = []
    for f, z in zip(fc["features"], gi_z):
        cls = "hot" if z > 1.96 else ("cold" if z < -1.96 else "none")
        nf = {**f, "properties": {**f["properties"], "gi_z": round(float(z), 3), "hot_class": cls}}
        out_feats.append(nf)

    return {
        "type": "FeatureCollection",
        "features": out_feats,
        "meta": {
            "moran_I": round(moran_i, 4),
            "moran_p": (None if moran_p != moran_p else round(moran_p, 4)),
            "interpretation": ("高值/低值显著空间聚集（正自相关）" if moran_i > 0.1
                               else "趋于随机分布" if abs(moran_i) <= 0.1
                               else "高低值相邻（负自相关）"),
            "engine": engine,
            "n_hot": int((gi_z > 1.96).sum()),
            "n_cold": int((gi_z < -1.96).sum()),
        },
    }
