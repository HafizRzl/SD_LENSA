# LENSA Phase 5 — Segment-Clip Malay Voice (Husein) Implementation Plan

> **Status (2026-10-18): not started; written against the July desktop beta.** Since then the beta
> gained object tracking, box-extent steering with hysteresis, an all-blocked map fallback, a
> second (road-hazard) detector, two-clip voice joins and a catalog whitelist; the ToF sensor is
> now the **VL53L8CX** (same 8×8 grid). When building, port behaviour from `desktop-beta/`
> (`lensa_rules.py`, `lensa_beta.py`) — it is the source of truth; this plan is the scaffolding.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).
> **Prerequisite:** Phase 4 complete (Loop A + Loop B + OCR all working).
> **SYNCED 2026-07-07:** rewritten around the segment-clip design the desktop beta has already
> validated (`desktop-beta/lensa_rules.decision_segments` + `render_clips.py`). The old
> whole-line/on-device-synthesis approach is superseded.

**Goal:** The app speaks **real Malay (Husein, Malaysian-TTS-0.6B)** with near-zero latency by
playing **~40 pre-rendered segment clips** shipped in the APK — no neural model on the phone,
comfortably inside the 150 MB budget.

**Architecture:** A `HazardDecision` decomposes into reusable audio segments
(`["orang", "di kiri", "2 meter", "elak ke kanan"]`) played back-to-back. The clips are rendered
**once, on a PC** by `desktop-beta/render_clips.py` and copied into `assets/voice/husein/`.
Dynamic text (OCR readouts, Gemma descriptions) falls back to Android TTS. This mirrors the
desktop beta exactly — same segment inventory, same MD5-hash filenames.

**Tech Stack:** `MediaPlayer` (asset playback), `java.security.MessageDigest` (hash), Android
`TextToSpeech` (fallback). **No new dependencies.**

## Global Constraints

- Phase 1–4 constraints hold (package `com.lensa`, offline, ms default).
- Segment texts and hash scheme must match the desktop renderer **exactly** (MD5 hex, first 16
  chars, UTF-8) — the clips are shared artifacts.
- Loop A never waits on synthesis: cached → play; not cached → Android TTS speaks the line now.

---

### Task 1: Render the clips on the PC and bundle them

- [ ] **Step 1: Render** (on the dev machine, one-time, CPU-slow but resumable):
```bash
python desktop-beta/render_clips.py --prewarm
```
Expected: `desktop-beta/voice_cache/husein/` contains ~41 `<hash16>.wav` files (24 kHz PCM_16,
~100 KB each — a few MB total).

- [ ] **Step 2: Copy into the app**
```bash
mkdir -p android-app/app/src/main/assets/voice/husein
cp desktop-beta/voice_cache/husein/*.wav android-app/app/src/main/assets/voice/husein/
```

- [ ] **Step 3: Commit**
```bash
git add android-app/app/src/main/assets/voice
git commit -m "assets: bundle Husein segment clips (rendered by desktop-beta/render_clips.py)"
```

---

### Task 2: DecisionSegments (Kotlin port of `decision_segments`) + test

**Files:**
- Create: `android-app/app/src/main/java/com/lensa/audio/DecisionSegments.kt`
- Test: `android-app/app/src/test/java/com/lensa/audio/DecisionSegmentsTest.kt`

**Interfaces:**
- Consumes: `HazardDecision` (with `avoid`), `ContentRepository`.
- Produces: `fun decisionSegments(dec: HazardDecision, content: ContentRepository, lang: String = "ms"): List<String>?` and `fun clipKey(text: String): String`.

- [ ] **Step 1: Write the failing test**
```kotlin
package com.lensa.audio

import com.lensa.content.*
import com.lensa.model.*
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

class DecisionSegmentsTest {
    private fun read(p: String) = File("src/main/assets/context/$p").readText()
    private val content = ContentRepository(
        objects = ObjectCatalog.fromMarkdown(read("rules/object_catalog.yaml")),
        hazardRules = HazardRules.fromMarkdown(read("rules/hazard_priority_rules.yaml")),
        ocrRules = OcrRules.fromMarkdown(read("rules/ocr_reading_rules.yaml")),
        phrasesMs = PhraseLibrary.fromMarkdown(read("language/phrase_library_ms.yaml")),
        phrasesEn = PhraseLibrary.fromMarkdown(read("language/phrase_library_en.yaml")),
        audioStyle = AudioStyle.fromMarkdown(read("config/audio_output_style.yaml")),
        safety = SafetyFallback.fromMarkdown(read("config/safety_fallback.yaml")),
        defaults = UserSettings.fromMarkdown(read("config/user_settings_defaults.yaml")),
        prompts = AgentPrompts("", "", "", ""),
    )

    @Test fun steerDecomposesIntoSegments() {
        val dec = HazardDecision(
            NavAction.STEER_RIGHT, "person", ObjectPosition.LEFT, 2.0, Urgency.MEDIUM,
            avoid = ObjectPosition.RIGHT,
        )
        assertEquals(listOf("orang", "di kiri", "2 meter", "elak ke kanan"),
            decisionSegments(dec, content))
    }

    @Test fun stopDecomposes() {
        val dec = HazardDecision(NavAction.STOP, "car", ObjectPosition.CENTER, 0.9, Urgency.HIGH)
        assertEquals(listOf("Berhenti!", "kereta", "di hadapan"), decisionSegments(dec, content))
    }

    @Test fun ocrIsNotSegmentable() {
        val dec = HazardDecision(NavAction.READ, urgency = Urgency.LOW, ocrText = "AWAS")
        assertEquals(null, decisionSegments(dec, content))
    }

    @Test fun hashMatchesDesktopRenderer() {
        // MD5 hex, first 16 — must equal desktop-beta/clip_cache.clip_key("orang")
        assertEquals(16, clipKey("orang").length)
    }
}
```

- [ ] **Step 2: Run → FAIL** (`gradlew.bat :app:testDebugUnitTest --tests com.lensa.audio.DecisionSegmentsTest`).

- [ ] **Step 3: Write `DecisionSegments.kt`**
```kotlin
package com.lensa.audio

import com.lensa.content.ContentRepository
import com.lensa.model.HazardDecision
import com.lensa.model.NavAction
import com.lensa.model.ObjectPosition
import com.lensa.model.Urgency
import java.security.MessageDigest
import kotlin.math.roundToInt

/** Same key scheme as desktop-beta/clip_cache.py: MD5 hex of the UTF-8 text, first 16 chars. */
fun clipKey(text: String): String =
    MessageDigest.getInstance("MD5").digest(text.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }.take(16)

/**
 * Break a HazardDecision into short REUSABLE audio segments for concatenative clip playback
 * (mirror of desktop-beta/lensa_rules.decision_segments — keep them in sync).
 * Returns null when the line isn't segmentable (dynamic text such as OCR, or non-ms lang).
 *
 * ponytail: the frame words ("di", "meter", "elak ke", "Berhenti!", "Hati-hati") mirror the
 * templates in phrase_library_ms.yaml — if you reword a template, reword these together.
 */
fun decisionSegments(
    dec: HazardDecision,
    content: ContentRepository,
    lang: String = "ms",
): List<String>? {
    if (lang != "ms") return null
    val lib = content.phrases(lang)
    val obj = dec.yoloClass?.let { content.objects.label(it, lang) }
    fun di(pos: ObjectPosition) = "di ${lib.direction(pos)}"
    val dist = dec.distanceM?.let { "${it.roundToInt()} meter" }

    when (dec.action) {
        NavAction.PROCEED -> return listOf(lib.render("clear"))
        NavAction.STOP ->
            return listOf("Berhenti!") + listOfNotNull(obj) + listOf(di(ObjectPosition.CENTER))
        NavAction.READ -> return null
        else -> {}
    }

    val segs = mutableListOf<String>()
    if (dec.urgency == Urgency.HIGH) {
        val p = (lib.urgencyPrefix["high"] ?: "").trim()
        if (p.isNotEmpty()) segs += p
    }
    return when (dec.action) {
        NavAction.CAUTION -> {
            if (dist != null) segs + listOfNotNull(obj, di(dec.position ?: ObjectPosition.CENTER), dist)
            else segs + "Hati-hati" + listOfNotNull(obj, di(dec.position ?: ObjectPosition.CENTER))
        }
        NavAction.STEER_LEFT, NavAction.STEER_RIGHT -> {
            val avoid = dec.avoid
                ?: if (dec.action == NavAction.STEER_LEFT) ObjectPosition.LEFT else ObjectPosition.RIGHT
            segs + listOfNotNull(obj, di(dec.position ?: ObjectPosition.CENTER), dist) +
                "elak ke ${lib.direction(avoid)}"
        }
        else -> null
    }
}
```

- [ ] **Step 4: Run → PASS** (4 tests).

- [ ] **Step 5: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/audio/DecisionSegments.kt android-app/app/src/test/java/com/lensa/audio/DecisionSegmentsTest.kt
git commit -m "feat: decisionSegments + clipKey (mirror of the desktop beta, tested)"
```

---

### Task 3: SegmentClipTtsService (plays clips from assets)

**Files:**
- Create: `android-app/app/src/main/java/com/lensa/audio/SegmentClipTtsService.kt`
- Modify: `android-app/app/src/main/java/com/lensa/audio/TtsService.kt` (decision-aware speak)

**Interfaces:**
- Consumes: `TtsService`, `AndroidTtsService` (fallback), `decisionSegments`, `clipKey`.
- Produces: `SegmentClipTtsService(context, content, speaker = "husein")`.

- [ ] **Step 1: Make `TtsService.speak` decision-aware** — in `TtsService.kt` change the
interface method (implementations gain the parameter with a default):
```kotlin
    suspend fun speak(text: String, interrupt: Boolean = false, decision: HazardDecision? = null)
```
Add `import com.lensa.model.HazardDecision`. Update `AndroidTtsService.speak` and
`LogTtsService.speak` signatures to match (they ignore `decision`).

- [ ] **Step 2: Write `SegmentClipTtsService.kt`**
```kotlin
package com.lensa.audio

import android.content.Context
import android.media.MediaPlayer
import com.lensa.content.ContentRepository
import com.lensa.model.HazardDecision
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * Malay voice via pre-rendered segment clips in assets/voice/<speaker>/ — near-zero latency,
 * no model on the phone. A line whose segments are all bundled plays as back-to-back clips;
 * anything else (OCR, Gemma output, missing clip) falls back to Android TTS.
 */
class SegmentClipTtsService(
    private val context: Context,
    private val content: ContentRepository,
    private val speaker: String = "husein",
) : TtsService {

    private val fallback = AndroidTtsService(context)
    private var player: MediaPlayer? = null
    private var lang = "ms"
    private val bundled: Set<String> =
        context.assets.list("voice/$speaker")?.toSet() ?: emptySet()

    override suspend fun init() = fallback.init()

    override suspend fun speak(text: String, interrupt: Boolean, decision: HazardDecision?) {
        if (text.isEmpty()) return
        val segs = decision?.let { decisionSegments(it, content, lang) }
        if (segs != null && segs.all { "${clipKey(it)}.wav" in bundled }) {
            if (interrupt) stopClip()
            for (s in segs) playClip("voice/$speaker/${clipKey(s)}.wav")
            return
        }
        fallback.speak(text, interrupt)
    }

    /** Plays one asset clip to completion (suspends; keeps segments strictly sequential). */
    private suspend fun playClip(assetPath: String) = suspendCancellableCoroutine { cont ->
        try {
            val afd = context.assets.openFd(assetPath)
            val mp = MediaPlayer()
            player = mp
            mp.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            afd.close()
            mp.setOnCompletionListener { it.release(); if (player === it) player = null; if (cont.isActive) cont.resume(Unit) }
            mp.setOnErrorListener { p, _, _ -> p.release(); if (cont.isActive) cont.resume(Unit); true }
            mp.prepare()
            mp.start()
            cont.invokeOnCancellation { runCatching { mp.stop() }; mp.release() }
        } catch (e: Exception) {
            if (cont.isActive) cont.resume(Unit)
        }
    }

    private fun stopClip() {
        player?.let { runCatching { it.stop() }; it.release() }
        player = null
    }

    override suspend fun stop() { stopClip(); fallback.stop() }
    override fun setLanguage(lang: String) { this.lang = lang; fallback.setLanguage(lang) }
    override fun setRate(rate: Double) = fallback.setRate(rate)
    override fun dispose() { stopClip(); fallback.dispose() }
}
```

- [ ] **Step 3: Pass the decision through the pipeline.** In `PipelineController`:
`maybeSpeak`/`speak` already have `r.decision` in scope — change the final call to
`tts.speak(line, interrupt = ..., decision = r.decision)` (and keep `announce()` passing no
decision — dynamic text falls back to Android TTS by design).

- [ ] **Step 4: Build → BUILD SUCCESSFUL.**

- [ ] **Step 5: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/audio android-app/app/src/main/java/com/lensa/pipeline
git commit -m "feat: segment-clip Malay TTS from bundled assets, Android TTS fallback"
```

---

### Task 4: Wire + device test

- [ ] **Step 1: `di/AppConfig.kt`** — add:
```kotlin
    var useSegmentClips = true   // Husein segment clips; false -> plain Android TTS
```

- [ ] **Step 2: `di/AppGraph.kt`** — choose the TTS impl:
```kotlin
    val tts: TtsService = when {
        AppConfig.useLogTts -> LogTtsService()
        AppConfig.useSegmentClips -> SegmentClipTtsService(context, content)
        else -> AndroidTtsService(context)
    }
```

- [ ] **Step 3: Device test.**
  1. Mock pipeline (Phase-1 flags): every standard line — `Berhenti! kereta di hadapan`,
     `orang di hadapan, 2 meter, elak ke kiri`, `Laluan hadapan kosong` — plays in **Husein's
     voice, instantly** (clips, no synthesis).
  2. Tap **Read text** → OCR line plays in the Android TTS voice (dynamic → fallback). Same
     for **Describe scene** (Gemma output).
  3. STOP interrupts a playing segment sequence mid-clip.

- [ ] **Step 4: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/di
git commit -m "feat: segment clips as the default voice (phase 5)"
```

---

## Phase 5 done — what it proves

The safety loop speaks real Malay with near-zero latency, fully offline, from a few MB of WAVs —
no neural TTS on the phone at all. Voice quality upgrades (e.g. Malaysian-Dia-1.6B, a different
speaker) are a **re-render on the PC + re-copy of the assets folder**; zero app-code changes.
This completes the LENSA build order from `docs/ARCHITECTURE.md`.
