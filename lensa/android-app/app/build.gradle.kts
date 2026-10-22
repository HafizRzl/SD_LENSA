plugins {
    id("com.android.application")
}

android {
    namespace = "my.lensa"
    compileSdk = 36
    defaultConfig {
        applicationId = "my.lensa"
        minSdk = 26          // Android 8.0+
        targetSdk = 36
        versionCode = 4
        versionName = "0.4"
        ndk { abiFilters += listOf("arm64-v8a") }   // recent phones only — keeps the APK small
    }
    androidResources { noCompress += listOf("onnx", "wav") }   // read models/clips straight from the APK
    buildTypes {
        release { isMinifyEnabled = false }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("org.yaml:snakeyaml:2.7")
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.31.0")
    val camerax = "1.6.2"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")   // real org.json for JVM tests (Android's is a stub)
}
