# LENSA Phase 2 — Real YOLO on the Phone Implementation Plan

> **Status (2026-10-18): not started; written against the July desktop beta.** Since then the beta
> gained object tracking, box-extent steering with hysteresis, an all-blocked map fallback, a
> second (road-hazard) detector, two-clip voice joins and a catalog whitelist; the ToF sensor is
> now the **VL53L8CX** (same 8×8 grid). When building, port behaviour from `desktop-beta/`
> (`lensa_rules.py`, `lensa_beta.py`) — it is the source of truth; this plan is the scaffolding.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> **Prerequisite:** Phase 1 complete (native skeleton runs and speaks on mocks).

**Goal:** Replace the mock detector with **YOLO26n running on the phone (LiteRT/TFLite)** fed by the phone's own camera (CameraX), so LENSA gives real spoken guidance from a live scene — still no head unit.

**Architecture:** A `CameraXTransport` (implements the existing `HeadUnitTransport`) emits live camera frames as `CameraFrame`s carrying a `Bitmap`. A `RealObjectDetector` runs the quantized model and returns `Detection`s. Everything downstream (rules agent, pipeline, TTS) is unchanged. Distance is `null` this phase (no ToF until Phase 3), which the rules engine already handles as low-urgency caution.

**Tech Stack:** CameraX, TensorFlow Lite (`org.tensorflow:tensorflow-lite` + GPU delegate + support lib).

## Global Constraints

- Everything from Phase 1's constraints still holds (package `com.lensa`, offline, ms default).
- The model file and its labels are **artifacts you supply** — the code has `⚠️ CALIBRATE` markers where it must match the actual export.
- Do not touch the Flutter reference project.

---

## File Structure

```
android-app/app/src/main/
├── assets/
│   ├── yolo26n.tflite            ⚠️ you provide (INT8 export)
│   └── yolo_labels.txt           ⚠️ you provide (one class per line, must map to object_catalog yolo_class)
├── java/com/lensa/
│   ├── model/Models.kt           MODIFY (add Bitmap + normalized centre to CameraFrame/Detection)
│   ├── perception/RealObjectDetector.kt   NEW
│   ├── perception/Nms.kt         NEW (pure, testable)
│   ├── camera/CameraXTransport.kt NEW
│   ├── di/AppGraph.kt            MODIFY (build real transport/detector behind flags)
│   └── ui/MainActivity.kt        MODIFY (camera permission + PreviewView + lifecycle)
├── res/layout/activity_main.xml  MODIFY (add PreviewView behind the text)
└── AndroidManifest.xml           MODIFY (CAMERA permission + feature)
test/java/com/lensa/perception/NmsTest.kt   NEW
```

---

### Task 1: Dependencies + permissions

- [ ] **Step 1: Add deps** to `app/build.gradle.kts` `dependencies { }`
```kotlin
    // CameraX
    implementation("androidx.camera:camera-core:1.3.4")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")
    // TensorFlow Lite (LiteRT). GPU delegate optional but recommended.
    implementation("org.tensorflow:tensorflow-lite:2.14.0")
    implementation("org.tensorflow:tensorflow-lite-gpu:2.14.0")
    implementation("org.tensorflow:tensorflow-lite-support:0.4.4")
```

- [ ] **Step 2: Stop Gradle compressing the model.** In `app/build.gradle.kts` `android { }`:
```kotlin
    androidResources {
        noCompress += "tflite"
    }
```

- [ ] **Step 3: Manifest** — add above `<application>` in `AndroidManifest.xml`:
```xml
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-feature android:name="android.hardware.camera.any" android:required="true" />
```

- [ ] **Step 4: Drop in the model artifacts.** Put `yolo26n.tflite` and `yolo_labels.txt` in `app/src/main/assets/`. `yolo_labels.txt` is one class name per line, in the model's output-index order. ⚠️ CALIBRATE: each line should be a `yolo_class` string that exists in `assets/context/rules/object_catalog.yaml` (person, car, motorcycle, …). COCO-trained models cover person/car/bus/truck/bicycle/bench/bottle/tv (bottle + tv are already catalogued for tabletop tests); curb/drain/pole/stairs need a custom-trained model.

- [ ] **Step 5: Commit**
```bash
git add android-app/app/build.gradle.kts android-app/app/src/main/AndroidManifest.xml
git commit -m "build: add CameraX + TFLite deps and camera permission (phase 2)"
```

---

### Task 2: Extend models for bitmaps + box centre

**Files:** Modify `model/Models.kt`.

**Interfaces:**
- Produces: `CameraFrame(bytes, timestamp, bitmap: Bitmap?)`; `Detection(..., boxCx: Float = 0.5f, boxCy: Float = 0.5f)`.

- [ ] **Step 1: Edit `CameraFrame`** — add an optional bitmap:
```kotlin
class CameraFrame(val bytes: ByteArray, val timestamp: Long, val bitmap: android.graphics.Bitmap? = null)
```

- [ ] **Step 2: Edit `Detection`** — add a normalized box centre (defaults keep Phase 1 mocks/tests valid; Phase 3 fusion reads these to pick a ToF zone):
```kotlin
data class Detection(
    val yoloClass: String,
    val confidence: Double,
    val distanceM: Double?,
    val position: ObjectPosition,
    val movingToward: Boolean,
    val boxCx: Float = 0.5f,   // normalized 0..1 centre-x (for ToF-zone fusion, Phase 3)
    val boxCy: Float = 0.5f,   // normalized 0..1 centre-y
)
```

- [ ] **Step 3: Build to verify** — `gradlew.bat :app:compileDebugKotlin` → BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/model/Models.kt
git commit -m "feat: carry Bitmap + normalized box centre on frames/detections (phase 2)"
```

---

### Task 3: NMS helper (pure + tested)

**Files:** Create `perception/Nms.kt`, test `perception/NmsTest.kt`.

**Interfaces:**
- Produces: `data class RawBox(val cx, cy, w, h: Float, val score: Float, val classId: Int)`; `fun nms(boxes: List<RawBox>, iouThreshold: Float): List<RawBox>`; `fun positionOf(cx: Float): ObjectPosition`.

- [ ] **Step 1: Write the failing test `NmsTest.kt`**
```kotlin
package com.lensa.perception

import com.lensa.model.ObjectPosition
import org.junit.Assert.assertEquals
import org.junit.Test

class NmsTest {
    @Test fun suppressesOverlappingLowerScore() {
        val a = RawBox(0.5f, 0.5f, 0.4f, 0.4f, 0.9f, 0)
        val b = RawBox(0.51f, 0.51f, 0.4f, 0.4f, 0.6f, 0) // heavy overlap, lower score
        val c = RawBox(0.1f, 0.1f, 0.1f, 0.1f, 0.8f, 1)   // separate
        val kept = nms(listOf(a, b, c), iouThreshold = 0.5f)
        assertEquals(setOf(0.9f, 0.8f), kept.map { it.score }.toSet())
    }

    @Test fun positionThirds() {
        assertEquals(ObjectPosition.LEFT, positionOf(0.2f))
        assertEquals(ObjectPosition.CENTER, positionOf(0.5f))
        assertEquals(ObjectPosition.RIGHT, positionOf(0.8f))
    }
}
```

- [ ] **Step 2: Run → FAIL** (`gradlew.bat :app:testDebugUnitTest --tests com.lensa.perception.NmsTest`).

- [ ] **Step 3: Write `Nms.kt`**
```kotlin
package com.lensa.perception

import com.lensa.model.ObjectPosition
import kotlin.math.max
import kotlin.math.min

/** A raw detection in normalized (0..1) centre form, before NMS. */
data class RawBox(
    val cx: Float, val cy: Float, val w: Float, val h: Float,
    val score: Float, val classId: Int,
)

private fun iou(a: RawBox, b: RawBox): Float {
    val ax1 = a.cx - a.w / 2; val ay1 = a.cy - a.h / 2; val ax2 = a.cx + a.w / 2; val ay2 = a.cy + a.h / 2
    val bx1 = b.cx - b.w / 2; val by1 = b.cy - b.h / 2; val bx2 = b.cx + b.w / 2; val by2 = b.cy + b.h / 2
    val ix1 = max(ax1, bx1); val iy1 = max(ay1, by1); val ix2 = min(ax2, bx2); val iy2 = min(ay2, by2)
    val iw = max(0f, ix2 - ix1); val ih = max(0f, iy2 - iy1)
    val inter = iw * ih
    val union = a.w * a.h + b.w * b.h - inter
    return if (union <= 0f) 0f else inter / union
}

/** Greedy non-max suppression, class-agnostic within the incoming list. */
fun nms(boxes: List<RawBox>, iouThreshold: Float): List<RawBox> {
    val sorted = boxes.sortedByDescending { it.score }.toMutableList()
    val kept = mutableListOf<RawBox>()
    while (sorted.isNotEmpty()) {
        val best = sorted.removeAt(0)
        kept += best
        sorted.removeAll { iou(best, it) > iouThreshold }
    }
    return kept
}

/** Map a normalized centre-x to a spoken position (even thirds). */
fun positionOf(cx: Float): ObjectPosition = when {
    cx < 1f / 3f -> ObjectPosition.LEFT
    cx > 2f / 3f -> ObjectPosition.RIGHT
    else -> ObjectPosition.CENTER
}
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/perception/Nms.kt android-app/app/src/test/java/com/lensa/perception/NmsTest.kt
git commit -m "feat: NMS + position mapping (pure, tested) for real detector"
```

---

### Task 4: RealObjectDetector (LiteRT)

**Files:** Create `perception/RealObjectDetector.kt`.

**Interfaces:**
- Consumes: `ObjectDetector` (Phase 1), `Nms.kt` (Task 3), `CameraFrame.bitmap` (Task 2).
- Produces: `RealObjectDetector(context, modelAsset="yolo26n.tflite", labelsAsset="yolo_labels.txt")`.

- [ ] **Step 1: Write `RealObjectDetector.kt`**
```kotlin
package com.lensa.perception

import android.content.Context
import android.graphics.Bitmap
import com.lensa.model.CameraFrame
import com.lensa.model.Detection
import org.tensorflow.lite.Interpreter
import org.tensorflow.lite.gpu.CompatibilityList
import org.tensorflow.lite.gpu.GpuDelegate
import org.tensorflow.lite.support.common.FileUtil
import org.tensorflow.lite.support.image.ImageProcessor
import org.tensorflow.lite.support.image.TensorImage
import org.tensorflow.lite.support.image.ops.ResizeOp
import kotlin.math.max

/**
 * YOLO26n on-device object detector.
 *
 * ⚠️ CALIBRATE to your export:
 *  - INPUT_SIZE: the model's square input (commonly 640).
 *  - Output tensor layout: this parser assumes the ultralytics format
 *    [1, 4 + numClasses, numAnchors] (box xywh first, then per-class scores),
 *    with box coords in PIXELS relative to INPUT_SIZE. If your export is
 *    [1, numAnchors, 4 + numClasses] or already NMS'd, adjust parseOutput().
 *  - CONF_THRESHOLD / IOU_THRESHOLD to taste during walk tests.
 */
class RealObjectDetector(
    context: Context,
    modelAsset: String = "yolo26n.tflite",
    labelsAsset: String = "yolo_labels.txt",
) : ObjectDetector {

    private val labels: List<String> =
        context.assets.open(labelsAsset).bufferedReader().useLines { it.filter { l -> l.isNotBlank() }.toList() }

    private val interpreter: Interpreter
    private var gpu: GpuDelegate? = null

    init {
        val opts = Interpreter.Options()
        if (CompatibilityList().isDelegateSupportedOnThisDevice) {
            gpu = GpuDelegate(); opts.addDelegate(gpu)
        } else {
            opts.numThreads = 4
        }
        interpreter = Interpreter(FileUtil.loadMappedFile(context, modelAsset), opts)
    }

    private val processor = ImageProcessor.Builder()
        .add(ResizeOp(INPUT_SIZE, INPUT_SIZE, ResizeOp.ResizeMethod.BILINEAR))
        .build()

    override suspend fun detect(frame: CameraFrame): List<Detection> {
        val bmp = frame.bitmap ?: return emptyList()
        val input = processor.process(TensorImage.fromBitmap(bmp.copy(Bitmap.Config.ARGB_8888, false)))

        // Output buffer: [1, 4 + numClasses, numAnchors]
        val out = interpreter.getOutputTensor(0)
        val shape = out.shape()                 // e.g. [1, 84, 8400]
        val rows = shape[1]                      // 4 + numClasses
        val anchors = shape[2]
        val output = Array(1) { Array(rows) { FloatArray(anchors) } }
        interpreter.run(input.buffer, output)

        val boxes = parseOutput(output[0], rows, anchors)
        val kept = nms(boxes, IOU_THRESHOLD)
        return kept.map {
            Detection(
                yoloClass = labels.getOrElse(it.classId) { "unknown" },
                confidence = it.score.toDouble(),
                distanceM = null,               // ToF arrives in Phase 3
                position = positionOf(it.cx),
                movingToward = false,
                boxCx = it.cx,
                boxCy = it.cy,
            )
        }
    }

    private fun parseOutput(o: Array<FloatArray>, rows: Int, anchors: Int): List<RawBox> {
        val numClasses = rows - 4
        val result = ArrayList<RawBox>()
        for (a in 0 until anchors) {
            var bestScore = 0f; var bestClass = -1
            for (c in 0 until numClasses) {
                val s = o[4 + c][a]
                if (s > bestScore) { bestScore = s; bestClass = c }
            }
            if (bestScore < CONF_THRESHOLD || bestClass < 0) continue
            // xywh in pixels relative to INPUT_SIZE -> normalize 0..1
            val cx = o[0][a] / INPUT_SIZE
            val cy = o[1][a] / INPUT_SIZE
            val w = o[2][a] / INPUT_SIZE
            val h = o[3][a] / INPUT_SIZE
            result += RawBox(cx, cy, max(0f, w), max(0f, h), bestScore, bestClass)
        }
        return result
    }

    override fun dispose() {
        interpreter.close()
        gpu?.close()
    }

    companion object {
        const val INPUT_SIZE = 640          // ⚠️ CALIBRATE
        const val CONF_THRESHOLD = 0.35f    // ⚠️ CALIBRATE
        const val IOU_THRESHOLD = 0.45f     // ⚠️ CALIBRATE
    }
}
```

- [ ] **Step 2: Build to verify** — `gradlew.bat :app:compileDebugKotlin` → BUILD SUCCESSFUL. (Runtime correctness depends on the model; verified on device in Task 6.)

- [ ] **Step 3: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/perception/RealObjectDetector.kt
git commit -m "feat: on-device YOLO26n detector (LiteRT) with calibratable post-processing"
```

---

### Task 5: CameraXTransport (phone camera as frame source)

**Files:** Create `camera/CameraXTransport.kt`.

**Interfaces:**
- Consumes: `HeadUnitTransport` (Phase 1).
- Produces: `CameraXTransport(context, lifecycleOwner, previewView?)` — frames from the phone camera; `sensor` never emits (no ToF this phase); `connection` = CONNECTED once bound.

- [ ] **Step 1: Write `CameraXTransport.kt`**
```kotlin
package com.lensa.camera

import android.content.Context
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.lensa.model.CameraFrame
import com.lensa.model.HeadUnitConnectionState
import com.lensa.model.SensorReading
import com.lensa.transport.HeadUnitTransport
import kotlinx.coroutines.flow.*
import java.util.concurrent.Executors

/** Uses the phone's own camera as the frame source (stand-in for the head unit until Phase 3). */
class CameraXTransport(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val previewView: PreviewView? = null,
) : HeadUnitTransport {

    private val _frames = MutableSharedFlow<CameraFrame>(extraBufferCapacity = 2, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    private val _conn = MutableStateFlow(HeadUnitConnectionState.DISCONNECTED)

    override val frames: Flow<CameraFrame> = _frames
    override val sensor: Flow<SensorReading> = emptyFlow()   // no ToF yet
    override val connection: Flow<HeadUnitConnectionState> = _conn

    private val executor = Executors.newSingleThreadExecutor()
    private var provider: ProcessCameraProvider? = null

    override suspend fun connect() {
        _conn.value = HeadUnitConnectionState.CONNECTING
        provider = ProcessCameraProvider.getInstance(context).get()

        val analysis = ImageAnalysis.Builder()
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .build()
        analysis.setAnalyzer(executor) { proxy ->
            val bmp = proxy.toBitmap()           // camera-core 1.3+
            _frames.tryEmit(CameraFrame(ByteArray(0), System.currentTimeMillis(), bmp))
            proxy.close()
        }

        val preview = Preview.Builder().build().also { p ->
            previewView?.let { p.setSurfaceProvider(it.surfaceProvider) }
        }

        ContextCompat.getMainExecutor(context).execute {
            provider?.unbindAll()
            provider?.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            _conn.value = HeadUnitConnectionState.CONNECTED
        }
    }

    override suspend fun disconnect() {
        ContextCompat.getMainExecutor(context).execute { provider?.unbindAll() }
        _conn.value = HeadUnitConnectionState.DISCONNECTED
    }

    override fun dispose() {
        executor.shutdown()
        provider?.unbindAll()
    }
}
```

- [ ] **Step 2: Build to verify** → BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/camera/CameraXTransport.kt
git commit -m "feat: CameraXTransport — phone camera as frame source (phase 2)"
```

---

### Task 6: Wire it up + camera permission + run

**Files:** Modify `di/AppGraph.kt`, `ui/MainActivity.kt`, `res/layout/activity_main.xml`.

- [ ] **Step 1: `AppGraph.kt`** — build real transport/detector behind the flags. Replace the transport/detector lines:
```kotlin
class AppGraph(
    context: Context,
    scope: CoroutineScope,
    lifecycleOwner: androidx.lifecycle.LifecycleOwner,
    previewView: androidx.camera.view.PreviewView,
) {
    val content = ContentRepository.fromAssets(context.assets)
    val settings = content.defaults

    private val transport = if (AppConfig.useMockTransport) MockTransport()
        else com.lensa.camera.CameraXTransport(context, lifecycleOwner, previewView)
    private val detector = if (AppConfig.useMockDetector) MockObjectDetector()
        else com.lensa.perception.RealObjectDetector(context)
    private val ocr = MockOcrService()
    private val agent = RulesNavigationAgent(content) { settings }
    val tts: TtsService = if (AppConfig.useLogTts) LogTtsService() else AndroidTtsService(context)

    val pipeline = PipelineController(content, { settings }, transport, detector, ocr, agent, tts, scope)

    fun dispose() { tts.dispose(); transport.dispose(); detector.dispose() }
}
```

- [ ] **Step 2: Flip the flags** in `di/AppConfig.kt`:
```kotlin
    var useMockTransport = false   // -> CameraXTransport (phone camera)
    var useMockDetector = false    // -> RealObjectDetector (YOLO26n)
```

- [ ] **Step 3: `activity_main.xml`** — add a `PreviewView` as the first child (behind the text) so the camera has a surface:
```xml
    <androidx.camera.view.PreviewView
        android:id="@+id/preview"
        android:layout_width="match_parent"
        android:layout_height="160dp" />
```

- [ ] **Step 4: `MainActivity.kt`** — request camera permission, pass the PreviewView + lifecycle to `AppGraph`. Replace `onCreate`'s graph construction and add the permission launcher:
```kotlin
    private val cameraPermission =
        registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) initGraph() else binding.txtStatus.text = "Camera permission needed"
        }

    private fun initGraph() {
        graph = AppGraph(applicationContext, lifecycleScope, this, binding.preview)
        observeState()
    }
```
Call in `onCreate` (after `setContentView`), replacing the direct `graph = AppGraph(...)`:
```kotlin
        if (androidx.core.content.ContextCompat.checkSelfPermission(this, android.Manifest.permission.CAMERA)
            == android.content.pm.PackageManager.PERMISSION_GRANTED) initGraph()
        else cameraPermission.launch(android.Manifest.permission.CAMERA)
```
Move the `pipeline.state` collector into an `observeState()` method and guard button handlers with `if (::graph.isInitialized)`.

- [ ] **Step 5: Run on device.** Grant camera. Tap **Start**, point at a person/car/chair. Expected: spoken Bahasa Melayu guidance driven by real detections (no distance yet → "Hati-hati, …" / "Elak ke …"). Confirm the preview shows the camera.

⚠️ If it says nothing or wrong classes: check `yolo_labels.txt` order and the `parseOutput` layout assumption against your export (Task 4 knobs).

- [ ] **Step 6: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/di android-app/app/src/main/java/com/lensa/ui android-app/app/src/main/res/layout/activity_main.xml
git commit -m "feat: wire CameraX + YOLO into the pipeline behind flags; camera permission (phase 2)"
```

---

## Phase 2 done — what it proves

Real YOLO26n runs on the phone from the live camera and drives correct spoken guidance through the unchanged rules engine and TTS. The only calibration surface is the model export (input size, tensor layout, labels) — all isolated in `RealObjectDetector` + the two asset files.
