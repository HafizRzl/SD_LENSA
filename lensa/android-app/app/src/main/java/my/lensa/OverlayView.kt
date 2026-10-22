package my.lensa

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View

/** Draws what LENSA sees on top of the camera preview — the phone version of the beta window:
 *  boxes + labels (cyan = announced, grey = ignored), the left/centre/right lines, and the
 *  spoken line in a banner coloured by urgency. Boxes are in normalised coords of the upright
 *  analysis frame (3:4), drawn into the same fit-centre rectangle the PreviewView uses. */
class OverlayView(ctx: Context) : View(ctx) {
    class Item(val box: Box, val label: String, val announced: Boolean)

    @Volatile private var items: List<Item> = emptyList()
    @Volatile private var line = ""
    @Volatile private var urgency = "low"
    @Volatile private var info = ""
    @Volatile private var mapGrid: Array<BooleanArray>? = null
    @Volatile private var mapPath: List<Pair<Int, Int>> = emptyList()
    private val cell = Paint()
    @Volatile private var answer = ""
    @Volatile private var answerUntil = 0L
    private val answerBg = Paint().apply { color = Color.argb(225, 40, 20, 60) }
    private val answerText = Paint().apply { color = Color.WHITE; textSize = 40f; isAntiAlias = true }

    private val boxPaint = Paint().apply { style = Paint.Style.STROKE; strokeWidth = 6f }
    private val textPaint = Paint().apply { textSize = 38f; isFakeBoldText = true; isAntiAlias = true }
    private val guide = Paint().apply { color = Color.argb(120, 255, 255, 255); strokeWidth = 3f }
    private val banner = Paint()
    private val bannerText = Paint().apply { color = Color.WHITE; textSize = 46f; isFakeBoldText = true; isAntiAlias = true }
    private val infoText = Paint().apply { color = Color.WHITE; textSize = 30f; isAntiAlias = true; setShadowLayer(4f, 0f, 0f, Color.BLACK) }

    fun show(items: List<Item>, line: String, urgency: String, info: String,
             grid: Array<BooleanArray>? = null, path: List<Pair<Int, Int>> = emptyList()) {
        this.items = items; this.line = line; this.urgency = urgency; this.info = info
        this.mapGrid = grid; this.mapPath = path
        postInvalidate()
    }

    /** Top-down pacman map (same snapshot the voice fallback uses): you = green at the bottom,
     *  red = obstacle, black = unseen, yellow = BFS route. Forward is up. */
    private fun drawMap(c: Canvas, r: RectF) {
        val g = mapGrid ?: return
        val rows = PacmanMap.ROWS; val cols = PacmanMap.COLS
        val px = r.width() * 0.34f / cols
        val left = r.right - cols * px - 12; val top = r.top + 60
        cell.color = Color.argb(200, 0, 0, 0)
        c.drawRect(left - 4, top - 4, left + cols * px + 4, top + rows * px + 4, cell)
        val unseen = PacmanMap.unseen
        for (row in 0 until rows) for (col in 0 until cols) {
            cell.color = when { g[row][col] -> Color.rgb(200, 40, 40); unseen[row][col] -> Color.rgb(10, 10, 10)
                                else -> Color.rgb(55, 55, 55) }
            val y = top + (rows - 1 - row) * px
            c.drawRect(left + col * px + 1, y + 1, left + (col + 1) * px - 1, y + px - 1, cell)
        }
        cell.color = Color.rgb(255, 220, 0)
        for ((row, col) in mapPath) c.drawCircle(left + (col + 0.5f) * px, top + (rows - 1 - row + 0.5f) * px, px / 5, cell)
        cell.color = Color.rgb(0, 200, 0)
        c.drawCircle(left + (cols / 2 + 0.5f) * px, top + (rows - 0.5f) * px, px / 3, cell)
    }

    fun clear() = show(emptyList(), "", "low", "")

    /** Loop B status / answer, shown for [seconds] (wrapped, under the fps line). */
    fun showAnswer(text: String, seconds: Int = 15) {
        answer = text; answerUntil = System.currentTimeMillis() + seconds * 1000L
        postInvalidate()
        postDelayed({ invalidate() }, seconds * 1000L + 50)
    }

    private fun drawAnswer(c: Canvas, r: RectF) {
        if (answer.isEmpty() || System.currentTimeMillis() > answerUntil) return
        val maxW = r.width() - 48
        val lines = mutableListOf<String>()
        var cur = ""
        for (w in answer.split(" ")) {
            val t = if (cur.isEmpty()) w else "$cur $w"
            if (answerText.measureText(t) > maxW && cur.isNotEmpty()) { lines.add(cur); cur = w } else cur = t
        }
        if (cur.isNotEmpty()) lines.add(cur)
        val shown = lines.take(8)
        val top = r.top + 70
        c.drawRect(r.left + 12, top, r.right - 12, top + 24 + shown.size * 50f, answerBg)
        shown.forEachIndexed { i, ln -> c.drawText(ln, r.left + 24, top + 54 + i * 50f, answerText) }
    }

    /** Fit-centre rectangle of a 3:4 (portrait) frame inside this view — matches PreviewView FIT_CENTER. */
    private fun frameRect(): RectF {
        val aspect = 3f / 4f
        val w = width.toFloat(); val h = height.toFloat()
        return if (w / h > aspect) { val fw = h * aspect; RectF((w - fw) / 2, 0f, (w + fw) / 2, h) }
        else { val fh = w / aspect; RectF(0f, (h - fh) / 2, w, (h + fh) / 2) }
    }

    override fun onDraw(c: Canvas) {
        val r = frameRect()
        for (i in 1..2) { val x = r.left + r.width() * i / 3; c.drawLine(x, r.top, x, r.bottom, guide) }
        for (it in items) {
            val col = if (it.announced) Color.rgb(40, 200, 230) else Color.GRAY
            boxPaint.color = col; textPaint.color = col
            val b = it.box
            val l = r.left + b.x1.toFloat() * r.width(); val t = r.top + b.y1.toFloat() * r.height()
            c.drawRect(l, t, r.left + b.x2.toFloat() * r.width(), r.top + b.y2.toFloat() * r.height(), boxPaint)
            c.drawText(it.label, l + 6, maxOf(t - 10, r.top + 40), textPaint)
        }
        if (info.isNotEmpty()) c.drawText(info, r.left + 16, r.top + 44, infoText)
        drawMap(c, r)
        drawAnswer(c, r)
        if (line.isNotEmpty()) {
            banner.color = when (urgency) { "high" -> Color.rgb(200, 30, 30); "medium" -> Color.rgb(230, 120, 0); else -> Color.argb(210, 20, 20, 20) }
            c.drawRect(r.left, r.bottom - 90, r.right, r.bottom, banner)
            c.drawText(line, r.left + 20, r.bottom - 30, bannerText)
        }
    }
}
