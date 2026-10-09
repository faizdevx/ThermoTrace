"""Group detections into thermal *sources* (spatial clusters accumulated over the window)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from .sites import EARTH_R

CLUSTER_EPS_M = 1000.0      # detections within ~1 km (about 2-3 VIIRS pixels) belong to one source


def cluster_detections(det: pd.DataFrame, eps_m: float = CLUSTER_EPS_M) -> pd.DataFrame:
    """Add ``cluster`` (int) to ``det`` and return it. Connected components at ``eps_m``."""
    if det.empty:
        return det.assign(cluster=pd.Series(dtype=int))
    coords = np.radians(det[["latitude", "longitude"]].to_numpy())
    labels = DBSCAN(eps=eps_m / EARTH_R, min_samples=1, metric="haversine", algorithm="ball_tree").fit_predict(coords)
    return det.assign(cluster=labels)


def _extent_m(lat: np.ndarray, lon: np.ndarray) -> float:
    dy = (lat.max() - lat.min()) * 111_320.0
    dx = (lon.max() - lon.min()) * 111_320.0 * np.cos(np.radians(lat.mean()))
    return float(np.hypot(dx, dy))


def summarise(det: pd.DataFrame, window_days: int, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per cluster with the features the classifier uses."""
    now = now or det["acq_datetime"].max()
    rows = []
    for cid, g in det.groupby("cluster"):
        days = g["acq_datetime"].dt.date.nunique()
        last = g["acq_datetime"].max()
        recent = g[g["acq_datetime"] >= last - pd.Timedelta(hours=24)]
        rows.append({
            "cluster": int(cid),
            "lat": float(np.average(g.latitude, weights=g.frp.clip(lower=0.1))),
            "lon": float(np.average(g.longitude, weights=g.frp.clip(lower=0.1))),
            "n_detections": int(len(g)),
            "n_days": int(days),
            "window_days": int(window_days),
            "first_seen": g["acq_datetime"].min(),
            "last_seen": last,
            "hours_since_last": float((now - last).total_seconds() / 3600),
            "frp_mean": float(g.frp.mean()),
            "frp_max": float(g.frp.max()),
            "frp_total": float(g.frp.sum()),
            "frp_recent_max": float(recent.frp.max()),
            "night_fraction": float((g.daynight == "N").mean()),
            "extent_m": _extent_m(g.latitude.to_numpy(), g.longitude.to_numpy()),
            "ti4_mean": float(g.bright_ti4.mean()),
            "high_conf_fraction": float((g.confidence == "high").mean()),
            "instruments": ",".join(sorted(g.instrument.unique())),
        })
    return pd.DataFrame(rows)
