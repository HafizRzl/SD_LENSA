# LENSA Phase 4 — Loop B (Gemma) + OCR Implementation Plan

> **Status (2026-10-18): not started; written against the July desktop beta.** Since then the beta
> gained object tracking, box-extent steering with hysteresis, an all-blocked map fallback, a
> second (road-hazard) detector, two-clip voice joins and a catalog whitelist; the ToF sensor is
> now the **VL53L8CX** (same 8×8 grid). When building, port behaviour from `desktop-beta/`
> (`lensa_rules.py`, `lensa_beta.py`) — it is the source of truth; this plan is the scaffolding.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).
> **Prerequisite:** Phase 3 complete (real head unit + distance-aware Loop A).

**Goal:** Add the two on-demand features: **Loop B** — a natural Malay scene description from recent YOLO labels via **Gemma 3n (MediaPipe LLM Inference)** — and real **OCR** (ML Kit) behind the existing "Read text" button. Neither ever blocks the safety loop.

**Architecture:** Both are user-triggered and latency-tolerant, run on `Dispatchers.IO`, and speak through the existing TTS. Loop A (Phase 1–3) is untouched. See `docs/ARCHITECTURE.md` § Loop B.

**Tech Stack:** `com.google.mediapipe:tasks-genai`, `com.google.mlkit:text-recognition`, Play Services Tasks await.

## Global Constraints

- Gemma runs **on-demand only** — never per-frame, never in Loop A.
- The Gemma model file is an **artifact you provision** on the device; the code has a `⚠️ CALIBRATE` model path + version.
- Offline; ms default.

---

### Task 1: Dependencies

- [ ] **Step 1: Add deps** to `app/build.gradle.kts`:
```kotlin
    implementation("com.google.mlkit:text-recognition:16.0.1")
    implementation("com.google.mediapipe:tasks-genai:0.10.14")   // ⚠️ CALIBRATE version to match your Gemma .task/.bin
```

- [ ] **Step 2: Commit**
```bash
git add android-app/app/build.gradle.kts
git commit -m "build: add ML Kit OCR + MediaPipe tasks-genai (phase 4)"
```

---

### Task 2: Real OCR (ML Kit)

**Files:** Create `perception/MlKitOcrService.kt`.

**Interfaces:**
- Consumes: `OcrService` (Phase 1), `CameraFrame.bitmap`.
- Produces: `MlKitOcrService()`.

- [ ] **Step 1: Write `MlKitOcrService.kt`**
```kotlin
package com.lensa.perception

import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.lensa.model.CameraFrame
import com.lensa.model.OcrResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Reads Latin-script signage (Malay/English) with ML Kit Text Recognition v2. */
class MlKitOcrService : OcrService {
    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    override suspend fun recognize(frame: CameraFrame): OcrResult? = withContext(Dispatchers.IO) {
        val bmp = frame.bitmap ?: return@withContext null
        val result = Tasks.await(recognizer.process(InputImage.fromBitmap(bmp, 0)))
        val text = result.text.trim().replace("\n", " ")
        // ML Kit has no single overall confidence; treat a non-empty read as reliable.
        if (text.isEmpty()) null else OcrResult(text, 0.9)
    }

    override fun dispose() { recognizer.close() }
}
```

- [ ] **Step 2: Wire it** in `di/AppGraph.kt` — replace `MockOcrService()` behind a flag:
```kotlin
    private val ocr = if (AppConfig.useMockOcr) MockOcrService() else com.lensa.perception.MlKitOcrService()
```
And in `di/AppConfig.kt` set `var useMockOcr = false`.

- [ ] **Step 3: Build → BUILD SUCCESSFUL.**

- [ ] **Step 4: Device test.** Point at a sign, tap **Read text**. Expected: it speaks the sign text prefixed per `ocr_reading_rules.yaml` (e.g. "Papan tanda: …"). (The rules agent already routes OCR text through the `ocr` template.)

- [ ] **Step 5: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/perception/MlKitOcrService.kt android-app/app/src/main/java/com/lensa/di
git commit -m "feat: real OCR via ML Kit behind flag (phase 4)"
```

---

### Task 3: Recent-labels buffer on the pipeline

**Files:** Modify `pipeline/PipelineController.kt`.

**Interfaces:**
- Produces: `PipelineController.recentLabels(): List<String>`.

- [ ] **Step 1: Add a field + expose it.** In `PipelineController`, add:
```kotlin
    @Volatile private var lastLabels: List<String> = emptyList()

    /** Distinct object classes from the most recent processed frame (for Loop B). */
    fun recentLabels(): List<String> = lastLabels
```

- [ ] **Step 2: Populate it** in `onFrame`, right after `dets = applyTof(...)`:
```kotlin
            lastLabels = dets.map { it.yoloClass }.distinct()
```

- [ ] **Step 3: Build → BUILD SUCCESSFUL.**

- [ ] **Step 4: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/pipeline/PipelineController.kt
git commit -m "feat: expose recent detection labels for Loop B"
```

---

### Task 4: Gemma scene describer (Loop B)

**Files:** Create `navigation/GemmaSceneDescriber.kt`.

**Interfaces:**
- Consumes: `ContentRepository` (for language/labels), MediaPipe `LlmInference`.
- Produces: `GemmaSceneDescriber(context, content, modelPath)` with `suspend fun describe(labels: List<String>, lang: String): String`.

- [ ] **Step 1: Provision the model.** Put a Gemma 3n `.task`/`.bin` on the device (e.g. push to `/data/local/tmp/gemma3n.task` via `adb push`, or bundle in app storage). ⚠️ CALIBRATE the path in Step 2 and the `tasks-genai` version to match the model.

- [ ] **Step 2: Write `GemmaSceneDescriber.kt`**
```kotlin
package com.lensa.navigation

import android.content.Context
import com.google.mediapipe.tasks.genai.llminference.LlmInference
import com.lensa.content.ContentRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Loop B: turns recent YOLO labels into a short, natural description.
 * On-demand only — created lazily, never called per-frame.
 *
 * ⚠️ CALIBRATE: modelPath must point at a real Gemma 3n .task/.bin on the device.
 */
class GemmaSceneDescriber(
    context: Context,
    private val content: ContentRepository,
    modelPath: String = "/data/local/tmp/gemma3n.task",
) {
    private val llm: LlmInference = LlmInference.createFromOptions(
        context,
        LlmInference.LlmInferenceOptions.builder()
            .setModelPath(modelPath)
            .setMaxTokens(256)
            .setTemperature(0.7f)
            .setTopK(40)
            .build(),
    )

    suspend fun describe(labels: List<String>, lang: String): String = withContext(Dispatchers.IO) {
        if (labels.isEmpty()) return@withContext if (lang == "en") "Nothing detected." else "Tiada objek dikesan."
        // Speak object names in the user's language for a natural sentence.
        val named = labels.joinToString(", ") { content.objects.label(it, lang) }
        val prompt = if (lang == "en") {
            "You help a blind person. Detected objects: $named. " +
                "Describe the scene in one or two short, plain sentences. No lists."
        } else {
            "Anda membantu orang buta. Objek dikesan: $named. " +
                "Terangkan pemandangan dalam satu atau dua ayat Bahasa Melayu yang ringkas. Jangan senaraikan."
        }
        llm.generateResponse(prompt).trim()
    }

    fun dispose() = llm.close()

    companion object {
        fun modelAvailable(path: String = "/data/local/tmp/gemma3n.task") = File(path).exists()
    }
}
```

- [ ] **Step 3: Build → BUILD SUCCESSFUL.** (Runtime needs the model file; verified in Task 5.)

- [ ] **Step 4: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/navigation/GemmaSceneDescriber.kt
git commit -m "feat: Gemma 3n scene describer for Loop B (on-demand)"
```

---

### Task 5: Wire Loop B to the "Describe scene" button

**Files:** Modify `di/AppGraph.kt`, `ui/MainActivity.kt`.

- [ ] **Step 1: `AppGraph.kt`** — build the describer only if the model is present (graceful when absent):
```kotlin
    val describer: com.lensa.navigation.GemmaSceneDescriber? =
        if (com.lensa.navigation.GemmaSceneDescriber.modelAvailable())
            com.lensa.navigation.GemmaSceneDescriber(context, content) else null
```
Add `describer?.dispose()` to `AppGraph.dispose()`.

- [ ] **Step 2: `MainActivity.kt`** — replace the `btnDescribe` handler:
```kotlin
        binding.btnDescribe.setOnClickListener {
            if (!::graph.isInitialized) return@setOnClickListener
            lifecycleScope.launch {
                val d = graph.describer
                if (d == null) { graph.pipeline.announce("Penerangan pemandangan belum tersedia"); return@launch }
                graph.pipeline.announce("Sebentar")   // "one moment" — Gemma has latency
                val text = d.describe(graph.pipeline.recentLabels(), graph.settings.language)
                graph.pipeline.announce(text)
            }
        }
```

- [ ] **Step 3: Device test.** With the model provisioned and Loop A running, tap **Describe scene**. Expected: after a short pause, a natural Malay sentence describing the detected objects (e.g. "Ada orang di hadapan dan kereta di sebelah kanan."). Loop A keeps warning throughout — Loop B never blocks it.

⚠️ If the model is absent, the button gracefully says "belum tersedia" — no crash.

- [ ] **Step 4: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/di android-app/app/src/main/java/com/lensa/ui
git commit -m "feat: wire Loop B (Gemma) to Describe button; graceful when model absent (phase 4)"
```

---

## Phase 4 done — what it proves

On-demand richness without touching the safety loop: real OCR reads signage, and Gemma turns the scene into natural Malay on request. Both degrade gracefully (empty read / missing model). Calibration surface: the Gemma model file + `tasks-genai` version.
