# LENSA Phase 3 — ESP32 "Wireless Eye" + Wi-Fi Transport Implementation Plan

> **Status (2026-10-18): not started; written against the July desktop beta.** Since then the beta
> gained object tracking, box-extent steering with hysteresis, an all-blocked map fallback, a
> second (road-hazard) detector, two-clip voice joins and a catalog whitelist; the ToF sensor is
> now the **VL53L8CX** (same 8×8 grid). When building, port behaviour from `desktop-beta/`
> (`lensa_rules.py`, `lensa_beta.py`) — it is the source of truth; this plan is the scaffolding.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).
> **Prerequisite:** Phase 2 complete (real YOLO runs from the phone camera).

**Goal:** Replace the phone camera with the real head unit: an **ESP32-S3 SoftAP** streaming OV5640 frames (MJPEG) + a synchronized **VL53L5CX 8×8 depth grid**, and a `WifiTransport` on the phone that consumes both. Detections get a **real per-object distance** by mapping each box to its ToF zone.

**Architecture:** ESP32 = sensor-fusion streamer only (no inference). Phone joins its SoftAP, pulls MJPEG frames and polls the ToF grid, fuses box→zone→distance, and runs the same pipeline. See `docs/ARCHITECTURE.md` and `docs/HARDWARE.md`.

**Tech Stack:** ESP32 Arduino core (`esp_camera`, `esp_http_server`, `WiFi`), SparkFun VL53L5CX library; Android `HttpURLConnection` MJPEG client + `org.json`.

## Global Constraints

- Phone joins the ESP32 SoftAP; the ESP32 is at **`192.168.4.1`** (Android SoftAP default). Fully offline.
- Firmware is **board-specific** — the camera pin map and I²C pins are `⚠️ CALIBRATE` to your exact ESP32-S3 + OV5640 wiring.
- ToF grid is 8×8 = 64 zones in **millimetres**, row-major. Sensor orientation (which way is "left"/"up") is `⚠️ CALIBRATE`.

---

## Part A — ESP32-S3 firmware

**Files:** Create `esp32/lensa_wireless_eye/lensa_wireless_eye.ino` (in the repo, outside `android-app/`).

### Task A1: Firmware sketch

- [ ] **Step 1: Install** (Arduino IDE): ESP32 boards package, and the **SparkFun VL53L5CX Arduino Library** (Library Manager). Select your ESP32-S3 board, enable PSRAM.

- [ ] **Step 2: Write `lensa_wireless_eye.ino`**
```cpp
// LENSA "Wireless Eye" — OV5640 MJPEG + VL53L5CX 8x8 ToF over Wi-Fi SoftAP.
// The phone runs all AI; this node only captures + streams synchronized data.
#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"
#include <Wire.h>
#include <SparkFun_VL53L5CX_Library.h>

// ---- SoftAP ----  ⚠️ CALIBRATE credentials if you like
static const char* AP_SSID = "LENSA-EYE";
static const char* AP_PASS = "lensa1234";   // >= 8 chars

// ---- OV5640 camera pins ----  ⚠️ CALIBRATE to YOUR board (this is a common ESP32-S3 map)
#define PWDN_GPIO_NUM   -1
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM   15
#define SIOD_GPIO_NUM    4   // SDA (camera SCCB)
#define SIOC_GPIO_NUM    5   // SCL
#define Y9_GPIO_NUM     16
#define Y8_GPIO_NUM     17
#define Y7_GPIO_NUM     18
#define Y6_GPIO_NUM     12
#define Y5_GPIO_NUM     10
#define Y4_GPIO_NUM      8
#define Y3_GPIO_NUM      9
#define Y2_GPIO_NUM     11
#define VSYNC_GPIO_NUM   6
#define HREF_GPIO_NUM    7
#define PCLK_GPIO_NUM   13

// ---- VL53L5CX I2C pins ----  ⚠️ CALIBRATE (must not clash with camera SCCB)
#define TOF_SDA 40
#define TOF_SCL 41

SparkFun_VL53L5CX tof;
VL53L5CX_ResultsData tofData;
volatile int tofGrid[64];   // mm, row-major 8x8

static const char* STREAM_CT = "multipart/x-mixed-replace;boundary=frame";
static const char* BOUNDARY  = "\r\n--frame\r\n";
static const char* PART_HDR  = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

esp_err_t stream_handler(httpd_req_t* req) {
  httpd_resp_set_type(req, STREAM_CT);
  char part[64];
  while (true) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) continue;
    size_t hlen = snprintf(part, sizeof(part), PART_HDR, fb->len);
    if (httpd_resp_send_chunk(req, BOUNDARY, strlen(BOUNDARY)) != ESP_OK ||
        httpd_resp_send_chunk(req, part, hlen) != ESP_OK ||
        httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len) != ESP_OK) {
      esp_camera_fb_return(fb); break;
    }
    esp_camera_fb_return(fb);
  }
  return ESP_OK;
}

esp_err_t tof_handler(httpd_req_t* req) {
  // JSON: {"ts":<ms>,"grid":[64 ints in mm]}
  String out = "{\"ts\":" + String(millis()) + ",\"grid\":[";
  for (int i = 0; i < 64; i++) { out += String(tofGrid[i]); if (i < 63) out += ","; }
  out += "]}";
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, out.c_str(), out.length());
}

void startServer() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.max_uri_handlers = 4;
  httpd_handle_t server = NULL;
  if (httpd_start(&server, &cfg) == ESP_OK) {
    httpd_uri_t s = { "/stream", HTTP_GET, stream_handler, NULL };
    httpd_uri_t t = { "/tof",    HTTP_GET, tof_handler,    NULL };
    httpd_register_uri_handler(server, &s);
    httpd_register_uri_handler(server, &t);
  }
}

void setup() {
  Serial.begin(115200);

  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM; c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000; c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;   // 640x480 ⚠️ CALIBRATE (battery vs latency)
  c.jpeg_quality = 12; c.fb_count = 2; c.grab_mode = CAMERA_GRAB_LATEST;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  if (esp_camera_init(&c) != ESP_OK) { Serial.println("camera init failed"); return; }

  Wire.begin(TOF_SDA, TOF_SCL);
  if (tof.begin() == false) { Serial.println("VL53L5CX not found"); }
  tof.setResolution(64);      // 8x8
  tof.setRangingFrequency(15);
  tof.startRanging();

  WiFi.softAP(AP_SSID, AP_PASS);
  Serial.print("AP IP: "); Serial.println(WiFi.softAPIP());   // expect 192.168.4.1
  startServer();
}

void loop() {
  if (tof.isDataReady() && tof.getRangingData(&tofData)) {
    for (int i = 0; i < 64; i++) tofGrid[i] = tofData.distance_mm[i];
  }
  delay(5);
}
```

- [ ] **Step 3: Flash + smoke test.** Flash the ESP32. On a laptop, join Wi-Fi `LENSA-EYE`, open `http://192.168.4.1/stream` in a browser (should show live video) and `http://192.168.4.1/tof` (should return JSON with 64 numbers). ⚠️ If the camera fails to init, the pin map is wrong for your board — fix the `*_GPIO_NUM` defines.

- [ ] **Step 4: Commit**
```bash
git add esp32/
git commit -m "feat(firmware): ESP32-S3 wireless eye — OV5640 MJPEG + VL53L5CX 8x8 ToF over SoftAP"
```

---

## Part B — Phone: depth grid model + fusion

### Task B1: DepthGrid + SensorReading (tested fusion)

**Files:** Modify `model/Models.kt`; create test `pipeline/DepthFusionTest.kt`.

**Interfaces:**
- Produces: `DepthGrid(mm: IntArray).distanceMAt(cx, cy): Double?`; `SensorReading(distanceM, timestamp, grid: DepthGrid? = null)`.

- [ ] **Step 1: Write the failing test `DepthFusionTest.kt`**
```kotlin
package com.lensa.pipeline

import com.lensa.model.DepthGrid
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DepthFusionTest {
    @Test fun mapsCentreToZoneDistance() {
        val mm = IntArray(64) { 5000 }        // all far
        mm[3 * 8 + 4] = 1500                    // row 3, col 4 -> 1.5 m
        val g = DepthGrid(mm)
        // cx≈0.56 -> col 4, cy≈0.44 -> row 3
        assertEquals(1.5, g.distanceMAt(0.56f, 0.44f)!!, 0.001)
    }

    @Test fun invalidZoneIsNull() {
        val g = DepthGrid(IntArray(64) { 0 })   // 0 = no return
        assertNull(g.distanceMAt(0.5f, 0.5f))
    }
}
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Edit `Models.kt`** — add `DepthGrid` and extend `SensorReading`:
```kotlin
/** VL53L5CX 8x8 depth grid, millimetres, row-major (index = row*8 + col). */
class DepthGrid(val mm: IntArray) {
    /** Distance (m) at a normalized image point, or null if that zone has no valid return. */
    fun distanceMAt(cx: Float, cy: Float): Double? {
        val col = (cx * 8f).toInt().coerceIn(0, 7)
        val row = (cy * 8f).toInt().coerceIn(0, 7)
        val v = mm[row * 8 + col]
        return if (v in 1..8000) v / 1000.0 else null   // ⚠️ CALIBRATE max range
    }

    fun minValidM(): Double? = mm.filter { it in 1..8000 }.minOrNull()?.let { it / 1000.0 }
}

class SensorReading(val distanceM: Double, val timestamp: Long, val grid: DepthGrid? = null)
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Update `PipelineController.applyTof`** to fuse per-detection when a grid is present (replace the method body):
```kotlin
    private fun applyTof(dets: List<Detection>, r: SensorReading?): List<Detection> {
        if (r == null) return dets
        val grid = r.grid
        if (grid != null) {
            // Per-object fusion: each box takes the distance of its ToF zone.
            return dets.map { d ->
                val dist = grid.distanceMAt(d.boxCx, d.boxCy)
                if (dist != null) d.copy(distanceM = dist) else d
            }
        }
        // Fallback (single-distance mock, Phase 1): refine nearest dead-ahead box.
        var idx = -1; var best = Double.MAX_VALUE
        dets.forEachIndexed { i, d ->
            if (d.position == ObjectPosition.CENTER) {
                val dist = d.distanceM ?: Double.MAX_VALUE
                if (dist < best) { best = dist; idx = i }
            }
        }
        if (idx < 0) return dets
        return dets.toMutableList().also { it[idx] = it[idx].copy(distanceM = r.distanceM) }
    }
```

- [ ] **Step 6: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/model/Models.kt android-app/app/src/main/java/com/lensa/pipeline/PipelineController.kt android-app/app/src/test/java/com/lensa/pipeline/DepthFusionTest.kt
git commit -m "feat: 8x8 depth grid + per-object ToF fusion (tested)"
```

---

### Task B2: WifiTransport (MJPEG client + ToF poller)

**Files:** Create `transport/WifiTransport.kt`.

**Interfaces:**
- Consumes: `HeadUnitTransport`, `DepthGrid`, `SensorReading`.
- Produces: `WifiTransport(baseUrl = "http://192.168.4.1", scope)`.

- [ ] **Step 1: Write `WifiTransport.kt`**
```kotlin
package com.lensa.transport

import android.graphics.BitmapFactory
import com.lensa.model.CameraFrame
import com.lensa.model.DepthGrid
import com.lensa.model.HeadUnitConnectionState
import com.lensa.model.SensorReading
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.io.BufferedInputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * Consumes the ESP32 SoftAP: MJPEG frames from /stream, 8x8 ToF grid polled from /tof.
 * The phone must already be joined to the "LENSA-EYE" Wi-Fi.
 */
class WifiTransport(
    private val baseUrl: String = "http://192.168.4.1",
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.IO + SupervisorJob()),
) : HeadUnitTransport {

    private val _frames = MutableSharedFlow<CameraFrame>(extraBufferCapacity = 2, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    private val _sensor = MutableSharedFlow<SensorReading>(extraBufferCapacity = 2, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    private val _conn = MutableStateFlow(HeadUnitConnectionState.DISCONNECTED)

    override val frames: Flow<CameraFrame> = _frames
    override val sensor: Flow<SensorReading> = _sensor
    override val connection: Flow<HeadUnitConnectionState> = _conn

    private var frameJob: Job? = null
    private var tofJob: Job? = null

    override suspend fun connect() {
        _conn.value = HeadUnitConnectionState.CONNECTING
        frameJob = scope.launch { streamFrames() }
        tofJob = scope.launch { pollTof() }
    }

    private suspend fun streamFrames() {
        try {
            val conn = (URL("$baseUrl/stream").openConnection() as HttpURLConnection).apply {
                connectTimeout = 4000; readTimeout = 8000
            }
            val input = BufferedInputStream(conn.inputStream, 1 shl 16)
            _conn.value = HeadUnitConnectionState.CONNECTED
            while (currentCoroutineContext().isActive) {
                val jpeg = readNextJpeg(input) ?: break
                val bmp = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size) ?: continue
                _frames.tryEmit(CameraFrame(jpeg, System.currentTimeMillis(), bmp))
            }
        } catch (e: Exception) {
            _conn.value = HeadUnitConnectionState.DISCONNECTED
        }
    }

    /** Reads one JPEG out of a multipart/x-mixed-replace MJPEG stream. */
    private fun readNextJpeg(input: BufferedInputStream): ByteArray? {
        // Find SOI (FF D8)
        var prev = -1; var b: Int
        while (true) {
            b = input.read(); if (b == -1) return null
            if (prev == 0xFF && b == 0xD8) break
            prev = b
        }
        val out = ArrayList<Byte>(1 shl 15)
        out.add(0xFF.toByte()); out.add(0xD8.toByte())
        prev = -1
        while (true) {
            b = input.read(); if (b == -1) return null
            out.add(b.toByte())
            if (prev == 0xFF && b == 0xD9) break   // EOI
            prev = b
        }
        return out.toByteArray()
    }

    private suspend fun pollTof() {
        while (currentCoroutineContext().isActive) {
            try {
                val text = URL("$baseUrl/tof").readText()
                val arr = JSONObject(text).getJSONArray("grid")
                val mm = IntArray(64) { arr.getInt(it) }
                val grid = DepthGrid(mm)
                _sensor.tryEmit(SensorReading(grid.minValidM() ?: -1.0, System.currentTimeMillis(), grid))
            } catch (_: Exception) { /* transient; keep polling */ }
            delay(66)   // ~15 Hz, matches ToF ranging frequency
        }
    }

    override suspend fun disconnect() {
        frameJob?.cancel(); tofJob?.cancel()
        _conn.value = HeadUnitConnectionState.DISCONNECTED
    }

    override fun dispose() { scope.cancel() }
}
```

- [ ] **Step 2: Build to verify** → BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/transport/WifiTransport.kt
git commit -m "feat: WifiTransport — MJPEG frames + 8x8 ToF from ESP32 SoftAP"
```

---

### Task B3: Wire + field test

**Files:** Modify `di/AppGraph.kt`, `di/AppConfig.kt`.

- [ ] **Step 1: `AppGraph.kt`** — select `WifiTransport` when `!useMockTransport && !useCameraX`. Simplest: add a `useWifiTransport` flag and branch:
```kotlin
    private val transport = when {
        AppConfig.useWifiTransport -> com.lensa.transport.WifiTransport()
        AppConfig.useMockTransport -> MockTransport()
        else -> com.lensa.camera.CameraXTransport(context, lifecycleOwner, previewView)
    }
```

- [ ] **Step 2: `AppConfig.kt`** — add and set:
```kotlin
    var useWifiTransport = true    // Phase 3: real head unit over SoftAP
```
(Detector stays `RealObjectDetector`; the phone still runs YOLO on the streamed frames.)

- [ ] **Step 3: Field test.** Power the ESP32. On the phone: join Wi-Fi **LENSA-EYE**, open LENSA, tap **Start**. Expected: guidance now includes **real distances** ("orang di hadapan, 2 meter"; "Berhenti! …" inside 1 m) because box→ToF-zone fusion is live. Walk toward an obstacle and confirm the urgency escalates.

⚠️ CALIBRATE: if left/right or near/far are mirrored, flip the row/col mapping in `DepthGrid.distanceMAt` (sensor mounting orientation).

- [ ] **Step 4: Commit**
```bash
git add android-app/app/src/main/java/com/lensa/di
git commit -m "feat: switch to WifiTransport (ESP32 head unit) behind flag (phase 3)"
```

---

## Phase 3 done — what it proves

The real wearable feeds LENSA: ESP32 streams synchronized frames + 8×8 depth, the phone fuses per-object distance and speaks correct, distance-aware Bahasa Melayu guidance. Calibration surface: firmware pin map + ToF orientation, both isolated and marked.
