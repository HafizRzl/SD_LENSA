package my.lensa

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.yaml.snakeyaml.Yaml
import java.io.File

/** Real call to Gemini through the PHONE's REST client. Skipped unless app/live-test.flag exists
 *  (its two lines = a scene photo and a sign photo), so normal builds never touch the network.
 *  Key + model come from desktop-beta/secrets.yaml. */
class LiveGeminiTest {
    @Test fun describeAndReadForReal() {
        val flag = File("live-test.flag")
        assumeTrue("live test not requested", flag.exists())
        val (scene, sign) = flag.readLines().filter { it.isNotBlank() }.map { File(it.trim()).readBytes() }
        val s = Yaml().load<Map<String, Any?>>(File("../../desktop-beta/secrets.yaml").readText())
        val model = s["gemini_model"].toString().substringBefore(" ")
        println("LIVE model: $model")
        val client = GeminiClient(s["gemini_api_key"].toString(), model)
        val ctx = File("../../assets/context")
        val rules = Yaml().load<Map<String, Any?>>(File(ctx, "rules/ocr_reading_rules.yaml").readText())
        val describe = client.ask(LoopBText.describePrompt(File(ctx, "agents/loopb_describe_scene.md").readText(), "ms"), scene)
        val read = client.ask(LoopBText.readPrompt(File(ctx, "agents/loopb_read_sign.md").readText(), "ms", rules), sign)
        println("LIVE describe: $describe")
        println("LIVE read: $read")
        assertTrue(describe.isNotBlank())
        assertTrue(read.uppercase().contains("DILARANG"))
    }
}
