package my.lensa

import ai.onnxruntime.OrtEnvironment
import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Matrix
import android.hardware.camera2.CameraCharacteristics
import android.os.Bundle
import android.util.Size
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.atan

/** LENSA v0.4 — live view + mini-map on top; PLAY | STOP and TERANGKAN | BACA below.
 *  Loop A (hazards) is the desktop beta's brain, ported 1:1 and offline.
 *  Loop B (describe / read a sign) is online: one photo to Gemini, on demand, even when stopped. */
class MainActivity : AppCompatActivity() {
    private val lang = "ms"
    private lateinit var content: Content
    private lateinit var status: TextView
    private lateinit var preview: PreviewView
    private lateinit var overlay: OverlayView
    private lateinit var speaker: Speaker
    private lateinit var loopB: LoopB
    private var detectors: List<Triple<Detector, IouTracker, Double>>? = null
    private var provider: ProcessCameraProvider? = null
    private var capture: ImageCapture? = null          // bound while PLAY runs (null if the phone can't)
    @Volatile private var lastFrame: Bitmap? = null    // fallback photo: the latest analysis frame
    private val worker = Executors.newSingleThreadExecutor()
    private val photoExec = Executors.newSingleThreadExecutor()
    @Volatile private var running = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        content = Content.load { p -> assets.open("context/$p").bufferedReader().readText() }
        speaker = Speaker(Voice(this))
        val key = runCatching { assets.open("loopb.bin").use { it.readBytes() } }.getOrNull()?.let(LoopBKey::decode)
        loopB = LoopB(lang, content.phrases(lang), { p -> assets.open("context/$p").bufferedReader().readText() },
            key?.let { (k, m) -> GeminiClient(k, m)::ask }) { text, isWait ->
            if (isWait) { speaker.say(text, listOf(text)); overlay.showAnswer("Bertanya Gemini…", 25) }
            else {
                speaker.sayFree(text); overlay.showAnswer(text)
                if (loopB.lastError.isNotEmpty()) runOnUiThread { status.text = "Loop B: ${loopB.lastError}".take(160) }
            }
        }

        fun big(label: String, desc: String, color: Int, onTap: () -> Unit) = Button(this).apply {
            text = label; textSize = 34f; setTextColor(Color.WHITE); setBackgroundColor(color)
            contentDescription = desc; setOnClickListener { onTap() }
        }
        fun row(vararg b: Button) = LinearLayout(this).apply {
            b.forEach { addView(it, LinearLayout.LayoutParams(0, -1, 1f)) }
        }
        preview = PreviewView(this).apply {
            scaleType = PreviewView.ScaleType.FIT_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            setBackgroundColor(Color.BLACK)
        }
        overlay = OverlayView(this)
        status = TextView(this).apply { textSize = 13f; gravity = Gravity.CENTER; setPadding(8, 6, 8, 6) }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(FrameLayout(this@MainActivity).apply {
                addView(preview, FrameLayout.LayoutParams(-1, -1))
                addView(this@MainActivity.overlay, FrameLayout.LayoutParams(-1, -1))   // not View.overlay
            }, LinearLayout.LayoutParams(-1, 0, 5f))
            addView(status, LinearLayout.LayoutParams(-1, -2))
            addView(row(big("PLAY", "Mula LENSA", Color.rgb(20, 120, 60)) { play() },
                        big("STOP", "Hentikan LENSA", Color.rgb(150, 30, 30)) { stop() }), LinearLayout.LayoutParams(-1, 0, 2f))
            addView(row(big("TERANGKAN", "Terangkan sekeliling", Color.rgb(20, 70, 150)) { askB(describe = true) },
                        big("BACA", "Baca tulisan", Color.rgb(100, 40, 140)) { askB(describe = false) }), LinearLayout.LayoutParams(-1, 0, 2f))
        })
        status.text = if (key == null) "Tekan PLAY untuk mula · (Loop B: tiada kunci API dalam APK ini)" else "Tekan PLAY untuk mula"
    }

    private fun hasCamera() = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    private fun play() {
        if (running) return
        if (!hasCamera()) { ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), REQ_PLAY); return }
        running = true
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (detectors == null) status.text = "Memuatkan model & menguji enjin pantas… (sekali sahaja)"
        worker.execute {
            if (detectors == null) {
                val env = OrtEnvironment.getEnvironment()
                detectors = listOf("yolo26n.onnx" to "", "road_hazard_yolo26n.onnx" to "r").map { (f, prefix) ->
                    val (d, ms) = Detector.fastest(env, assets.open("models/$f").use { it.readBytes() })
                    Triple(d, IouTracker(prefix), ms)
                }
            }
            runOnUiThread { if (running) startCamera() }
        }
    }

    private fun stop() {
        running = false
        provider?.unbindAll(); capture = null; lastFrame = null
        speaker.clear()
        overlay.clear()
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        status.text = "Dihentikan. Tekan PLAY untuk mula"
    }

    /** TERANGKAN / BACA — works anytime; the photo is taken on Loop B's background thread. */
    private fun askB(describe: Boolean) {
        if (!hasCamera()) { ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), REQ_ASK); return }
        if (describe) loopB.describe(::photo) else loopB.readSign(::photo)
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        val ok = results.firstOrNull() == PackageManager.PERMISSION_GRANTED
        when {
            !ok -> status.text = "LENSA perlukan kamera"
            code == REQ_PLAY -> play()
            else -> status.text = "Kamera dibenarkan — tekan TERANGKAN / BACA semula"
        }
    }

    private fun fourThree() = ResolutionSelector.Builder()
        .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY).build()

    /** ~1600×1200 stills: enough to read a sign, small enough to upload quickly. */
    private fun newCapture() = ImageCapture.Builder()
        .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
        .setResolutionSelector(ResolutionSelector.Builder()
            .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
            .setResolutionStrategy(ResolutionStrategy(Size(1600, 1200), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
            .build())
        .build()

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val p = future.get().also { provider = it }
            // Same 4:3 shape for preview and analysis, so the overlay's boxes line up with the video.
            val pv = Preview.Builder().setResolutionSelector(fourThree()).build()
                .also { it.surfaceProvider = preview.surfaceProvider }
            val analysis = ImageAnalysis.Builder()
                .setResolutionSelector(fourThree())
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
            val loop = Loop()
            analysis.setAnalyzer(worker) { img -> loop.frame(img) }
            p.unbindAll()
            val cap = newCapture()
            val cam = try {
                p.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, pv, analysis, cap).also { capture = cap }
            } catch (e: Exception) {   // some phones can't run 3 camera uses at once -> photos use the analysis frame
                capture = null
                p.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, pv, analysis)
            }
            PacmanMap.fovDeg = portraitHorizontalFov(cam.cameraInfo) ?: 50.0
        }, ContextCompat.getMainExecutor(this))
    }

    /** One JPEG for Loop B (called on Loop B's background thread). While PLAY runs: a still from
     *  the bound capture (or the last analysis frame). Stopped: open the camera just for this photo. */
    private fun photo(): ByteArray? {
        val live = capture
        if (running && live == null) return lastFrame?.let(::jpeg)
        val done = CountDownLatch(1)
        var out: ByteArray? = null
        fun shoot(cap: ImageCapture, after: () -> Unit) = cap.takePicture(photoExec, object : ImageCapture.OnImageCapturedCallback() {
            override fun onCaptureSuccess(img: ImageProxy) {
                out = runCatching { jpeg(upright(img)) }.getOrNull(); img.close(); after(); done.countDown()
            }
            override fun onError(e: ImageCaptureException) { after(); done.countDown() }
        })
        runOnUiThread {
            if (running && live != null) shoot(live) {}
            else {
                val future = ProcessCameraProvider.getInstance(this)
                future.addListener({
                    val p = future.get().also { provider = it }
                    val cap = newCapture()
                    try {
                        p.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, cap)
                        shoot(cap) { runOnUiThread { if (!running) p.unbind(cap) } }
                    } catch (e: Exception) { done.countDown() }
                }, ContextCompat.getMainExecutor(this))
            }
        }
        done.await(10, TimeUnit.SECONDS)
        return out
    }

    private fun jpeg(bmp: Bitmap): ByteArray {
        val s = 1600f / maxOf(bmp.width, bmp.height)
        val b = if (s < 1f) Bitmap.createScaledBitmap(bmp, (bmp.width * s).toInt(), (bmp.height * s).toInt(), true) else bmp
        return ByteArrayOutputStream().also { b.compress(Bitmap.CompressFormat.JPEG, 85, it) }.toByteArray()
    }

    private fun upright(img: ImageProxy): Bitmap {
        val b = img.toBitmap()
        val r = img.imageInfo.rotationDegrees
        return if (r == 0) b else Bitmap.createBitmap(b, 0, 0, b.width, b.height, Matrix().apply { postRotate(r.toFloat()) }, true)
    }

    /** Horizontal field of view with the phone held upright = the sensor's SHORT side. */
    @androidx.annotation.OptIn(androidx.camera.camera2.interop.ExperimentalCamera2Interop::class)
    private fun portraitHorizontalFov(info: androidx.camera.core.CameraInfo): Double? = runCatching {
        val c = Camera2CameraInfo.from(info)
        val size = c.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)!!
        val f = c.getCameraCharacteristic(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)!![0]
        Math.toDegrees(2 * atan(minOf(size.width, size.height) / (2.0 * f)))
    }.getOrNull()

    /** The per-frame pipeline — same steps as the desktop beta's main loop. */
    private inner class Loop {
        private val agent = RulesAgent(content, lang)
        private val gate = SpeechGate(content.audio, content.hazard.bands, quietS = speaker::quietS)
        private val tracks = Tracks()
        private var fps = 0.0
        private var last = System.nanoTime()

        fun frame(img: ImageProxy) {
            try {
                if (!running) return
                val bmp = upright(img)
                lastFrame = bmp
                val dets = detectors ?: return
                val t0 = System.nanoTime()
                // one after the other: in parallel they fought over the CPU cores (v0.2: 265-377 ms
                // per frame vs ~170 ms for the two models alone). Keeps all boxes for drawing.
                val all = dets.flatMap { (det, tracker, _) -> tracker.assign(det.detect(bmp, CONF)) }
                val detMs = (System.nanoTime() - t0) / 1e6
                val boxes = all.filter { it.cls in content.objects }
                tracks.tick()
                val seen = dedupe(boxes).map { b ->
                    val cx = (b.x1 + b.x2) / 2
                    val raw = if (content.objects.isGround(b.cls)) null else estimateDistanceM(b.y2 - b.y1)
                    val (pos, dist, spoken) = tracks.update(b.id, cx, raw)
                    Detection(b.cls, b.conf, dist, pos, cx = cx, widthN = b.x2 - b.x1, spokenM = spoken, trackId = b.id)
                }
                // confirm before speaking: only objects seen N frames in a row reach the rules
                val detections = seen.filter { tracks.confirmed(it.trackId, content.hazard.confirmFrames) }
                tracks.prune()
                val (dec, line) = agent.run(detections)
                if (gate.shouldSpeak(line, dec.urgency == "high", dec)) {
                    gate.mark(dec)
                    speaker.say(line, decisionSegments(dec, content, lang), urgent = dec.urgency == "high")
                }
                val now = System.nanoTime()
                fps = 0.9 * fps + 0.1 * 1e9 / (now - last); last = now
                val byId = seen.associateBy { it.trackId }
                val confirmedIds = detections.map { it.trackId }.toSet()
                overlay.show(all.map { b ->
                    val d = byId[b.id]
                    val tag = "#${b.id} " + content.objects.label(b.cls, lang) + (d?.distanceM?.let { " ~%.1fm".format(it) } ?: "")
                    OverlayView.Item(b, tag, b.cls in content.objects && b.id in confirmedIds)   // grey = ignored or not yet confirmed
                }, line, dec.urgency, "%.1f fps".format(fps), agent.map.grid, agent.map.path)
                val engines = dets.joinToString(" + ") { (d, _, ms) -> "${d.engine} %.0fms".format(ms) }
                runOnUiThread {
                    if (running && !loopB.busy) status.text = "%.1f fps · kesan %.0f ms · %s · FOV %.0f°"
                        .format(fps, detMs, engines, PacmanMap.fovDeg)
                }
            } finally { img.close() }
        }
    }

    override fun onDestroy() { stop(); speaker.stop(); worker.shutdown(); photoExec.shutdown(); super.onDestroy() }

    companion object {
        const val CONF = 0.35   // detector floor; the rules then apply 0.45
        const val REQ_PLAY = 1
        const val REQ_ASK = 2
    }
}
