"""BYTE-style multi-object tracker for plate bounding boxes.

Keeps a stable ``track_id`` across frames so OCR majority voting fuses
reads from the *same* vehicle/plate, not neighbouring cars.

Algorithm (classic BYTE, IoU association via Hungarian / greedy fallback):
  1. Predict track boxes with a simple constant-velocity Kalman (xyah).
  2. Associate high-score detections to tracks (IoU).
  3. Second association of remaining tracks with low-score detections.
  4. Spawn new tracks for unmatched high-score detections.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

try:
    import lap  # type: ignore
except ImportError:  # pragma: no cover
    lap = None  # type: ignore[assignment]


@dataclass
class TrackOut:
    track_id: int
    bbox: tuple[int, int, int, int]  # x1,y1,x2,y2
    score: float
    det_index: int | None  # index into this-frame detection list, if matched


@dataclass
class _STrack:
    track_id: int
    bbox: np.ndarray  # float xyxy
    score: float
    mean: np.ndarray
    covariance: np.ndarray
    hits: int = 1
    age: int = 1
    time_since_update: int = 0
    state: str = "tracked"  # tracked | lost
    meta: dict = field(default_factory=dict)


def _xyxy_to_xyah(box: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = box
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    return np.array([x1 + w / 2, y1 + h / 2, w / max(h, 1e-6), h], dtype=np.float64)


def _xyah_to_xyxy(xyah: np.ndarray) -> np.ndarray:
    x, y, a, h = xyah
    w = a * h
    return np.array([x - w / 2, y - h / 2, x + w / 2, y + h / 2], dtype=np.float64)


class _Kalman:
    def __init__(self) -> None:
        self._std_weight_pos = 1.0 / 20
        self._std_weight_vel = 1.0 / 160

    def initiate(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean = np.concatenate([measurement, np.zeros(4)])
        std = [
            2 * self._std_weight_pos * measurement[3],
            2 * self._std_weight_pos * measurement[3],
            1e-2,
            2 * self._std_weight_pos * measurement[3],
            10 * self._std_weight_vel * measurement[3],
            10 * self._std_weight_vel * measurement[3],
            1e-5,
            10 * self._std_weight_vel * measurement[3],
        ]
        return mean, np.diag(np.square(std))

    def predict(self, mean: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        std_pos = [
            self._std_weight_pos * mean[3],
            self._std_weight_pos * mean[3],
            1e-2,
            self._std_weight_pos * mean[3],
        ]
        std_vel = [
            self._std_weight_vel * mean[3],
            self._std_weight_vel * mean[3],
            1e-5,
            self._std_weight_vel * mean[3],
        ]
        motion_cov = np.diag(np.square(np.concatenate([std_pos, std_vel])))
        f = np.eye(8)
        for i in range(4):
            f[i, i + 4] = 1.0
        return f @ mean, f @ cov @ f.T + motion_cov

    def update(
        self, mean: np.ndarray, cov: np.ndarray, measurement: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        std = [
            self._std_weight_pos * mean[3],
            self._std_weight_pos * mean[3],
            1e-1,
            self._std_weight_pos * mean[3],
        ]
        innovation_cov = np.diag(np.square(std))
        h = np.eye(4, 8)
        projected = h @ cov @ h.T + innovation_cov
        chol = np.linalg.cholesky(projected)
        b = (cov @ h.T).T
        k = np.linalg.solve(chol, np.linalg.solve(chol.T, b)).T
        innovation = measurement - mean[:4]
        return mean + k @ innovation, cov - k @ projected @ k.T


def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float64)
    tl = np.maximum(a[:, None, :2], b[None, :, :2])
    br = np.minimum(a[:, None, 2:], b[None, :, 2:])
    wh = np.clip(br - tl, 0, None)
    inter = wh[..., 0] * wh[..., 1]
    area_a = np.clip(a[:, 2] - a[:, 0], 0, None) * np.clip(a[:, 3] - a[:, 1], 0, None)
    area_b = np.clip(b[:, 2] - b[:, 0], 0, None) * np.clip(b[:, 3] - b[:, 1], 0, None)
    return inter / np.clip(area_a[:, None] + area_b[None, :] - inter, 1e-9, None)


def _linear_assignment(cost: np.ndarray, thresh: float) -> tuple[np.ndarray, list[int], list[int]]:
    if cost.size == 0:
        return np.empty((0, 2), dtype=int), list(range(cost.shape[0])), list(range(cost.shape[1]))
    if lap is not None:
        _, x, y = lap.lapjv(cost, extend_cost=True, cost_limit=thresh)
        matches, ua, ub = [], [], []
        for ix, mx in enumerate(x):
            if mx >= 0:
                matches.append([ix, int(mx)])
            else:
                ua.append(ix)
        for iy, my in enumerate(y):
            if my < 0:
                ub.append(iy)
        return np.asarray(matches, dtype=int).reshape(-1, 2), ua, ub

    cost_c = cost.copy()
    matches, used_a, used_b = [], set(), set()
    for c, i, j in sorted(
        (cost_c[i, j], i, j) for i in range(cost_c.shape[0]) for j in range(cost_c.shape[1])
    ):
        if c > thresh or i in used_a or j in used_b:
            continue
        matches.append([i, j])
        used_a.add(i)
        used_b.add(j)
    ua = [i for i in range(cost.shape[0]) if i not in used_a]
    ub = [j for j in range(cost.shape[1]) if j not in used_b]
    return np.asarray(matches, dtype=int).reshape(-1, 2), ua, ub


class ByteTracker:
    """Plate / box BYTE tracker. ``update([(bbox, score), ...]) → list[TrackOut]``."""

    def __init__(
        self,
        track_thresh: float = 0.4,
        match_thresh: float = 0.3,  # min IoU to match (converted to cost = 1-IoU)
        match_thresh_second: float = 0.2,
        track_buffer: int = 30,
    ) -> None:
        self.track_thresh = track_thresh
        self.match_thresh = match_thresh
        self.match_thresh_second = match_thresh_second
        self.track_buffer = track_buffer
        self._kf = _Kalman()
        self._tracked: list[_STrack] = []
        self._lost: list[_STrack] = []
        self._next_id = 1
        self.frame_id = 0

    def reset(self) -> None:
        self._tracked.clear()
        self._lost.clear()
        self._next_id = 1
        self.frame_id = 0

    def alive_ids(self) -> set[int]:
        """IDs the tracker still holds: matched this frame, or lost but within ``track_buffer``.

        ``update`` only returns tracks matched in the current frame. A track missing from one
        frame's output is not finished; it can be re-matched with the same ID until it has been
        lost for more than ``track_buffer`` frames.
        """
        return {t.track_id for t in self._tracked} | {t.track_id for t in self._lost}

    def _new_track(self, box: np.ndarray, score: float, det_index: int) -> _STrack:
        mean, cov = self._kf.initiate(_xyxy_to_xyah(box))
        tr = _STrack(
            track_id=self._next_id,
            bbox=box.astype(np.float64),
            score=score,
            mean=mean,
            covariance=cov,
        )
        self._next_id += 1
        tr.meta["det_index"] = det_index
        return tr

    def _predict(self, tracks: list[_STrack]) -> None:
        for t in tracks:
            t.mean, t.covariance = self._kf.predict(t.mean, t.covariance)
            t.bbox = _xyah_to_xyxy(t.mean[:4])
            t.age += 1

    def _touch(self, tr: _STrack, box: np.ndarray, score: float, det_index: int) -> None:
        tr.mean, tr.covariance = self._kf.update(tr.mean, tr.covariance, _xyxy_to_xyah(box))
        tr.bbox = _xyah_to_xyxy(tr.mean[:4])
        tr.score = score
        tr.hits += 1
        tr.time_since_update = 0
        tr.state = "tracked"
        tr.meta["det_index"] = det_index

    def _associate(
        self, tracks: list[_STrack], dets: np.ndarray, min_iou: float
    ) -> tuple[np.ndarray, list[int], list[int]]:
        if not tracks or len(dets) == 0:
            return np.empty((0, 2), dtype=int), list(range(len(tracks))), list(range(len(dets)))
        ious = _iou_matrix(np.stack([t.bbox for t in tracks]), dets)
        return _linear_assignment(1.0 - ious, thresh=1.0 - min_iou)

    def update(
        self,
        detections: list[tuple[tuple[int, int, int, int], float]],
    ) -> list[TrackOut]:
        self.frame_id += 1
        boxes = np.asarray([d[0] for d in detections], dtype=np.float64).reshape(-1, 4)
        scores = (
            np.asarray([d[1] for d in detections], dtype=np.float64) if detections else np.zeros(0)
        )

        high_m = scores >= self.track_thresh if len(scores) else np.zeros(0, dtype=bool)
        low_m = (~high_m) & (scores > 0.1) if len(scores) else np.zeros(0, dtype=bool)
        high_boxes, high_scores = boxes[high_m], scores[high_m]
        high_idx = np.flatnonzero(high_m)
        low_boxes, low_scores = boxes[low_m], scores[low_m]
        low_idx = np.flatnonzero(low_m)

        pool = self._tracked + self._lost
        self._predict(pool)

        matched_track_ids: set[int] = set()
        activated: list[_STrack] = []

        matches, u_tr, _u_det = self._associate(pool, high_boxes, self.match_thresh)
        for it, idet in matches:
            tr = pool[it]
            self._touch(tr, high_boxes[idet], float(high_scores[idet]), int(high_idx[idet]))
            activated.append(tr)
            matched_track_ids.add(tr.track_id)

        # Second association: unmatched tracks ↔ low-score dets
        remain = [pool[i] for i in u_tr]
        matches2, _u_tr2, _u_low = self._associate(remain, low_boxes, self.match_thresh_second)
        for it, idet in matches2:
            tr = remain[it]
            self._touch(tr, low_boxes[idet], float(low_scores[idet]), int(low_idx[idet]))
            activated.append(tr)
            matched_track_ids.add(tr.track_id)

        rematched_remain = {remain[it].track_id for it, _ in matches2}
        lost_now: list[_STrack] = []
        for i in u_tr:
            tr = pool[i]
            if tr.track_id in rematched_remain or tr.track_id in matched_track_ids:
                continue
            tr.time_since_update += 1
            tr.state = "lost"
            if tr.time_since_update <= self.track_buffer:
                lost_now.append(tr)

        matched_high = {int(idet) for _, idet in matches}
        for j in range(len(high_boxes)):
            if j in matched_high:
                continue
            activated.append(
                self._new_track(high_boxes[j], float(high_scores[j]), int(high_idx[j]))
            )

        self._tracked = [t for t in activated if t.time_since_update == 0]
        self._lost = lost_now

        outs: list[TrackOut] = []
        for t in self._tracked:
            x1, y1, x2, y2 = t.bbox
            outs.append(
                TrackOut(
                    track_id=t.track_id,
                    bbox=(round(x1), round(y1), round(x2), round(y2)),
                    score=float(t.score),
                    det_index=t.meta.get("det_index"),
                )
            )
        return outs
