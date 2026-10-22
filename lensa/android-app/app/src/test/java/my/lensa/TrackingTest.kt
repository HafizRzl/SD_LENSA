package my.lensa

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/** Same checks as `lensa_beta.py --self-test`, plus the IoU tracker that replaces ByteTrack. */
class TrackingTest {
    @Test fun sideDeadBand() {
        assertEquals("center", positionOf(0.34))
        assertEquals("left", positionOf(0.34, "left"))     // inside the margin -> keeps its side
        assertEquals("center", positionOf(0.40, "left"))   // clearly past -> switches
    }

    @Test fun spokenMetresHeldThroughJitter() {
        val t = Tracks()
        val said = listOf(2.4, 2.6, 2.45, 2.55, 2.6).map { t.update("7", 0.5, it).third }.toSet()
        assertEquals(setOf(2), said)
    }

    @Test fun dedupeKeepsMostConfident() {
        val a = Box(0.0, 0.0, 0.10, 0.10, 0.9, "chair")
        val dup = Box(0.01, 0.0, 0.11, 0.10, 0.6, "bench")
        val other = Box(0.5, 0.0, 0.6, 0.1, 0.8, "chair")
        assertEquals(listOf(a, other), dedupe(listOf(dup, a, other)))
    }

    @Test fun confirmAfterThreeFramesInARow() {
        val t = Tracks()
        val seen = List(3) { t.tick(); t.update("9", 0.5, 2.0); t.confirmed("9", 3) }
        assertEquals(listOf(false, false, true), seen)
        t.tick(); t.tick(); t.update("9", 0.5, 2.0)          // missed a frame -> count restarts
        assertEquals(false, t.confirmed("9", 3))
    }

    @Test fun gateComparesMeaningAndWaitsForSilence() {
        val ctx = java.io.File("../../assets/context")
        val c = Content.load { java.io.File(ctx, it).readText() }
        var now = 100.0
        var quiet = 0.5
        val g = SpeechGate(c.audio, c.hazard.bands, quietS = { quiet }, clock = { now })
        g.mark(Decision("steer_right", "medium", "person", "left", 1.8, avoid = "right"))
        now += 10
        // same meaning (distance jitter inside the caution band) -> not news
        assertEquals(false, g.shouldSpeak("x", false, Decision("steer_right", "medium", "person", "left", 2.3, avoid = "right")))
        val new = Decision("steer_left", "medium", "person", "right", 2.0, avoid = "left")
        assertEquals(false, g.shouldSpeak("x", false, new))   // only 0.5 s of silence
        quiet = 2.5
        assertEquals(true, g.shouldSpeak("x", false, new))
    }

    @Test fun trackerKeepsIdsAcrossFrames() {
        var now = 0.0
        val tr = IouTracker("", clock = { now })
        val f1 = tr.assign(listOf(Box(0.1, 0.1, 0.3, 0.6, 0.9, "person"), Box(0.6, 0.1, 0.8, 0.6, 0.8, "person")))
        now += 0.1
        val f2 = tr.assign(listOf(Box(0.62, 0.1, 0.82, 0.6, 0.8, "person"), Box(0.12, 0.1, 0.32, 0.6, 0.9, "person")))
        assertEquals(f1.map { it.id }.toSet(), f2.map { it.id }.toSet())
        assertEquals(f1[0].id, f2[1].id)                    // the left person kept its ID
        now += 5.0                                          // gone long enough -> forgotten
        val f3 = tr.assign(listOf(Box(0.1, 0.1, 0.3, 0.6, 0.9, "person")))
        assertNotEquals(f1[0].id, f3[0].id)
    }
}
