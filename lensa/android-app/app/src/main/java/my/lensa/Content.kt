package my.lensa

import org.yaml.snakeyaml.Yaml

// Port of the content models in desktop-beta/lensa_rules.py. Reads the SAME yaml files
// (assets/context/**), copied into the APK by android-app/sync_assets.py.

@Suppress("UNCHECKED_CAST")
private fun Any?.map(): Map<String, Any?> = (this as? Map<String, Any?>) ?: emptyMap()
private fun Any?.num(d: Double) = (this as? Number)?.toDouble() ?: d
private fun Any?.str(d: String) = this?.toString() ?: d
private fun Any?.bool(d: Boolean) = (this as? Boolean) ?: d

class CatalogEntry(val en: String, val ms: String, val dangerWeight: Int, val ground: Boolean)

class ObjectCatalog(val byClass: Map<String, CatalogEntry>) {
    // The catalog is a WHITELIST: a class not listed weighs 0 — never announced, never blocks.
    fun dangerWeight(c: String) = byClass[c]?.dangerWeight ?: 0
    fun isGround(c: String) = byClass[c]?.ground ?: false
    operator fun contains(c: String) = c in byClass
    fun label(c: String, lang: String): String {
        val e = byClass[c] ?: return c.replace("_", " ")
        return if (lang == "en") e.en else e.ms
    }

    companion object {
        fun load(y: Map<String, Any?>): ObjectCatalog {
            val m = LinkedHashMap<String, CatalogEntry>()
            for (raw in (y["objects"] as? List<*>).orEmpty()) {
                val e = raw.map()
                val c = e["yolo_class"].str("").trim()
                if (c.isEmpty()) continue
                m[c] = CatalogEntry(e["en"].str(c), e["ms"].str(c),
                    e["danger_weight"].num(3.0).toInt(), e["ground"].bool(false))
            }
            return ObjectCatalog(m)
        }
    }
}

class DistanceBands(val stop: Double, val caution: Double, val notice: Double, val ignore: Double)

class HazardRules(
    val confidenceFloor: Double, val bands: DistanceBands, val sensitivity: String,
    val movingTowardMultiplier: Double, val groundAlertMinDanger: Int, val confirmFrames: Int,
) {
    companion object {
        fun load(y: Map<String, Any?>): HazardRules {
            val db = y["distance_bands"].map()
            return HazardRules(
                y["confidence_floor"].num(0.45),
                DistanceBands(db["immediate_stop_below_m"].num(1.0), db["caution_below_m"].num(3.0),
                    db["notice_below_m"].num(6.0), db["ignore_above_m"].num(6.0)),
                y["sensitivity"].str("medium"),
                y["moving_toward_multiplier"].num(1.5),
                y["ground_alert_min_danger"].num(9.0).toInt(),
                y["confirm_frames"].num(3.0).toInt(),
            )
        }
    }
}

class PhraseLibrary(
    val directions: Map<String, String>, val templates: Map<String, String>,
    val urgencyPrefix: Map<String, String>,
) {
    fun direction(pos: String): String {
        val key = when (pos) { "left" -> "left"; "right" -> "right"; else -> "ahead" }
        return directions[key] ?: key
    }

    fun render(key: String, obj: String? = null, direction: String? = null, distance: String? = null,
               text: String? = null, avoid: String? = null): String {
        var t = templates[key] ?: ""
        if (obj != null) t = t.replace("{objek}", obj).replace("{object}", obj)
        if (direction != null) t = t.replace("{arah}", direction).replace("{direction}", direction)
        if (distance != null) t = t.replace("{jarak}", distance).replace("{distance}", distance)
        if (text != null) t = t.replace("{teks}", text).replace("{text}", text)
        if (avoid != null) t = t.replace("{elak}", avoid).replace("{avoid}", avoid)
        return t.trim()
    }

    companion object {
        private fun sm(x: Any?) = x.map().map { (k, v) -> k to v.str("") }.toMap()
        fun load(y: Map<String, Any?>) =
            PhraseLibrary(sm(y["directions"]), sm(y["templates"]), sm(y["urgency_prefix"]))
    }
}

class AudioStyle(
    val repeatHighS: Double, val repeatNormalS: Double, val blockedMaxRepeats: Int,
    val speakWhenUnchanged: Boolean, val minGapS: Double,
) {
    companion object {
        fun load(y: Map<String, Any?>) = AudioStyle(
            y["repeat_high_urgency_every_s"].num(2.0), y["repeat_normal_every_s"].num(4.0),
            y["blocked_max_repeats"].num(3.0).toInt(), y["speak_when_scene_unchanged"].bool(false),
            y["min_gap_between_lines_s"].num(1.0))
    }
}

class Content(
    val objects: ObjectCatalog, val hazard: HazardRules,
    val phrasesMs: PhraseLibrary, val phrasesEn: PhraseLibrary, val audio: AudioStyle,
) {
    fun phrases(lang: String) = if (lang == "en") phrasesEn else phrasesMs

    companion object {
        /** read(path) returns a file's text, e.g. "rules/object_catalog.yaml". */
        fun load(read: (String) -> String): Content {
            fun y(p: String) = Yaml().load<Any?>(read(p)).map()
            return Content(
                ObjectCatalog.load(y("rules/object_catalog.yaml")),
                HazardRules.load(y("rules/hazard_priority_rules.yaml")),
                PhraseLibrary.load(y("language/phrase_library_ms.yaml")),
                PhraseLibrary.load(y("language/phrase_library_en.yaml")),
                AudioStyle.load(y("config/audio_output_style.yaml")),
            )
        }
    }
}
