package my.lensa

import kotlin.math.abs
import kotlin.math.round

// Port of the per-frame helpers in desktop-beta/lensa_beta.py: side dead-band, held metres,
// duplicate-box merge, monocular distance — plus a small IoU tracker standing in for
// Ultralytics' ByteTrack (which doesn't exist on Android).

/** A detector box in normalised image coords (0..1). */
data class Box(val x1: Double, val y1: Double, val x2: Double, val y2: Double,
               val conf: Double, val cls: String, var id: String? = null)

fun iou(a: Box, b: Box): Double {
    val ix = maxOf(0.0, minOf(a.x2, b.x2) - maxOf(a.x1, b.x1))
    val iy = maxOf(0.0, minOf(a.y2, b.y2) - maxOf(a.y1, b.y1))
    val inter = ix * iy
    val union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return if (union > 0) inter / union else 0.0
}

const val DUP_IOU = 0.7

/** One object flagged twice (same box, or chair+bench on one seat) -> keep the most confident. */
fun dedupe(boxes: List<Box>): List<Box> {
    val kept = mutableListOf<Box>()
    for (b in boxes.sortedByDescending { it.conf }) if (kept.all { iou(b, it) < DUP_IOU }) kept.add(b)
    return kept
}

/** Rough distance from box height (stand-in for the ToF sensor). k = calibrate per camera. */
fun estimateDistanceM(boxHNorm: Double, k: Double = 1.2) = (k / maxOf(boxHNorm, 0.02)).coerceIn(0.5, 10.0)

private val ZONE_BOUNDS = mapOf("left" to (0.0 to 1 / 3.0), "center" to (1 / 3.0 to 2 / 3.0), "right" to (2 / 3.0 to 1.0))
const val ZONE_MARGIN = 0.04   // a tracked object must cross a 1/3 line by this much to change side

fun positionOf(cx: Double, prev: String? = null): String {
    val raw = if (cx < 1 / 3.0) "left" else if (cx > 2 / 3.0) "right" else "center"
    if (prev == null || raw == prev) return raw
    val (lo, hi) = ZONE_BOUNDS.getValue(prev)
    return if (cx >= lo - ZONE_MARGIN && cx <= hi + ZONE_MARGIN) prev else raw
}

/** Per-object memory keyed by tracker ID: side with a dead-band, smoothed distance, and the
 *  metres actually SAID — held until the estimate moves >0.65 m (no "2 meter"<->"3 meter" flicker). */
class Tracks(private val clock: () -> Double = { System.nanoTime() / 1e9 }) {
    private class St(val pos: String, val dist: Double?, val spoken: Int?, val seen: Double,
                     val frame: Int, val hits: Int)
    private val s = HashMap<String, St>()
    private var frame = 0

    /** Call once at the start of every frame (consecutive-frame counting). */
    fun tick() { frame += 1 }

    /** Seen in n frames in a row? Untracked boxes can't be counted, so they pass. */
    fun confirmed(tid: String?, n: Int) = tid == null || (s[tid]?.hits ?: 0) >= n

    fun update(tid: String?, cx: Double, rawDist: Double?): Triple<String, Double?, Int?> {
        val st = tid?.let { s[it] }
        val pos = positionOf(cx, st?.pos)
        var dist = rawDist
        if (dist != null && st?.dist != null) dist = st.dist + EMA * (dist - st.dist)
        var spoken = st?.spoken
        if (dist == null) spoken = null
        else if (spoken == null || abs(dist - spoken) > 0.65) spoken = maxOf(1, round(dist).toInt())
        if (tid != null) s[tid] = St(pos, dist, spoken, clock(), frame,
            if (st != null && st.frame == frame - 1) st.hits + 1 else 1)
        return Triple(pos, dist, spoken)
    }

    fun prune() { val cut = clock() - FORGET_S; s.entries.removeIf { it.value.seen < cut } }

    companion object { const val EMA = 0.4; const val FORGET_S = 2.0 }
}

/** Greedy IoU tracker: a box keeps the ID of the same-class box it overlaps most in recent
 *  frames. ponytail: no motion model (ByteTrack has a Kalman filter) — fine at walking pace;
 *  upgrade if IDs swap when objects cross. */
class IouTracker(private val prefix: String, private val clock: () -> Double = { System.nanoTime() / 1e9 }) {
    private class T(var box: Box, var seen: Double)
    private val tracks = LinkedHashMap<String, T>()
    private var next = 1

    fun assign(boxes: List<Box>): List<Box> {
        val now = clock()
        tracks.entries.removeIf { now - it.value.seen > FORGET_S }
        val free = tracks.keys.toMutableSet()
        for (b in boxes.sortedByDescending { it.conf }) {
            val best = free.filter { tracks.getValue(it).box.cls == b.cls }
                .maxByOrNull { iou(tracks.getValue(it).box, b) }
            val id = if (best != null && iou(tracks.getValue(best).box, b) >= MIN_IOU) best.also { free.remove(it) }
            else "$prefix${next++}"
            b.id = id
            tracks[id] = T(b, now)
        }
        return boxes
    }

    companion object { const val MIN_IOU = 0.3; const val FORGET_S = 1.0 }
}
