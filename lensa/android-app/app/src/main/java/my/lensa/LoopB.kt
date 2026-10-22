package my.lensa

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Base64

// Loop B — the on-demand, ONLINE loop: describe the scene / read a sign with Gemini vision.
// Port of desktop-beta/loop_b.py (prompts + sign lines are checked against the Python by
// GoldenParityTest). Loop A (hazards) stays offline and is never blocked by this.

private val LANG_NAME = mapOf("ms" to "Bahasa Melayu", "en" to "English")

object LoopBText {
    fun describePrompt(template: String, lang: String) =
        template.replace("{language}", LANG_NAME[lang] ?: lang)

    fun readPrompt(template: String, lang: String, rules: Map<String, Any?>): String {
        @Suppress("UNCHECKED_CAST")
        val prioritise = (rules["prioritise"] as? List<Any?>)?.map { it.toString() } ?: listOf("warning", "signboard")
        val translate = rules["translate_to_user_language"] as? Boolean ?: false
        val ignoreClutter = rules["ignore_clutter"] as? Boolean ?: true
        return template
            .replace("{prioritise}", prioritise.joinToString(", "))
            .replace("{clutter_rule}", if (ignoreClutter) "Skip advertising and dense unrelated text." else "Read all text you can see.")
            .replace("{translate_rule}", if (translate) "Translate the text into ${LANG_NAME[lang] ?: lang}."
                else "Keep the text in its original language.")
            .replace("{max_chars}", maxChars(rules).toString())
    }

    private fun maxChars(rules: Map<String, Any?>) = (rules["max_chars"] as? Number)?.toInt() ?: 80

    /** Model output -> the spoken line (OCR template, max_chars, 'no text' message). */
    fun signLine(raw: String?, rules: Map<String, Any?>, lib: PhraseLibrary): String {
        var text = (raw ?: "").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
        if (text.isEmpty() || text.uppercase().trim(' ', '.') == "NONE") return lib.render("ocr_none")
        val limit = maxChars(rules)
        if (text.length > limit) text = text.take(limit).substringBeforeLast(" ")
        return lib.render("ocr", text = text)
    }
}

/** The one place that talks to Google. Swap this class (e.g. for a small server that holds the
 *  key, so the key never ships in the APK) and nothing else changes. */
class GeminiClient(private val key: String, model: String) {
    private val model = model.removePrefix("models/")

    fun ask(prompt: String, jpeg: ByteArray): String {
        val url = URL("https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent")
        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = TIMEOUT_MS; conn.readTimeout = TIMEOUT_MS
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("x-goog-api-key", key)
            conn.outputStream.use { it.write(requestJson(prompt, Base64.getEncoder().encodeToString(jpeg)).toByteArray()) }
            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText().orEmpty()
            if (code !in 200..299) throw RuntimeException("Gemini HTTP $code: ${body.take(200)}")
            return parseText(body)
        } finally { conn.disconnect() }
    }

    companion object {
        const val TIMEOUT_MS = 20_000

        fun requestJson(prompt: String, jpegB64: String): String = JSONObject()
            .put("system_instruction", JSONObject().put("parts", JSONArray().put(JSONObject().put("text", prompt))))
            .put("contents", JSONArray().put(JSONObject().put("parts", JSONArray()
                .put(JSONObject().put("inline_data", JSONObject().put("mime_type", "image/jpeg").put("data", jpegB64)))
                .put(JSONObject().put("text", "Respond now, following the instructions.")))))
            .toString()

        /** All text parts of the first candidate (skipping any "thought" parts). */
        fun parseText(json: String): String {
            val parts = JSONObject(json).optJSONArray("candidates")?.optJSONObject(0)
                ?.optJSONObject("content")?.optJSONArray("parts") ?: return ""
            return (0 until parts.length()).map { parts.getJSONObject(it) }
                .filter { !it.optBoolean("thought", false) }
                .joinToString("") { it.optString("text", "") }.trim()
        }
    }
}

/** One request at a time, in the background. say(text, isWaitClip) speaks: "Sebentar" first (a
 *  cached Husein clip), then the answer — or a spoken failure (no key / no internet / error). */
class LoopB(
    private val lang: String,
    @Volatile var lib: PhraseLibrary,
    private val readFile: (String) -> String,                 // e.g. "agents/loopb_describe_scene.md"
    private val ask: ((String, ByteArray) -> String)?,        // null = no API key in this build
    private val say: (String, Boolean) -> Unit,
) {
    @Volatile var busy = false; private set
    @Volatile var last = ""; private set
    @Volatile var lastError = ""; private set

    /** [photo] is called on the background thread and returns a JPEG (or null on failure). */
    fun describe(photo: () -> ByteArray?) = start(photo) { a, jpg ->
        a(LoopBText.describePrompt(readFile("agents/loopb_describe_scene.md"), lang), jpg)
    }

    fun readSign(photo: () -> ByteArray?) = start(photo) { a, jpg ->
        val rules = org.yaml.snakeyaml.Yaml().load<Map<String, Any?>>(readFile("rules/ocr_reading_rules.yaml")) ?: emptyMap()
        LoopBText.signLine(a(LoopBText.readPrompt(readFile("agents/loopb_read_sign.md"), lang, rules), jpg), rules, lib)
    }

    private fun start(photo: () -> ByteArray?, job: ((String, ByteArray) -> String, ByteArray) -> String) {
        if (busy) return                                       // ignore repeated presses
        val a = ask ?: return finish(lib.render("loopb_no_key"))
        busy = true
        say(lib.render("loopb_wait"), true)
        Thread {
            val text = try {
                val jpg = photo() ?: throw RuntimeException("no camera photo")
                job(a, jpg)
            } catch (e: Exception) {
                lastError = "${e.javaClass.simpleName}: ${e.message}"
                // no connection (IOException) vs Gemini/camera answering with an error — saying
                // "no internet" for a 404 sent the user looking for the wrong problem
                lib.render(if (e is java.io.IOException) "loopb_offline" else "loopb_error")
            }
            busy = false
            finish(text)
        }.apply { isDaemon = true; start() }
    }

    private fun finish(text: String) { last = text; say(text, false) }
}

/** The API key + model, baked into the APK by sync_assets.py from desktop-beta/secrets.yaml.
 *  ponytail: XOR-scrambled so a plain text search of the APK doesn't find it — NOT real security;
 *  anyone determined can recover it. Real fix: a small server that holds the key. */
object LoopBKey {
    private val MASK = "LENSA-loopB-key-mask".toByteArray()

    fun decode(bytes: ByteArray): Pair<String, String>? = runCatching {
        val json = JSONObject(String(ByteArray(bytes.size) { (bytes[it].toInt() xor MASK[it % MASK.size].toInt()).toByte() }))
        val key = json.getString("key"); val model = json.getString("model")
        if (key.isBlank() || model.isBlank()) null else key to model
    }.getOrNull()
}
