package my.lensa

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/** The hazard map's decisions (same rules as ../../web/index.html), without a phone. */
class HazardMapTest {
    private fun pothole(id: String, y2: Double) = Box(0.4, y2 - 0.1, 0.6, y2, 0.8, "pothole", id)

    @Test fun passedOnlyAfterComingCloseThenLeavingView() {
        var t = 0.0
        val w = PassWatcher { t }
        assertEquals(emptyList<String>(), w.update(listOf(pothole("r1", 0.4))))   // far ahead
        t = 1.0; assertEquals(emptyList<String>(), w.update(listOf(pothole("r1", 0.9))))   // close
        t = 2.0; assertEquals(emptyList<String>(), w.update(emptyList()))   // just left the view
        t = 3.1; assertEquals(listOf("pothole"), w.update(emptyList()))      // gone 2 s -> walked past
        t = 4.0; assertEquals(emptyList<String>(), w.update(emptyList()))    // said once
    }

    @Test fun seenOnlyFarAwayIsNotPassed() {
        var t = 0.0
        val w = PassWatcher { t }
        w.update(listOf(pothole("r1", 0.3)))
        t = 5.0
        assertEquals(emptyList<String>(), w.update(emptyList()))   // turned away / lost it: no question
    }

    @Test fun oneQuestionPerClassWhileTheTrackerReassignsIds() {
        var t = 0.0
        val w = PassWatcher { t }
        w.update(listOf(pothole("r1", 0.9)))
        t = 3.0; assertEquals(listOf("pothole"), w.update(listOf(pothole("r2", 0.9))))   // same pothole, new ID
        t = 6.0; assertEquals(emptyList<String>(), w.update(emptyList()))                 // r2 gone: inside cooldown
        t = 30.0; w.update(listOf(pothole("r3", 0.9)))
        t = 33.0; assertEquals(listOf("pothole"), w.update(emptyList()))                  // later: a new one
    }

    @Test fun yesNoWords() {
        assertEquals(true, yesNo("Yes"))
        assertEquals(true, yesNo("okay log it"))
        assertEquals(false, yesNo("no thanks"))
        assertEquals(false, yesNo("cancel"))
        assertNull(yesNo("I know"))     // "no" inside a word doesn't count
        assertNull(yesNo(""))
    }

    @Test fun haversine() {
        val d = metres(4.38500, 100.97900, 4.38505, 100.97905)
        assertTrue("~8 m, got $d", d in 7.0..9.0)
    }

    // the shape server.py sends (extra fields ignored)
    private val json = """[
        {"id": 1, "type": "open manhole", "status": "fixed", "lat": 4.385, "lon": 100.979, "mine": true, "fixed_at": 1000.0, "events": []},
        {"id": 2, "type": "pothole", "status": "confirmed", "lat": 4.3852, "lon": 100.979, "mine": false, "fixed_at": null},
        {"id": 3, "type": "blocked walkway", "status": "rejected", "lat": 4.385, "lon": 100.979, "mine": false, "fixed_at": null},
        {"id": 4, "type": "drain", "status": "unconfirmed", "lat": 4.3900, "lon": 100.979, "mine": false, "fixed_at": null}
    ]"""

    @Test fun parsesServerJson() {
        val h = parseHazards(json)
        assertEquals(listOf(1, 2, 3, 4), h.map { it.id })
        assertEquals(1000.0, h[0].fixedAt!!, 0.0)
        assertNull(h[1].fixedAt)
        assertTrue(h[0].mine)
    }

    @Test fun noticesInOrderAndOnce() {
        val h = parseHazards(json)
        val said = HashSet<String>()
        fun next() = nextNotice(h, 4.3850, 100.9790, said, 2000.0)?.also { said += it.first }
        assertEquals("1:thanks", next()!!.first)                        // reporter thanked first
        val caution = next()!!
        assertEquals("2:open", caution.first)                           // ~22 m away
        assertTrue(caution.second, caution.second.startsWith("Caution. Reported pothole, about 20 metres"))
        assertNull(next())   // rejected #3 never said; #4 is ~550 m away; #1's repair already covered by the thanks
    }

    @Test fun repairNewsForOthersNearby() {
        val h = parseHazards(json).map { if (it.id == 1) it.copy(mine = false) else it }
        val n = nextNotice(h, 4.3850, 100.9790, emptySet(), 2000.0)!!
        assertEquals("1:fixed", n.first)
        assertTrue(n.second.startsWith("Good news."))
        assertNull(nextNotice(h, 4.3850, 100.9790, setOf("1:fixed", "2:open"), 1000.0 + GOOD_NEWS_S + 1))
    }

    @Test fun noGpsNoDistanceNotices() =
        assertNull(nextNotice(parseHazards(json).map { it.copy(mine = false) }, null, null, emptySet(), 2000.0))

    /** Real round trip to server.py. Skipped unless app/live-map.flag exists (one line: the
     *  server address), so normal builds never touch the network. */
    @Test fun liveServerRoundTrip() {
        val flag = File("live-map.flag")
        assumeTrue("live map test not requested", flag.exists())
        val server = HazardServer(flag.readText().trim())
        val status = server.report("pothole", 7, 4.40000, 101.00000, "kotlin-test")
        assertEquals("unconfirmed", status)
        val mine = server.hazards("kotlin-test").filter { it.mine }
        println("LIVE map: ${mine.size} case(s) by kotlin-test, first ${mine.firstOrNull()}")
        assertTrue(mine.any { it.type == "pothole" && it.status == "unconfirmed" })
    }
}
