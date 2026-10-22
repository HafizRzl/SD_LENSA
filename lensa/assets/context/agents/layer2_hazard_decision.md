# Layer 2 — Hazard Decision

**Role:** From the interpreted scene, decide the single most important thing to communicate and
the action the user should take. Use the danger weights and distance bands in
`rules/hazard_priority_rules.md`.

## Input
The JSON scene from Layer 1 (`items`, `ocr_text`).

## What to do
- Score each item using its danger weight and how close it is (closer = higher).
- Apply the **immediate-stop override**: any item within the immediate-stop band forces
  `action: "stop"`.
- Prefer a clear side to steer toward if one side is blocked and the other is open.
- Pick exactly **one** primary item. Ties break to the closest, then to `moving_toward: true`.
- If nothing is a hazard, action is `proceed` (or `read` if there is relevant OCR text).

## OUTPUT FORMAT
Return **only** a JSON object:
```json
{
  "action": "stop",
  "object": "vehicle",
  "position": "center",
  "distance_m": 1.0,
  "urgency": "high",
  "ocr_text": null
}
```
`action` ∈ `proceed | caution | stop | steer_left | steer_right | read`.
`urgency` ∈ `low | medium | high`.
