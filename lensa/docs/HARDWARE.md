# Hardware — Head Unit

Bill of materials for the wearable head unit. Edit quantities/part numbers as the build evolves.

| Component            | Part                     | Role |
|----------------------|--------------------------|------|
| Microcontroller      | ESP32-S3 **N16R8**       | Capture node + Wi-Fi streaming. 16 MB flash / 8 MB PSRAM handles camera + buffering. |
| Camera               | **OV5640**               | Scene capture. |
| Distance sensor      | **VL53L8CX** breakout (I²C) | Infrared Time-of-Flight; **8×8 zone (64-point) depth grid** for per-object distance. Replaced the lost VL53L5CX (same grid, same 4 wires). |
| Speaker (optional)   | **8 Ohm 0.5W mini small round speaker**          | Audio to user (can also route through the phone). |
| Power                | **LiPo battery - 3.7V (1000mAh ~ 39mm length, 19mm wide, 9mm height)**         | Powers the head unit. |
| Mount                | **3D-printed headwear**  | Lightweight mount for all components. |

## Role — "Wireless Eye" (not a compute node)
The ESP32-S3 does **sensor fusion + streaming only**; all AI runs on the phone. Its 8 MB octal
PSRAM buffers a JPEG frame and all 64 ToF zones, **time-synced** into one timestamped payload,
and it may **crop/resize/compress** the frame before transmit to save bandwidth.

## Connectivity
- ESP32-S3 runs as a **Wi-Fi SoftAP**; the phone joins it (self-contained, offline, no router).
- Camera (OV5640, DVP) → ESP32-S3 → **MJPEG over Wi-Fi** to the phone.
- ToF (VL53L8CX, I²C) → ESP32-S3 → **8×8 depth grid** sent alongside frames (shared timestamp).
- **BLE is not used** — it cannot carry a camera stream. Left as a future option only.

## Notes
- Keep the ToF aligned to the camera's forward axis so grid zones map to image regions correctly.
- Frame rate / resolution is a battery vs. latency trade-off; tune on the firmware side.
- If you change the streaming format or payload, update the **data contract** in `ARCHITECTURE.md`.
