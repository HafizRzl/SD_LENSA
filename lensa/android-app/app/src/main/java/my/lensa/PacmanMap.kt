package my.lensa

import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.tan

// Port of desktop-beta/pacman_map.py (minus the drawing): top-down occupancy grid + BFS route,
// used by the rules engine when all three zones are blocked. Same numbers, same tests.

object PacmanMap {
    const val CELL_M = 0.5
    const val HALF_W_M = 3.0
    const val DEPTH_M = 8.0
    val COLS = (2 * HALF_W_M / CELL_M).toInt() + 1   // 13
    val ROWS = (DEPTH_M / CELL_M).toInt()            // 16
    const val CLEAR_M = 0.3                          // inflate obstacles by ~user half-width
    const val REFRESH_S = 4.0
    const val NEAR_UNSEEN_OK_M = 1.5                 // cane range: unseen cells closer are walkable

    /** Horizontal camera field of view. 60° = the desktop webcam the beta was tuned on;
     *  the app sets the phone camera's value (ponytail: calibrate per phone). */
    var fovDeg = 60.0
        set(v) { field = v; unseen = outsideFov() }

    var unseen: Array<BooleanArray> = outsideFov(); private set

    private fun rad(deg: Double) = Math.toRadians(deg)

    fun lateralM(cxNorm: Double, distM: Double) = distM * tan((cxNorm - 0.5) * rad(fovDeg))

    class Hit(val x: Double, val d: Double, val w: Double)

    fun hitsFrom(ds: List<Detection>): List<Hit> = ds.mapNotNull { d ->
        val dist = d.distanceM ?: return@mapNotNull null
        val cx = d.cx ?: return@mapNotNull null
        val w = maxOf(0.4, (d.widthN ?: 0.0) * 2 * dist * tan(rad(fovDeg) / 2))
        Hit(lateralM(cx, dist), dist, w)
    }

    private fun outsideFov(): Array<BooleanArray> {
        val half = tan(rad(fovDeg) / 2)
        return Array(ROWS) { r ->
            val depth = (r + 0.5) * CELL_M
            BooleanArray(COLS) { c -> depth >= NEAR_UNSEEN_OK_M && kotlin.math.abs(c * CELL_M - HALF_W_M) > depth * half }
        }
    }

    fun paint(hits: List<Hit>): Array<BooleanArray> {
        val g = Array(ROWS) { BooleanArray(COLS) }
        for (h in hits) {
            val half = h.w / 2 + CLEAR_M
            // columns are centred: block one when the body-inflated obstacle covers its centre
            val c0 = ceil((h.x - half + HALF_W_M) / CELL_M).toInt()
            val c1 = floor((h.x + half + HALF_W_M) / CELL_M).toInt()
            val r0 = (maxOf(0.0, h.d - 0.35) / CELL_M).toInt()
            val r1 = ((h.d + 0.35) / CELL_M).toInt()
            for (r in maxOf(0, r0)..minOf(ROWS - 1, r1))
                for (c in maxOf(0, c0)..minOf(COLS - 1, c1)) g[r][c] = true
        }
        return g
    }

    /** BFS from the user (row 0, centre col) to the reachable free cell farthest FORWARD
     *  (ties: closest to centre). Neighbour order matches Python: down-row, up-row, left, right. */
    fun bfsDeepest(grid: Array<BooleanArray>): List<Pair<Int, Int>> {
        val blocked = Array(ROWS) { r -> BooleanArray(COLS) { c -> grid[r][c] || unseen[r][c] } }
        val start = 0 to COLS / 2
        if (blocked[0][COLS / 2]) return emptyList()
        val parent = HashMap<Pair<Int, Int>, Pair<Int, Int>?>()
        parent[start] = null
        val q = ArrayDeque(listOf(start))
        var best = start
        while (q.isNotEmpty()) {
            val cur = q.removeFirst()
            val (r, c) = cur
            if (r > best.first || (r == best.first &&
                        kotlin.math.abs(c - COLS / 2) < kotlin.math.abs(best.second - COLS / 2))) best = cur
            for ((nr, nc) in listOf(r + 1 to c, r - 1 to c, r to c - 1, r to c + 1)) {
                val nxt = nr to nc
                if (nr in 0 until ROWS && nc in 0 until COLS && !blocked[nr][nc] && nxt !in parent) {
                    parent[nxt] = cur
                    q.addLast(nxt)
                }
            }
        }
        val path = ArrayList<Pair<Int, Int>>()
        var cur: Pair<Int, Int>? = best
        while (cur != null) { path.add(cur); cur = parent[cur] }
        return path.reversed()
    }

    fun firstTurn(path: List<Pair<Int, Int>>): String? {
        for (i in 0 until path.size - 1) {
            val (c0, c1) = path[i].second to path[i + 1].second
            if (c1 != c0) return if (c1 < c0) "left" else "right"
        }
        return null
    }

    fun sceneKey(ds: List<Detection>): Set<String> = ds.map { it.trackId ?: it.cls }.toSet()
}

/** One snapshot every REFRESH_S; the rules engine forces one when it needs a fresh route. */
class PacmanMapper(private val clock: () -> Double = { System.nanoTime() / 1e9 }) {
    var turn: String? = null; private set
    var reachM = 0.0; private set
    var scene: Set<String>? = null; private set
    var grid: Array<BooleanArray>? = null; private set      // last snapshot, for the mini-map
    var path: List<Pair<Int, Int>> = emptyList(); private set
    private var at = -1e9

    fun maybeRefresh(ds: List<Detection>) { if (ageS() >= PacmanMap.REFRESH_S) refresh(ds) }

    fun refresh(ds: List<Detection>) {
        at = clock()
        scene = PacmanMap.sceneKey(ds)
        val g = PacmanMap.paint(PacmanMap.hitsFrom(ds))
        val path = PacmanMap.bfsDeepest(g)
        grid = g; this.path = path
        turn = PacmanMap.firstTurn(path)
        reachM = if (path.isEmpty()) 0.0 else (path.last().first + 1) * PacmanMap.CELL_M
    }

    fun ageS() = clock() - at
}
