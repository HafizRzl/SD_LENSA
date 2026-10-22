package my.lensa

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

// Hazard map (hackathon): after LENSA walks the user past a FIXED hazard it asks, in English,
// "Dangerous hazard found: pothole. Do you want to log it?" -> yes = a report with the phone's GPS
// to the township hazard server (../../server.py). It also polls that server to warn about hazards
// other walkers logged, and to say when one has been repaired. Same rules as web/index.html.
// Loop A (guidance) stays offline; this only adds to it.

/** Detector classes worth reporting to the township. Curbs, crossings and intact covers are
 *  ground classes too, but they aren't defects. */
val REPORTABLE = setOf("pothole", "broken_road", "drain", "manhole_open", "manhole_displaced", "manhole_broken")

/** Decides when a hazard has been walked PAST: a confirmed track that came close (box bottom in
 *  the lower part of the frame), then left the view for GONE_S. One prompt per class per
 *  COOLDOWN_S, because the IoU tracker can give one pothole a new ID after a missed frame. */
class PassWatcher(private val clock: () -> Double = { System.nanoTime() / 1e9 }) {
    private class Seen(val cls: String, var last: Double, var near: Boolean)
    private val seen = HashMap<String, Seen>()
    private val lastPrompt = HashMap<String, Double>()

    /** Every frame: the confirmed reportable boxes. Returns the classes just walked past. */
    fun update(boxes: List<Box>): List<String> {
        val now = clock()
        for (b in boxes) {
            val id = b.id ?: continue
            val s = seen.getOrPut(id) { Seen(b.cls, now, false) }
            s.last = now
            if (b.y2 >= NEAR_Y2) s.near = true
        }
        val passed = mutableListOf<String>()
        seen.entries.removeIf { (_, s) ->
            if (now - s.last < GONE_S) return@removeIf false
            if (s.near && now - (lastPrompt[s.cls] ?: -1e9) >= COOLDOWN_S) { passed += s.cls; lastPrompt[s.cls] = now }
            true
        }
        return passed.distinct()
    }

    companion object {
        // ponytail: calibration knobs. NEAR_Y2 assumes the phone/camera points ahead and down a
        // little; lower it if hazards you walk over never count as "near".
        const val NEAR_Y2 = 0.6
        const val GONE_S = 2.0
        const val COOLDOWN_S = 20.0
    }
}

/** "yes" / "no" in what the recogniser heard; null = neither (keep listening). */
fun yesNo(heard: String): Boolean? {
    val t = heard.lowercase()
    if (Regex("\\b(yes|yeah|yep|yup|sure|okay|ok|log it)\\b").containsMatchIn(t)) return true
    if (Regex("\\b(no|nope|cancel|don't)\\b").containsMatchIn(t)) return false
    return null
}

/** Haversine distance in metres. */
fun metres(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
    val p1 = Math.toRadians(lat1); val p2 = Math.toRadians(lat2)
    val a = sin((p2 - p1) / 2).let { it * it } +
        cos(p1) * cos(p2) * sin(Math.toRadians(lon2 - lon1) / 2).let { it * it }
    return 2 * 6371000 * asin(sqrt(a))
}

data class MapHazard(val id: Int, val type: String, val status: String, val lat: Double, val lon: Double,
                     val mine: Boolean, val fixedAt: Double?)

fun parseHazards(json: String): List<MapHazard> {
    val a = JSONArray(json)
    return (0 until a.length()).map { a.getJSONObject(it) }.map {
        MapHazard(it.getInt("id"), it.getString("type"), it.getString("status"), it.getDouble("lat"),
            it.getDouble("lon"), it.optBoolean("mine", false),
            if (it.isNull("fixed_at")) null else it.optDouble("fixed_at"))
    }
}

/** The next thing to tell the walker about the map, or null. `said` holds keys already spoken
 *  ("12:open", "12:fixed", "12:thanks") so each is said once. Same order as web/index.html:
 *  thank-you (anywhere), then repaired / caution for hazards within WARN_M. */
fun nextNotice(hazards: List<MapHazard>, lat: Double?, lon: Double?, said: Set<String>, nowS: Double): Pair<String, String>? {
    hazards.firstOrNull { it.mine && it.status == "fixed" && "${it.id}:thanks" !in said }?.let {
        return "${it.id}:thanks" to "Thank you. The ${it.type} you reported has been repaired."
    }
    if (lat == null || lon == null) return null
    for (h in hazards) {
        if (h.status == "rejected") continue
        val fixed = h.status == "fixed"
        val key = "${h.id}:" + if (fixed) "fixed" else "open"
        if (key in said || (fixed && "${h.id}:thanks" in said)) continue
        if (fixed && nowS - (h.fixedAt ?: 0.0) > GOOD_NEWS_S) continue
        val d = metres(lat, lon, h.lat, h.lon)
        if (d > WARN_M) continue
        val m = maxOf(5, (d / 5).roundToInt() * 5)
        return key to if (fixed) "Good news. The ${h.type} about $m metres from you has been repaired."
            else "Caution. ${if (h.status == "unconfirmed") "Possible" else "Reported"} ${h.type}, about $m metres from you."
    }
    return null
}

const val WARN_M = 30.0
const val GOOD_NEWS_S = 7 * 86400.0
const val ANSWER_MS = 5000L

/** Talks to the hazard server (plain HTTP on the local Wi-Fi). */
class HazardServer(base: String) {
    private val base = base.trim().trimEnd('/').let { if ("://" in it) it else "http://$it" }

    fun hazards(me: String): List<MapHazard> = parseHazards(call("GET", "/api/hazards?me=" + URLEncoder.encode(me, "UTF-8"), null))

    /** Returns the case's status after this report ("unconfirmed" = first report). */
    fun report(type: String, danger: Int, lat: Double, lon: Double, by: String): String =
        JSONObject(call("POST", "/api/report", JSONObject().put("type", type).put("danger", danger)
            .put("lat", lat).put("lon", lon).put("by", by).toString())).getString("status")

    private fun call(method: String, path: String, body: String?): String {
        val conn = URL(base + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 4000; conn.readTimeout = 4000
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText().orEmpty()
            if (code !in 200..299) throw RuntimeException("hazard server HTTP $code: ${text.take(120)}")
            return text
        } finally { conn.disconnect() }
    }
}

/** The Android side: English voice, yes/no listening, GPS, polling. All slow work runs on one
 *  background thread, so a prompt and a warning never talk over each other. */
class HazardMap(
    private val ctx: Context,
    private val speaker: Speaker,
    private val catalog: ObjectCatalog,
    private val show: (String) -> Unit,               // a line on screen (status / overlay)
    private val askButtons: (Boolean) -> Unit,        // show / hide the YES / NO buttons
) {
    private val prefs = ctx.getSharedPreferences("hazard_map", Context.MODE_PRIVATE)
    private val main = Handler(Looper.getMainLooper())
    private val work = Executors.newSingleThreadScheduledExecutor()
    private val watcher = PassWatcher()   // touched only by the camera thread (frame())
    private val said = HashSet<String>(prefs.getStringSet("thanked", emptySet()).orEmpty())
    private var tts: TextToSpeech? = null
    private val ttsReady = CountDownLatch(1)
    @Volatile private var fix: Location? = null
    @Volatile private var answer: CountDownLatch? = null
    @Volatile private var answered: Boolean? = null
    @Volatile var busy = false; private set
    @Volatile private var polling = false
    @Volatile private var gen = 0   // a stop + start must not leave two polling loops

    val me: String = prefs.getString("me", null) ?: ("lensa-" + (1..5).map { "abcdefghjkmnpqrstuvwxyz23456789".random() }
        .joinToString("")).also { prefs.edit().putString("me", it).apply() }

    var url: String
        get() = prefs.getString("url", "").orEmpty()
        set(v) = prefs.edit().putString("url", v.trim()).apply()
    val enabled get() = url.isNotBlank()

    init {
        tts = TextToSpeech(ctx, { s ->
            if (s == TextToSpeech.SUCCESS) tts?.language = Locale.US
            ttsReady.countDown()
        }, "com.google.android.tts")
    }

    /** MAIN pressed: GPS + polling on (if a server is set). */
    @SuppressLint("MissingPermission")   // caller checks ACCESS_FINE_LOCATION first
    fun start(hasLocation: Boolean) {
        if (!enabled) return
        if (hasLocation) main.post {
            val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
            for (p in listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER))
                if (lm.isProviderEnabled(p)) lm.requestLocationUpdates(p, 2000L, 0f, gps)
        }
        if (!polling) { polling = true; val g = ++gen; work.execute { poll(g) } }
    }

    fun stop() {
        polling = false
        main.post { (ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager).removeUpdates(gps) }
    }

    private val gps = object : LocationListener {
        override fun onLocationChanged(l: Location) {
            val f = fix
            // the network fix is coarse: keep GPS unless it is old
            if (f == null || l.provider == LocationManager.GPS_PROVIDER || l.time - f.time > 10_000) fix = l
        }
        @Deprecated("needed before API 29") override fun onStatusChanged(p: String?, s: Int, e: Bundle?) {}
        override fun onProviderEnabled(p: String) {}
        override fun onProviderDisabled(p: String) {}
    }

    /** Camera thread, every frame: the confirmed boxes. */
    fun frame(boxes: List<Box>) {
        if (!enabled) return
        for (cls in watcher.update(boxes.filter { it.cls in REPORTABLE })) work.execute { prompt(cls) }
    }

    /** The YES / NO buttons (or a key) answer the open question. */
    fun answer(yes: Boolean) { answered = yes; answer?.countDown() }

    private fun prompt(cls: String) {
        if (busy) return
        busy = true
        try { holding {
            val label = catalog.label(cls, "en")
            val at = fix ?: return@holding speak("Passed a $label, but there is no GPS fix, so it cannot be logged.")
            speak("Dangerous hazard found: $label. Do you want to log it?")
            val yes = listen()
            if (yes != true) return@holding speak(if (yes == false) "Okay, not logged." else "No answer. Cancelled.")
            val status = runCatching { HazardServer(url).report(label, catalog.dangerWeight(cls), at.latitude, at.longitude, me) }
                .getOrElse { return@holding speak("Could not reach the hazard map.") }
            skipNear(at)
            speak(if (status == "unconfirmed") "Logged. It will be confirmed when another user passes."
                  else "Logged. Thank you, this hazard is now confirmed for everyone.")
        } } finally { busy = false }
    }

    /** Don't warn me about the hazard I just logged: mark open hazards right here as said. */
    private fun skipNear(at: Location) = runCatching {
        HazardServer(url).hazards(me).filter { metres(at.latitude, at.longitude, it.lat, it.lon) <= WARN_M && it.status != "fixed" }
            .forEach { said += "${it.id}:open" }
    }

    /** Up to ANSWER_MS: voice (Android's recogniser, English) or the on-screen buttons. */
    private fun listen(): Boolean? {
        val latch = CountDownLatch(1)
        answered = null; answer = latch
        var rec: SpeechRecognizer? = null
        main.post {
            askButtons(true)
            if (!SpeechRecognizer.isRecognitionAvailable(ctx)) return@post
            rec = SpeechRecognizer.createSpeechRecognizer(ctx).apply {
                setRecognitionListener(object : RecognitionListener {
                    fun heard(b: Bundle?) {
                        val text = b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.joinToString(" ").orEmpty()
                        yesNo(text)?.let { answer(it) }
                    }
                    override fun onResults(b: Bundle?) { heard(b); restart() }
                    override fun onPartialResults(b: Bundle?) = heard(b)
                    override fun onError(e: Int) {   // silence / no match: keep listening until 5 s
                        if (e != SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) restart()
                    }
                    fun restart() { if (latch.count > 0) main.postDelayed({ if (latch.count > 0) runCatching { startListening(intent()) } }, 150) }
                    override fun onReadyForSpeech(p: Bundle?) {}
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(v: Float) {}
                    override fun onBufferReceived(b: ByteArray?) {}
                    override fun onEndOfSpeech() {}
                    override fun onEvent(t: Int, p: Bundle?) {}
                })
                startListening(intent())
            }
        }
        latch.await(ANSWER_MS, TimeUnit.MILLISECONDS)
        latch.countDown()   // closed: a late restart() or button press does nothing now
        answer = null
        main.post { askButtons(false); rec?.destroy() }
        return answered
    }

    private fun intent() = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
    }

    /** Every 5 s while MAIN runs: fetch the map, say at most one notice. */
    private fun poll(g: Int) {
        if (!polling || g != gen) return
        if (!busy) runCatching {
            val f = fix
            nextNotice(HazardServer(url).hazards(me), f?.latitude, f?.longitude, said, System.currentTimeMillis() / 1000.0)
                ?.let { (key, text) ->
                    said += key
                    if (key.endsWith(":thanks")) prefs.edit().putStringSet("thanked", said.filter { it.endsWith(":thanks") }.toSet()).apply()
                    holding { speak(text) }
                }
        }.onFailure { show("Peta bahaya: tiada sambungan ke $url") }
        work.schedule({ poll(g) }, 5, TimeUnit.SECONDS)
    }

    /** Hazard guidance waits while the map talks or listens (urgent lines still cut in, see
     *  Speaker.hold) — and the recogniser doesn't hear LENSA's own voice. */
    private fun holding(block: () -> Unit) {
        speaker.hold = true
        try {
            val until = System.nanoTime() + 3_000_000_000L
            while (speaker.busy && System.nanoTime() < until) Thread.sleep(50)   // let the current line finish
            block()
        } finally { speaker.hold = false }
    }

    /** English TTS, blocking. */
    private fun speak(text: String) {
        show(text)
        ttsReady.await(5, TimeUnit.SECONDS)
        val t = tts ?: return
        t.speak(text, TextToSpeech.QUEUE_FLUSH, null, "hazard-map")
        Thread.sleep(300)
        while (t.isSpeaking) Thread.sleep(50)
    }

    fun shutdown() { stop(); work.shutdownNow(); tts?.shutdown() }
}
