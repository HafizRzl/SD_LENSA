package my.lensa

import kotlin.math.round

// Port of RulesNavigationAgent + decision_segments in desktop-beta/lensa_rules.py.
// Must stay byte-for-byte equal in what it SAYS — GoldenParityTest checks it against the Python.
// Note: kotlin.math.round rounds half to EVEN, like Python's round() (2.5 -> 2).

data class Detection(
    val cls: String, val confidence: Double, val distanceM: Double?, val position: String,
    val movingToward: Boolean = false, val cx: Double? = null, val widthN: Double? = null,
    val spokenM: Int? = null, val trackId: String? = null,
)

data class SceneItem(val cls: String, val distanceM: Double?, val position: String,
                     val movingToward: Boolean, val cx: Double?, val spokenM: Int?)

data class Decision(
    val action: String, val urgency: String, val cls: String? = null, val position: String? = null,
    val distanceM: Double? = null, val avoid: String? = null, val spokenM: Int? = null,
    val incident: Set<String> = emptySet(),
) {
    fun metres(): Int? = distanceM?.let { spokenM ?: round(it).toInt() }
}

private val SENS = mapOf("low" to 0.8, "high" to 1.25)
private val ZONES = listOf("left", "center", "right")
private const val ZONE_OVERLAP = 0.25   // a box blocks a third once it covers >=25% of it
private const val HOLD_FRAMES = 3       // frames the other side must win before a steer flips
private const val ROUTE_MAX_AGE_S = 1.0

fun zonesOf(d: Detection): Set<String> {
    val z = mutableSetOf(d.position)
    val cx = d.cx ?: return z
    val w = d.widthN ?: return z
    val (x1, x2) = (cx - w / 2) to (cx + w / 2)
    ZONES.forEachIndexed { i, name ->
        val lo = i / 3.0; val hi = (i + 1) / 3.0
        if (minOf(x2, hi) - maxOf(x1, lo) >= ZONE_OVERLAP / 3) z.add(name)
    }
    return z
}

class RulesAgent(private val c: Content, private val lang: String = "ms",
                 clock: () -> Double = { System.nanoTime() / 1e9 }) {
    private val sensitivity = c.hazard.sensitivity
    private var held: String? = null
    private var flip = 0
    private var calm = 0
    val map = PacmanMapper(clock)

    fun run(detections: List<Detection>): Pair<Decision, String> {
        val live = detections.filter { it.confidence >= c.hazard.confidenceFloor && c.objects.dangerWeight(it.cls) > 0 }
        map.maybeRefresh(live)
        val scene = interpret(live)
        val blocked = blocked(live)
        var dec = stabilise(decide(scene, blocked, live), blocked)
        if (dec.cls != null) dec = dec.copy(spokenM = scene.first { it.cls == dec.cls }.spokenM)
        return dec to language(dec)
    }

    /** Zones occupied by any NEAR detection (caution band, or a ground hazard with no distance):
     *  a bottle 5 m away doesn't stop you stepping left now. */
    private fun blocked(ds: List<Detection>): Set<String> {
        val lim = c.hazard.bands.caution
        return ds.filter { it.distanceM == null || it.distanceM <= lim }.flatMap { zonesOf(it) }.toSet()
    }

    private fun route(ds: List<Detection>): String? {
        if (map.ageS() > ROUTE_MAX_AGE_S || map.scene != PacmanMap.sceneKey(ds)) map.refresh(ds)
        map.turn?.let { return it }
        return if (map.reachM >= c.hazard.bands.caution) "straight" else null
    }

    private fun incident(ds: List<Detection>): Set<String> {
        val lim = c.hazard.bands.notice
        return ds.filter { it.trackId != null && (it.distanceM == null || it.distanceM <= lim) }
            .mapNotNull { it.trackId }.toSet()
    }

    private fun stabilise(dec: Decision, blocked: Set<String>): Decision {
        if (dec.avoid == null) {
            flip = 0; calm += 1
            if (calm >= HOLD_FRAMES) held = null
            return dec
        }
        calm = 0
        if (held == null || held == dec.avoid || held in blocked) { held = dec.avoid; flip = 0; return dec }
        flip += 1
        if (flip >= HOLD_FRAMES) { held = dec.avoid; flip = 0; return dec }
        return dec.copy(action = "steer_$held", avoid = held)
    }

    private fun interpret(ds: List<Detection>): List<SceneItem> {
        val merged = LinkedHashMap<String, SceneItem>()
        for (d in ds) {
            val ex = merged[d.cls]
            merged[d.cls] = if (ex == null) SceneItem(d.cls, d.distanceM, d.position, d.movingToward, d.cx, d.spokenM)
            else {
                val closer = (d.distanceM ?: 1e9) < (ex.distanceM ?: 1e9)
                SceneItem(d.cls, if (closer) d.distanceM else ex.distanceM, if (closer) d.position else ex.position,
                    ex.movingToward || d.movingToward, if (closer) d.cx else ex.cx, if (closer) d.spokenM else ex.spokenM)
            }
        }
        return merged.values.toList()
    }

    private fun decide(items: List<SceneItem>, blocked: Set<String>, ds: List<Detection>): Decision {
        val b = c.hazard.bands
        if (items.isEmpty()) return Decision("proceed", "low")
        val stop = items.filter { it.distanceM != null && it.distanceM <= b.stop }.minByOrNull { it.distanceM!! }
        if (stop != null) return Decision("stop", "high", stop.cls, stop.position, stop.distanceM)

        val sm = SENS[sensitivity] ?: 1.0
        var best: SceneItem? = null; var bestScore = -1.0
        for (it in items) {
            val score = c.objects.dangerWeight(it.cls) * proximity(it.distanceM, b) *
                    (if (it.movingToward) c.hazard.movingTowardMultiplier else 1.0) * sm
            if (score > bestScore) { bestScore = score; best = it }
        }
        if (best == null || bestScore <= 0.001) return Decision("proceed", "low")

        val d = best.distanceM
        val action = "caution"
        var urg: String
        when {
            d != null && d <= b.caution -> urg = "medium"
            d != null && d <= b.notice -> urg = "low"
            d == null -> {
                urg = "low"
                // flat hazards have no distance yet; very dangerous ones get "Awas!" + high urgency
                if (c.objects.isGround(best.cls) && c.objects.dangerWeight(best.cls) >= c.hazard.groundAlertMinDanger) urg = "high"
            }
            else -> return Decision("proceed", "low")
        }
        val close = d != null && d <= b.caution

        var avoid: String? = null
        if (best.position == "center") {
            val open = listOf("left", "right").filter { it !in blocked }
            if (open.size == 2) avoid = if (best.cx != null && best.cx < 0.5) "right" else "left"
            else if (open.isNotEmpty()) avoid = open[0]
            else {
                avoid = route(ds)
                if (avoid == "straight") avoid = null
                else if (avoid == null && close) return Decision("blocked", "high", best.cls, "center", d, incident = incident(ds))
            }
        } else if (close) {
            val other = if (best.position == "left") "right" else "left"
            if (other !in blocked) avoid = other
            else if ("center" in blocked) {
                avoid = route(ds)
                if (avoid == "straight") avoid = null
                else if (avoid == null) return Decision("blocked", "high", best.cls, best.position, d, incident = incident(ds))
            }
        }
        if (avoid != null) return Decision("steer_$avoid", urg, best.cls, best.position, d, avoid = avoid)
        return Decision(action, urg, best.cls, best.position, d)
    }

    private fun language(dec: Decision): String {
        val lib = c.phrases(lang)
        val obj = dec.cls?.let { c.objects.label(it, lang) } ?: ""
        val dist = dec.metres()?.toString() ?: ""
        val base = when (dec.action) {
            "proceed" -> lib.render("clear")
            "stop" -> lib.render("stop", obj = obj)
            "blocked" -> lib.render("blocked").ifEmpty { lib.render("stop", obj = obj) }
            "caution" -> if (dec.distanceM != null)
                lib.render("detection", obj = obj, direction = lib.direction(dec.position ?: "center"), distance = dist)
            else lib.render(if (dec.urgency == "high") "alert" else "caution", obj = obj,
                direction = lib.direction(dec.position ?: "center"))
            "steer_left", "steer_right" -> {
                val avoidWord = lib.direction(dec.avoid ?: if (dec.action == "steer_left") "left" else "right")
                val hazardWord = lib.direction(dec.position ?: "center")
                (if (dec.distanceM != null) lib.render("steer_hazard", obj = obj, direction = hazardWord, distance = dist, avoid = avoidWord)
                else lib.render("steer_hazard_nodist", obj = obj, direction = hazardWord, avoid = avoidWord))
                    .ifEmpty { lib.render("steer", direction = avoidWord) }
            }
            else -> ""
        }
        if (dec.urgency == "high" && dec.action in setOf("caution", "steer_left", "steer_right")) {
            val p = lib.urgencyPrefix["high"].orEmpty().trim()
            if (p.isNotEmpty()) return "$p $base".trim()
        }
        return base
    }

    private fun proximity(d: Double?, b: DistanceBands): Double = when {
        d == null -> 0.2
        d <= b.stop -> 1.0
        d >= b.ignore -> 0.0
        else -> ((b.ignore - d) / (b.ignore - b.stop)).coerceIn(0.0, 1.0)
    }
}

/** What a line MEANS, for the speech gate: '1.8 m' vs '2.0 m' is the same meaning; a closer band isn't. */
fun meaningKey(dec: Decision, b: DistanceBands): List<Any?> {
    val d = dec.distanceM
    val band = when { d == null -> null; d <= b.stop -> "stop"; d <= b.caution -> "caution"; else -> "notice" }
    return listOf(dec.action, dec.cls, dec.position, dec.avoid, band)
}

/** A Decision as at most two cached clips (+ optional "Awas!"). Null = not segmentable. */
fun decisionSegments(dec: Decision, c: Content, lang: String = "ms"): List<String>? {
    if (lang != "ms") return null
    val lib = c.phrases(lang)
    val obj = dec.cls?.let { c.objects.label(it, lang) }
    val n = dec.metres()
    fun at(pos: String) = "$obj di ${lib.direction(pos)}"
    when (dec.action) {
        "proceed" -> return listOf(lib.render("clear"))
        "stop" -> return if (obj != null) listOf("Berhenti!", at("center")) else listOf("Berhenti!")
        "blocked" -> return listOf("Berhenti!", "Laluan tersekat")
    }
    val segs = mutableListOf<String>()
    if (dec.urgency == "high") lib.urgencyPrefix["high"].orEmpty().trim().let { if (it.isNotEmpty()) segs.add(it) }
    val pos = dec.position ?: "center"
    if (dec.action == "caution") {
        if (n != null) return segs + listOf(at(pos), "$n meter")
        return segs + if (segs.isNotEmpty()) listOf(at(pos)) else listOf("Hati-hati", at(pos))
    }
    if (dec.action == "steer_left" || dec.action == "steer_right") {
        val avoid = lib.direction(dec.avoid ?: if (dec.action == "steer_left") "left" else "right")
        return segs + listOf(at(pos), if (n != null) "$n meter, elak ke $avoid" else "elak ke $avoid")
    }
    return null
}
