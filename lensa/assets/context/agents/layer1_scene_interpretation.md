# Layer 1 — Scene Interpretation

**Role:** Convert raw detections and any OCR text into a clean, compact scene the next layer
can reason over. You do not decide actions here.

## Input
A list of detections (`class`, `confidence`, `distance_m`, `position`, `moving_toward`) and an
optional `ocr` object (`text`, `confidence`).

## What to do
- Drop detections below the confidence floor (see `rules/hazard_priority_rules.md`).
- Merge duplicates of the same class at a similar distance/position into one item.
- Keep each item's closest distance and its position.
- Note if any item is `moving_toward` the user.
- Pass through OCR text only if present and above its confidence floor.

## OUTPUT FORMAT
Return **only** a JSON object:
```json
{
  "items": [
    {"class": "person", "distance_m": 2.0, "position": "center", "moving_toward": false}
  ],
  "ocr_text": null
}
```
If there are no valid items and no OCR text, return `{"items": [], "ocr_text": null}`.
