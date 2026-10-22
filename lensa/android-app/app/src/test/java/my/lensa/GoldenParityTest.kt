package my.lensa

import org.junit.Assert.assertEquals
import org.junit.Test
import org.yaml.snakeyaml.Yaml
import java.io.File

/** The phone must say exactly what the desktop beta says. golden.json is written by
 *  android-app/make_golden.py from the Python code; regenerate it after changing the rules. */
@Suppress("UNCHECKED_CAST")
class GoldenParityTest {
    private val ctx = File("../../assets/context")
    private val content = Content.load { File(ctx, it).readText() }
    private val gold = Yaml().load<Map<String, Any?>>(javaClass.getResource("/golden.json")!!.readText())

    private fun num(x: Any?) = (x as Number?)?.toDouble()

    private fun det(m: Map<String, Any?>): Detection {
        val cx = num(m["cx"])!!
        val pos = if (cx < 1.0 / 3) "left" else if (cx > 2.0 / 3) "right" else "center"
        return Detection(m["cls"] as String, num(m["conf"])!!, num(m["dist"]), pos, cx = cx,
            widthN = num(m["w"]), spokenM = (m["spoken"] as Number?)?.toInt(), trackId = m["tid"] as String?)
    }

    @Test fun scenesSayTheSame() {
        for (s in gold["scenes"] as List<Map<String, Any?>>) {
            val lang = s["lang"] as String
            val agent = RulesAgent(content, lang)
            val frames = s["frames"] as List<List<Map<String, Any?>>>
            val expect = s["expect"] as List<Map<String, Any?>>
            frames.forEachIndexed { i, f ->
                val (dec, line) = agent.run(f.map(::det))
                val e = expect[i]
                val tag = "${s["name"]} [$lang] frame $i"
                assertEquals("$tag action", e["action"], dec.action)
                assertEquals("$tag urgency", e["urgency"], dec.urgency)
                assertEquals("$tag avoid", e["avoid"], dec.avoid)
                assertEquals("$tag line", e["line"], line)
                assertEquals("$tag segments", e["segments"], decisionSegments(dec, content, lang))
                assertEquals("$tag meaning key", e["key"], meaningKey(dec, content.hazard.bands))
            }
        }
    }

    @Test fun mapsRouteTheSame() {
        for (m in gold["maps"] as List<Map<String, Any?>>) {
            val hits = (m["hits"] as List<List<Number>>).map { PacmanMap.Hit(it[0].toDouble(), it[1].toDouble(), it[2].toDouble()) }
            val path = PacmanMap.bfsDeepest(PacmanMap.paint(hits))
            assertEquals("${m["name"]} path", m["path"], path.map { listOf(it.first, it.second) })
            assertEquals("${m["name"]} turn", m["turn"], PacmanMap.firstTurn(path))
        }
    }
}
