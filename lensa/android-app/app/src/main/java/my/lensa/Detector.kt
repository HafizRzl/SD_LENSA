package my.lensa

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import java.nio.FloatBuffer

/** One YOLO26n ONNX model (exported with NMS: output [1, 300, 6] = x1,y1,x2,y2,score,class in
 *  640-px letterbox coords). Preprocessing matches Ultralytics: keep aspect, pad grey 114, /255. */
class Detector(env: OrtEnvironment, model: ByteArray, val engine: String = "cpu") {
    private val session: OrtSession
    private val env = env
    val names: List<String>
    private val canvasBmp = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
    private val pixels = IntArray(SIZE * SIZE)
    private val input = FloatBuffer.allocate(3 * SIZE * SIZE)

    init {
        val opts = OrtSession.SessionOptions().apply {
            // XNNPACK runs its own thread pool; ORT's must then be 1 or the two fight for cores
            setIntraOpNumThreads(if (engine == "xnnpack") 1 else 4)
            when (engine) {
                "xnnpack" -> addXnnpack(mapOf("intra_op_num_threads" to "4"))
                "nnapi" -> addNnapi()
            }
        }
        session = env.createSession(model, opts)
        // Ultralytics stores class names in the model metadata: "{0: 'person', 1: 'bicycle', ...}"
        val raw = session.metadata.customMetadata["names"].orEmpty()
        names = Regex("""(\d+):\s*'([^']*)'""").findAll(raw)
            .sortedBy { it.groupValues[1].toInt() }
            .map { it.groupValues[2].lowercase().replace(" ", "_") }.toList()
    }

    /** Boxes with confidence >= conf, in normalised (0..1) coords of [bmp]. */
    fun detect(bmp: Bitmap, conf: Double): List<Box> {
        val scale = minOf(SIZE.toFloat() / bmp.width, SIZE.toFloat() / bmp.height)
        val padX = (SIZE - bmp.width * scale) / 2f
        val padY = (SIZE - bmp.height * scale) / 2f
        Canvas(canvasBmp).apply {
            drawColor(Color.rgb(114, 114, 114))
            drawBitmap(bmp, Matrix().apply { postScale(scale, scale); postTranslate(padX, padY) }, null)
        }
        canvasBmp.getPixels(pixels, 0, SIZE, 0, 0, SIZE, SIZE)
        val plane = SIZE * SIZE
        input.clear()
        val f = input.array()
        for (i in 0 until plane) {
            val p = pixels[i]
            f[i] = ((p shr 16) and 0xFF) / 255f
            f[plane + i] = ((p shr 8) and 0xFF) / 255f
            f[2 * plane + i] = (p and 0xFF) / 255f
        }
        val out = ArrayList<Box>()
        OnnxTensor.createTensor(env, input, longArrayOf(1, 3, SIZE.toLong(), SIZE.toLong())).use { t ->
            session.run(mapOf(session.inputNames.first() to t)).use { res ->
                @Suppress("UNCHECKED_CAST")
                val rows = (res[0].value as Array<Array<FloatArray>>)[0]
                for (r in rows) {
                    if (r[4] < conf) continue
                    val cls = names.getOrNull(r[5].toInt()) ?: continue
                    fun nx(x: Float) = ((x - padX) / scale / bmp.width).toDouble().coerceIn(0.0, 1.0)
                    fun ny(y: Float) = ((y - padY) / scale / bmp.height).toDouble().coerceIn(0.0, 1.0)
                    out.add(Box(nx(r[0]), ny(r[1]), nx(r[2]), ny(r[3]), r[4].toDouble(), cls))
                }
            }
        }
        return out
    }

    fun close() = session.close()

    companion object {
        const val SIZE = 640
        private val ENGINES = listOf("xnnpack", "nnapi", "cpu")

        /** Time each engine on THIS phone (median of 3 runs after a warm-up) and keep the fastest.
         *  Returns the detector and its ms per frame. An engine that fails to load is skipped. */
        fun fastest(env: OrtEnvironment, model: ByteArray): Pair<Detector, Double> {
            val probe = Bitmap.createBitmap(480, 640, Bitmap.Config.ARGB_8888)
            var best: Pair<Detector, Double>? = null
            for (e in ENGINES) {
                val d = runCatching { Detector(env, model, e) }.getOrNull() ?: continue
                val ms = runCatching {
                    d.detect(probe, 0.99)
                    List(3) { val t = System.nanoTime(); d.detect(probe, 0.99); (System.nanoTime() - t) / 1e6 }.sorted()[1]
                }.getOrNull()
                if (ms != null && (best == null || ms < best.second)) { best?.first?.close(); best = d to ms }
                else d.close()
            }
            return best ?: (Detector(env, model) to Double.NaN)
        }
    }
}
