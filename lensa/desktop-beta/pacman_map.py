"""Top-down "pacman" occupancy map + BFS route.

Every REFRESH_S seconds, the current detections are painted onto a top-down grid using their RAW
bounding-box position + estimated distance (NOT the 3-plane buckets), and BFS finds the
shortest route to the DEEPEST safe cell the camera can see. The window itself is silent —
the rules engine only consults the same map (via _route) when all three zones are blocked.
ghost 1 at 2 m → dodge around it; ghost 2 at 5 m → weave back after passing.

Run `python desktop-beta/pacman_map.py` for the self-test (the two-ghost scenario).
"""
from __future__ import annotations

import math
import time
from collections import deque

import cv2
import numpy as np

# ---- grid geometry (metres) ----
CELL_M = 0.5
HALF_W_M = 3.0                          # lateral extent each side of the user
DEPTH_M = 8.0                           # forward extent
COLS = int(2 * HALF_W_M / CELL_M) + 1   # 13
ROWS = int(DEPTH_M / CELL_M)            # 16
FOV_DEG = 60.0                          # ponytail: typical webcam; calibrate per camera
CLEAR_M = 0.3                           # inflate obstacles by ~user half-width
PX = 30                                 # render pixels per cell
REFRESH_S = 4.0                         # map + fallback route cadence (per-frame was too jumpy)
# ponytail: cane range. Beside and just ahead of the user the camera sees nothing (the view cone
# is ~1 cell wide at 1 m), but the user just walked through that space and their cane covers it.
# Unseen cells closer than this are walkable; beyond it, unseen = blocked. Tune on walk tests.
NEAR_UNSEEN_OK_M = 1.5


def lateral_m(cx_norm: float, dist_m: float) -> float:
    """Box centre x (0..1) + distance -> lateral metres from the user's axis (pinhole)."""
    ang = (cx_norm - 0.5) * math.radians(FOV_DEG)
    return dist_m * math.tan(ang)


def hits_from(detections, label_of=lambda c: c):
    """Detections (need cx + distance) -> paint() hits (x_m, d_m, w_m, label)."""
    out = []
    for d in detections:
        if d.cx is None or d.distance_m is None:
            continue
        w = max(0.4, (d.width_n or 0.0) * 2 * d.distance_m * math.tan(math.radians(FOV_DEG) / 2))
        out.append((lateral_m(d.cx, d.distance_m), d.distance_m, w, label_of(d.yolo_class)))
    return out


def _outside_fov() -> np.ndarray:
    """1 = cell the camera cannot see AND too far to assume walkable. Unseen far space is NOT
    free: routing a blind user into space nobody looked at is worse than saying 'blocked'.
    Unseen space within cane range (NEAR_UNSEEN_OK_M) is walkable — see above."""
    half = math.tan(math.radians(FOV_DEG) / 2)
    m = np.zeros((ROWS, COLS), np.uint8)
    for r in range(ROWS):
        depth = (r + 0.5) * CELL_M
        for c in range(COLS):
            if depth >= NEAR_UNSEEN_OK_M and abs(c * CELL_M - HALF_W_M) > depth * half:
                m[r, c] = 1
    return m


UNSEEN = _outside_fov()   # fixed by FOV_DEG -> computed once


def scene_key(detections) -> frozenset:
    """Which objects are in view: tracker IDs when present, else class names."""
    return frozenset(d.track_id or d.yolo_class for d in detections)


def first_turn(path) -> str | None:
    """First sideways move on a BFS path: 'left' | 'right' | None (straight / no path)."""
    for (_, c0), (_, c1) in zip(path, path[1:]):
        if c1 != c0:
            return "left" if c1 < c0 else "right"
    return None


def paint(hits) -> tuple[np.ndarray, dict]:
    """hits: iterable of (x_m, d_m, w_m, label) -> (grid[ROWS][COLS] uint8, {cell: label}).
    grid row 0 = nearest band, col 0 = far left. 1 = blocked."""
    grid = np.zeros((ROWS, COLS), np.uint8)
    labels: dict[tuple[int, int], str] = {}
    for x, d, w, label in hits:
        half = w / 2 + CLEAR_M
        # Columns are CENTRED on c*CELL_M - HALF_W_M; block a column when the (already
        # body-inflated) obstacle covers its centre. int() shifted every obstacle half a cell
        # left; blocking on any touch merged gaps of <1 m into walls.
        c0 = math.ceil((x - half + HALF_W_M) / CELL_M)
        c1 = math.floor((x + half + HALF_W_M) / CELL_M)
        r0 = int(max(0.0, d - 0.35) / CELL_M)
        r1 = int((d + 0.35) / CELL_M)
        for r in range(max(0, r0), min(ROWS, r1 + 1)):
            for c in range(max(0, c0), min(COLS, c1 + 1)):
                grid[r, c] = 1
                labels.setdefault((r, c), label)
    return grid, labels


def bfs_deepest(grid: np.ndarray) -> list[tuple[int, int]]:
    """4-connected BFS from the user (row 0, centre col) to the reachable free cell that is
    farthest FORWARD (ties: closest to centre). Returns the path [(row, col), ...] or []."""
    start = (0, COLS // 2)
    if grid[start]:
        return []
    parent = {start: None}
    q = deque([start])
    best = start
    while q:
        cur = q.popleft()
        r, c = cur
        if r > best[0] or (r == best[0] and abs(c - COLS // 2) < abs(best[1] - COLS // 2)):
            best = cur
        for nr, nc in ((r + 1, c), (r - 1, c), (r, c - 1), (r, c + 1)):
            nxt = (nr, nc)
            if 0 <= nr < ROWS and 0 <= nc < COLS and not grid[nr, nc] and nxt not in parent:
                parent[nxt] = cur
                q.append(nxt)
    path = []
    cur = best
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    return path


def summarize(path: list[tuple[int, int]]) -> str:
    """One console line describing the weave: 'left @1.5m -> right @4.0m, clear to 7.5m'."""
    if not path:
        return "no safe path — blocked immediately ahead"
    moves = []
    for (r0, c0), (r1, c1) in zip(path, path[1:]):
        if c1 != c0:
            side = "left" if c1 < c0 else "right"
            if not moves or moves[-1][0] != side:
                moves.append((side, r1 * CELL_M))
    depth = path[-1][0] * CELL_M + CELL_M
    if not moves:
        return f"straight ahead, clear to {depth:.1f}m"
    steps = " -> ".join(f"{s} @{d:.1f}m" for s, d in moves)
    return f"weave: {steps}, clear to {depth:.1f}m"


def render(grid: np.ndarray, labels: dict, path: list, unseen=None) -> np.ndarray:
    """Top-down view, forward = up, user at bottom centre. Pacman-yellow dots = BFS route.
    Unseen cells (outside the camera's view) are drawn darker."""
    img = np.full((ROWS * PX, COLS * PX, 3), (25, 25, 25), np.uint8)

    def cell_rect(r, c):
        y = (ROWS - 1 - r) * PX
        x = c * PX
        return x, y

    for r in range(ROWS):
        for c in range(COLS):
            x, y = cell_rect(r, c)
            if unseen is not None and unseen[r, c]:
                cv2.rectangle(img, (x, y), (x + PX, y + PX), (12, 12, 12), -1)
            cv2.rectangle(img, (x, y), (x + PX, y + PX), (45, 45, 45), 1)
            if grid[r, c]:
                cv2.rectangle(img, (x + 1, y + 1), (x + PX - 1, y + PX - 1), (40, 40, 160), -1)
                lab = labels.get((r, c))
                if lab:
                    cv2.putText(img, lab[:4], (x + 2, y + PX - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 255), 1)
    for r, c in path:
        x, y = cell_rect(r, c)
        cv2.circle(img, (x + PX // 2, y + PX // 2), PX // 6, (0, 220, 255), -1)  # pacman pellets
    if path:
        x, y = cell_rect(*path[-1])
        cv2.circle(img, (x + PX // 2, y + PX // 2), PX // 3, (0, 220, 255), 2)   # the goal
    # the user
    ux, uy = cell_rect(0, COLS // 2)
    cv2.circle(img, (ux + PX // 2, uy + PX - 6), PX // 3, (0, 200, 0), -1)
    # metre ruler
    for m in range(1, int(DEPTH_M) + 1, 2):
        y = (ROWS - int(m / CELL_M)) * PX
        cv2.putText(img, f"{m}m", (4, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)
    return img


class PacmanMapper:
    """One snapshot every REFRESH_S: the map image (drawn by the beta) plus the route's first
    turn and how far it reaches (read by the rules engine when all three zones are blocked).
    Sharing one snapshot keeps the window and the spoken fallback in agreement."""

    def __init__(self, label_of=lambda c: c):
        self.label_of = label_of
        self.turn = None          # 'left' | 'right' | None (straight, or no route)
        self.reach_m = 0.0        # how far forward the route gets
        self.img = render(np.zeros((ROWS, COLS), np.uint8), {}, [], UNSEEN)
        self._at = -1e9           # time of the last snapshot
        self.scene = None         # which objects the snapshot was built from (see scene_key)
        self._last = None

    def maybe_refresh(self, detections):
        if time.monotonic() - self._at >= REFRESH_S:
            self.refresh(detections)

    def refresh(self, detections):
        """Snapshot NOW. The rules engine forces this when the fallback is needed and the
        snapshot is stale — a 4 s old map may predate the obstacle that blocked the zones."""
        self._at = time.monotonic()
        self.scene = scene_key(detections)
        grid, labels = paint(hits_from(detections, self.label_of))
        path = bfs_deepest(grid | UNSEEN)
        self.turn = first_turn(path)
        self.reach_m = (path[-1][0] + 1) * CELL_M if path else 0.0
        self.img = render(grid, labels, path, UNSEEN)
        line = summarize(path)
        if line != self._last:          # console only on change
            print(f"[map] {line}")
            self._last = line

    def age_s(self) -> float:
        return time.monotonic() - self._at

    def remaining_s(self) -> float:
        return max(0.0, REFRESH_S - self.age_s())


if __name__ == "__main__":
    # Self-test: the two-ghost scenario. Ghost 1 slightly right of centre at 2 m,
    # ghost 2 on the left at 5 m -> the route must exist, weave, and reach deep.
    hits = [(0.3, 2.0, 0.8, "g1"), (-0.8, 5.0, 0.8, "g2")]
    grid, labels = paint(hits)
    path = bfs_deepest(grid)
    assert path, "expected a path around the ghosts"
    assert path[-1][0] >= ROWS - 2, f"expected to reach deep, got row {path[-1][0]}"
    cols = {c for _, c in path}
    assert len(cols) > 1, "expected the path to weave, not go straight"
    print("two ghosts ->", summarize(path))

    # Fully blocked wall at 1 m -> no path.
    wall = [(x / 10.0, 1.0, 1.0, "wall") for x in range(-30, 31, 5)]
    grid2, _ = paint(wall)
    assert bfs_deepest(grid2) == [] or bfs_deepest(grid2)[-1][0] < 2, "wall must block the route"
    print("wall ->", summarize(bfs_deepest(grid2)))
    # regressions (Oct 2026): these all dead-ended at 1.0-1.5 m with no turn -> "Laluan tersekat"
    def route(hits):
        g, _ = paint(hits)
        return bfs_deepest(g | UNSEEN)
    p = route([(0.0, 2.0, 0.5, "person")])                 # one person 2 m dead ahead
    assert first_turn(p) in ("left", "right") and p[-1][0] >= ROWS - 2, summarize(p)
    p = route([(0.6, 1.5, 0.5, "chair")])                  # chair off to the right
    assert p[-1][0] >= ROWS - 2, summarize(p)
    g, labels = paint([(0.0, 2.0, 0.5, "person")])         # centred obstacle -> symmetric cells
    cols = sorted({c for _, c in labels})
    assert cols == [COLS // 2 - 1, COLS // 2, COLS // 2 + 1], cols
    print("person 2 m ahead ->", summarize(route([(0.0, 2.0, 0.5, "person")])))
    print("OK — pacman map self-test passed")
