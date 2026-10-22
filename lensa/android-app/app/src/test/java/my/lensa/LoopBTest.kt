package my.lensa

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.yaml.snakeyaml.Yaml
import java.io.File

/** Loop B on the phone: same prompts + sign lines as desktop-beta/loop_b.py (golden.json),
 *  the Gemini request/response format, the request flow, the key scrambling and the speech queue. */
@Suppress("UNCHECKED_CAST")
class LoopBTest {
    private val ctx = File("../../assets/context")
    private fun read(p: String) = File(ctx, p).readText()
    private val content = Content.load(::read)
    private val rules = Yaml().load<Map<String, Any?>>(read("rules/ocr_reading_rules.yaml"))
    private val gold = (Yaml().load<Map<String, Any?>>(javaClass.getResource("/golden.json")!!.readText()))["loopb"] as Map<String, Any?>

    @Test fun promptsMatchPython() {
        val p = gold["prompts"] as Map<String, String>
        for (lang in listOf("ms", "en")) {
            assertEquals(p["describe_$lang"], LoopBText.describePrompt(read("agents/loopb_describe_scene.md"), lang))
            assertEquals(p["read_$lang"], LoopBText.readPrompt(read("agents/loopb_read_sign.md"), lang, rules))
        }
    }

    @Test fun signLinesMatchPython() {
        for (s in gold["signs"] as List<Map<String, Any?>>) {
            val lang = s["lang"] as String
            assertEquals("raw=${s["raw"]}", s["line"], LoopBText.signLine(s["raw"] as String, rules, content.phrases(lang)))
        }
    }

    @Test fun geminiRequestAndResponseFormat() {
        val req = JSONObject(GeminiClient.requestJson("PROMPT", "QUJD"))
        assertEquals("PROMPT", req.getJSONObject("system_instruction").getJSONArray("parts").getJSONObject(0).getString("text"))
        val parts = req.getJSONArray("contents").getJSONObject(0).getJSONArray("parts")
        assertEquals("image/jpeg", parts.getJSONObject(0).getJSONObject("inline_data").getString("mime_type"))
        assertEquals("QUJD", parts.getJSONObject(0).getJSONObject("inline_data").getString("data"))
        val resp = """{"candidates":[{"content":{"role":"model","parts":[
            {"text":"thinking...","thought":true},{"text":"Ada kedai "},{"text":"di kiri."}]}}]}"""
        assertEquals("Ada kedai di kiri.", GeminiClient.parseText(resp))
        assertEquals("", GeminiClient.parseText("""{"promptFeedback":{"blockReason":"SAFETY"}}"""))
    }

    private fun run(ask: ((String, ByteArray) -> String)?, describe: Boolean = true, photo: ByteArray? = byteArrayOf(1)): List<String> {
        val said = mutableListOf<String>()
        val lb = LoopB("ms", content.phrases("ms"), ::read, ask) { t, _ -> synchronized(said) { said.add(t) } }
        if (describe) lb.describe { photo } else lb.readSign { photo }
        repeat(200) { if (!lb.busy) return@repeat; Thread.sleep(10) }
        Thread.sleep(20)
        return synchronized(said) { said.toList() }
    }

    @Test fun flowSpeaksWaitThenAnswerOrAFailure() {
        assertEquals(listOf("Sebentar", "Ada kedai di kiri."), run({ _, _ -> "Ada kedai di kiri." }))
        assertEquals(listOf("Sebentar", "Tiada internet. Fungsi ini perlukan internet"),
            run({ _, _ -> throw java.io.IOException("offline") }))
        assertEquals(listOf("Kunci API belum ditetapkan"), run(null))
        assertEquals(listOf("Sebentar", "Maaf, Gemini tidak dapat menjawab sekarang"), run({ _, _ -> "x" }, photo = null))
        assertEquals(listOf("Sebentar", "Maaf, Gemini tidak dapat menjawab sekarang"),
            run({ _, _ -> throw RuntimeException("Gemini HTTP 404: model not found") }))
        assertEquals(listOf("Sebentar", "Tiada tulisan dijumpai"), run({ _, _ -> "NONE" }, describe = false))
        assertEquals(listOf("Sebentar", "Papan tanda: DILARANG MASUK"), run({ _, _ -> "DILARANG MASUK" }, describe = false))
    }

    @Test fun keyScramblingRoundTrips() {
        // same XOR as android-app/sync_assets.py
        val mask = "LENSA-loopB-key-mask".toByteArray()
        val raw = """{"key": "AIza-test", "model": "gemini-test"}""".toByteArray()
        val scrambled = ByteArray(raw.size) { (raw[it].toInt() xor mask[it % mask.size].toInt()).toByte() }
        assertTrue("scrambled text must not contain the key", !String(scrambled, Charsets.ISO_8859_1).contains("AIza"))
        assertEquals("AIza-test" to "gemini-test", LoopBKey.decode(scrambled))
        assertNull(LoopBKey.decode(byteArrayOf(1, 2, 3)))
    }

    @Test fun speechQueueOrder() {
        val q = SpeechQueue()
        q.put("orang di kiri", null)                       // routine hazard
        q.put("Ada kedai di kiri.", null, isFree = true)   // Loop B answer
        q.put("Berhenti! kereta di hadapan", null, urgent = true)
        assertEquals(listOf("Berhenti! kereta di hadapan", "Ada kedai di kiri."), List(2) { q.next()!!.line })
        q.put("orang di kiri", null); q.put("jawapan", null, isFree = true)
        assertEquals("jawapan", q.next()!!.line)                    // a routine line can't push the answer out
        assertEquals("orang di kiri", q.next()!!.line)
        assertNull(q.next())
    }
}
