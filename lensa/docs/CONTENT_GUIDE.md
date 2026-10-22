# Content Guide — how to edit LENSA safely

Everything in `assets/context/` changes LENSA's behaviour **without recompiling logic**.
This guide is the contract for editing it.

## Two kinds of file
1. **Prompt files** (`agents/*.md`, prose) — instructions for an AI model. The live ones are
   `loopb_*.md` (Gemini, Loop B); the `layer*.md` set is older reference material.
   You may freely reword them, but **keep any `## OUTPUT FORMAT` section intact**, because the
   app parses the model's output against it.
2. **Config files** (`rules/`, `language/`, `config/` — plain **`.yaml`**) — parsed directly.
   Keep them valid YAML (consistent indentation, no tabs). `#` comments are for humans.
   The **desktop beta hot-reloads these while running** (~2 s after you save).

## File map
| File | Type | What it controls |
|------|------|------------------|
| `agents/loopb_describe_scene.md`       | prompt | **Live.** Loop B: describe the camera frame (Gemini) |
| `agents/loopb_read_sign.md`            | prompt | **Live.** Loop B: read signs; filled from `ocr_reading_rules.yaml` |
| `agents/agent_system_master.md`        | prompt | Shared persona + non-negotiable safety rules |
| `agents/layer1_scene_interpretation.md`| prompt | Turn raw detections + OCR into a clean scene |
| `agents/layer2_hazard_decision.md`     | prompt | Pick the one thing to say + the action |
| `agents/layer3_instruction_language.md`| prompt | Write the final short spoken line |
| `rules/object_catalog.yaml`            | yaml   | The whitelist of announced objects: EN/BM labels, danger weight, `ground` |
| `rules/hazard_priority_rules.yaml`     | yaml   | Danger scoring, distance bands, sensitivity |
| `rules/ocr_reading_rules.yaml`         | yaml   | When/what OCR reads aloud |
| `language/phrase_library_ms.yaml`      | yaml   | Bahasa Melayu spoken templates |
| `language/phrase_library_en.yaml`      | yaml   | English spoken templates |
| `config/audio_output_style.yaml`       | yaml   | Voice, pace, interruption/repeat frequency |
| `config/safety_fallback.yaml`          | yaml   | What to say when things fail (safety-critical) |
| `config/user_settings_defaults.yaml`   | yaml   | Default in-app settings |

## Recipes

### Add a new detectable object
1. Add a row to the `objects:` list in `rules/object_catalog.yaml` (set `yolo_class`, `en`,
   `ms`, `danger_weight`; add `ground: true` if it lies flat on the ground, like a pothole).
2. Make sure a detector actually outputs that `yolo_class`
   (`python desktop-beta/list_classes.py` shows what both models can see). If none does, it
   needs a labelled dataset and a training run like `desktop-beta/train_hazards.py`.
3. Render its voice clips: `python desktop-beta/render_clips.py --prewarm`.

### Stop LENSA announcing an object
Delete its row from `rules/object_catalog.yaml`. The catalog is a whitelist: the detector may
still see it (drawn as a grey box), but it is never spoken and never blocks a path.

### Make LENSA more/less cautious
Edit `rules/hazard_priority_rules.yaml` → `sensitivity` and `distance_bands`. Larger bands =
warns earlier / more often.

### Change how something is said
Edit the template in `language/phrase_library_ms.yaml` (and the English mirror). Placeholders in
`{curly_braces}` are filled by the app — keep the placeholder names unchanged. If you reword a Malay template, also reword the frame
words in `decision_segments()` (`desktop-beta/lensa_rules.py`) and re-run `--prewarm`.

### Fix how a word is pronounced
Add a respelling under `pronounce:` in `language/phrase_library_ms.yaml` (affects the voice
only, not the on-screen text), then `python desktop-beta/render_clips.py --prewarm --force`.

### Change how often LENSA repeats itself
`config/audio_output_style.yaml`: `repeat_normal_every_s`, `repeat_high_urgency_every_s`,
`speak_when_scene_unchanged`, `blocked_max_repeats`.

### Change the default language
Edit `config/user_settings_defaults.yaml` → `language: ms` (or `en`) for the phone app; the
desktop beta uses `--lang ms|en`.

## After any change
- Re-run the app (content is read at startup / on reload).
- Describe behaviour changes in the git commit message so they stay traceable.
- If you touched a `## OUTPUT FORMAT` block or a placeholder name, do a quick walk test.
