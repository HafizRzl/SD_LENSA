package my.lensa

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.yaml.snakeyaml.Yaml
import java.io.File

/** The phone finds the same Husein clips the beta plays — or it silently falls back to TTS. */
@Suppress("UNCHECKED_CAST")
class VoiceAssetsTest {
    private val clips = File("src/main/assets/clips")

    @Test fun clipKeyMatchesPython() {
        // python: clip_cache.clip_key("Berhenti!") / ("orang di kiri")
        assertEquals(PY_BERHENTI, clipKey("Berhenti!"))
        assertEquals(PY_ORANG, clipKey("orang di kiri"))
    }

    @Test fun everyMalaySegmentHasAClip() {
        val gold = Yaml().load<Map<String, Any?>>(javaClass.getResource("/golden.json")!!.readText())
        val missing = (gold["scenes"] as List<Map<String, Any?>>).filter { it["lang"] == "ms" }
            .flatMap { s -> (s["expect"] as List<Map<String, Any?>>).flatMap { (it["segments"] as List<String>?).orEmpty() } }
            .toSet().filter { !File(clips, "${clipKey(it)}.wav").exists() }
        assertTrue("segments with no clip in the APK: $missing", missing.isEmpty())
    }

    @Test fun wavDecodesAndTrims() {
        val (pcm, rate) = readWav(File(clips, "${clipKey("Berhenti!")}.wav").readBytes())
        assertEquals(24000, rate)
        assertTrue(pcm.size > rate / 4)
        val t = trim(pcm, rate)
        assertTrue("trimming must shorten the clip", t.size < pcm.size && t.isNotEmpty())
    }

    companion object { const val PY_BERHENTI = "7153b06e45bf3328"; const val PY_ORANG = "6f403a5ad304816a" }
}
