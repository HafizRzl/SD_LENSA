package my.lensa

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.speech.tts.TextToSpeech
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs

// Port of the beta's voice path: Husein clips (assets/clips/<md5[:16]>.wav, same key as
// desktop-beta/clip_cache.py), trimmed + joined into one buffer; Android TTS when a clip is
// missing. Plus the SpeechGate (when to speak) and a one-line-at-a-time Speaker thread.

fun clipKey(text: String): String =
    MessageDigest.getInstance("MD5").digest(text.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }.take(16)

/** 16-bit PCM WAV -> (samples, sampleRate). Clips are mono 24 kHz from render_clips.py. */
fun readWav(bytes: ByteArray): Pair<ShortArray, Int> {
    val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
    var rate = 24000
    var i = 12
    while (i + 8 <= bytes.size) {
        val id = String(bytes, i, 4, Charsets.US_ASCII)
        val len = bb.getInt(i + 4)
        if (id == "fmt ") rate = bb.getInt(i + 12)
        if (id == "data") {
            val n = minOf(len, bytes.size - i - 8) / 2
            return ShortArray(n) { bb.getShort(i + 8 + it * 2) } to rate
        }
        i += 8 + len + (len and 1)
    }
    return ShortArray(0) to rate
}

private const val GAP_S = 0.08
private const val FADE_S = 0.012

/** Cut the TTS's leading/trailing silence, keep 40 ms for soft onsets, fade the edges. */
fun trim(x: ShortArray, rate: Int): ShortArray {
    val peak = x.maxOfOrNull { abs(it.toInt()) } ?: return x
    val thr = maxOf(peak * 0.02, 3.0)
    val first = x.indexOfFirst { abs(it.toInt()) > thr }
    if (first < 0) return x
    val last = x.indexOfLast { abs(it.toInt()) > thr }
    val pad = (0.04 * rate).toInt()
    val y = x.copyOfRange(maxOf(0, first - pad), minOf(x.size, last + pad))
    val f = minOf((FADE_S * rate).toInt(), y.size / 2)
    for (k in 0 until f) {
        val g = k / f.toDouble()
        y[k] = (y[k] * g).toInt().toShort()
        y[y.size - 1 - k] = (y[y.size - 1 - k] * g).toInt().toShort()
    }
    return y
}

class Voice(private val ctx: Context) {
    private var tts: TextToSpeech? = null
    private val ready = CountDownLatch(1)
    private val cache = HashMap<String, Pair<ShortArray, Int>?>()

    /** Android TTS for free text (Loop B answers, lines with no clip). Prefers Google's engine —
     *  it has a Malay voice; Samsung's default may not. Android falls back to the phone's default
     *  engine by itself if Google's isn't installed. */
    fun init() {
        val onInit = TextToSpeech.OnInitListener { status ->
            if (status == TextToSpeech.SUCCESS) {
                val ms = Locale("ms", "MY")
                if ((tts?.isLanguageAvailable(ms) ?: -1) >= TextToSpeech.LANG_AVAILABLE) tts?.language = ms
            }
            ready.countDown()
        }
        tts = TextToSpeech(ctx, onInit, "com.google.android.tts")
    }

    private fun clip(text: String): Pair<ShortArray, Int>? = cache.getOrPut(text) {
        runCatching { readWav(ctx.assets.open("clips/${clipKey(text)}.wav").use { it.readBytes() }) }.getOrNull()
    }

    /** Blocks until spoken. Clips if every segment is cached, else Android TTS for the line. */
    fun speak(line: String, segments: List<String>?) {
        val parts = segments?.map { clip(it) }
        if (parts != null && parts.all { it != null }) playJoined(parts.map { it!! }) else speakTts(line)
    }

    private fun playJoined(parts: List<Pair<ShortArray, Int>>) {
        val rate = parts[0].second
        val gap = ShortArray((GAP_S * rate).toInt())
        val pcm = parts.flatMapIndexed { i, (x, r) -> if (i == 0) listOf(trim(x, r)) else listOf(gap, trim(x, r)) }
            .let { chunks -> ShortArray(chunks.sumOf { it.size }).also { out ->
                var o = 0; for (c in chunks) { c.copyInto(out, o); o += c.size } } }
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(pcm.size * 2, AudioTrack.getMinBufferSize(rate,
                AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)))
            .build()
        try {
            track.play()
            track.write(pcm, 0, pcm.size)
            Thread.sleep((pcm.size * 1000L / rate) + 50)   // wait for playback to finish
            track.stop()
        } finally { track.release() }
    }

    private fun speakTts(line: String) {
        ready.await(5, TimeUnit.SECONDS)            // first request may arrive before TTS is up
        val t = tts ?: return
        t.speak(line, TextToSpeech.QUEUE_FLUSH, null, "lensa")
        Thread.sleep(300)
        while (t.isSpeaking) Thread.sleep(50)
    }

    fun shutdown() { tts?.shutdown() }
}

/** When to speak THIS line — same policy as the beta's SpeechGate: lines compared by MEANING
 *  (meaningKey), gaps timed from when the voice went quiet (quietS). */
class SpeechGate(var s: AudioStyle, private val bands: DistanceBands,
                 var quietS: () -> Double = { Double.POSITIVE_INFINITY },
                 private val clock: () -> Double = { System.nanoTime() / 1e9 }) {
    private var lastKey: List<Any?>? = null
    private var lastAt = -1e9
    private var blockedIncident: Set<String>? = null
    private var blockedSaid = 0

    fun waitRemaining(dec: Decision, high: Boolean): Double {
        val same = meaningKey(dec, bands) == lastKey
        val since = minOf(clock() - lastAt, quietS())
        if (same && high) return maxOf(0.0, s.repeatHighS - since)
        if (same && !s.speakWhenUnchanged) return maxOf(0.0, s.repeatNormalS - since)
        if (!high) return maxOf(0.0, s.minGapS - since)
        return 0.0
    }

    fun shouldSpeak(line: String, high: Boolean, dec: Decision): Boolean {
        if (dec.action == "blocked" && dec.incident == blockedIncident && blockedSaid >= s.blockedMaxRepeats) return false
        return line.isNotEmpty() && waitRemaining(dec, high) <= 0.0
    }

    fun mark(dec: Decision) {
        lastKey = meaningKey(dec, bands); lastAt = clock()
        if (dec.action == "blocked") {
            if (dec.incident != blockedIncident) { blockedIncident = dec.incident; blockedSaid = 0 }
            blockedSaid += 1
        }
    }
}

/** What to say next — same order as the beta's Speaker: an URGENT hazard line first, then a
 *  waiting Loop B answer, then a routine hazard line. A newer hazard line replaces a waiting one;
 *  the answer has its own slot, so a routine line can't push it out. */
class SpeechQueue {
    class Item(val line: String, val segments: List<String>?, val urgent: Boolean, val free: Boolean)
    private var pending: Item? = null
    private var free: Item? = null

    @Synchronized fun put(line: String, segments: List<String>?, urgent: Boolean = false, isFree: Boolean = false) {
        val it = Item(line, segments, urgent, isFree)
        if (isFree) free = it else pending = it
    }

    @Synchronized fun next(): Item? {
        val p = pending
        if (p != null && p.urgent) { pending = null; return p }
        free?.let { free = null; return it }
        pending = null
        return p
    }

    /** Only an urgent hazard line (used while the hazard map holds the voice). */
    @Synchronized fun nextUrgent(): Item? = pending?.takeIf { it.urgent }?.also { pending = null }

    @Synchronized fun isEmpty() = pending == null && free == null
    @Synchronized fun clear() { pending = null; free = null }
}

/** One dedicated thread, one line at a time. Lives as long as the app (Loop B can speak while
 *  hazard guidance is stopped). */
class Speaker(private val voice: Voice) {
    @Volatile var busy = false; private set
    @Volatile private var endedAt = 0.0
    private val queue = SpeechQueue()
    @Volatile private var running = true
    /** Set while the hazard map asks / listens / warns: only URGENT hazard lines are spoken. */
    @Volatile var hold = false

    /** Seconds of silence since the last line FINISHED (0 while speaking or a line is waiting). */
    fun quietS(): Double = if (busy || !queue.isEmpty()) 0.0 else System.nanoTime() / 1e9 - endedAt

    private val thread = Thread {
        voice.init()
        while (running) {
            val item = if (hold) queue.nextUrgent() else queue.next()
            if (item == null) { Thread.sleep(30); continue }
            busy = true
            runCatching { voice.speak(item.line, if (item.free) null else item.segments) }
            endedAt = System.nanoTime() / 1e9
            busy = false
        }
        voice.shutdown()
    }.apply { isDaemon = true; start() }

    /** Hazard line (Loop A) or a Loop B wait clip. */
    fun say(line: String, segments: List<String>?, urgent: Boolean = false) = queue.put(line, segments, urgent)
    /** Free text (a Loop B answer): Android TTS, never dropped by routine hazard lines. */
    fun sayFree(text: String) = queue.put(text, null, isFree = true)
    fun clear() = queue.clear()
    fun stop() { running = false }
}
