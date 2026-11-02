import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// Signing config is loaded from a keystore outside the repo. tools/make-keystore.sh creates it.
val keystoreInfoFile = file(System.getProperty("user.home") + "/.hearth-keys/KEY-INFO.txt")
val hasReleaseSigning = keystoreInfoFile.exists()
val keyProps = Properties()
if (hasReleaseSigning) {
    FileInputStream(keystoreInfoFile).use { keyProps.load(it) }
}

android {
    namespace = "dev.jacobcoffee.hearth"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.jacobcoffee.hearth"
        minSdk = 25
        targetSdk = 28
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(keyProps.getProperty("storeFile"))
                storePassword = keyProps.getProperty("storePassword")
                keyAlias = keyProps.getProperty("keyAlias")
                keyPassword = keyProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = false
            isReturnDefaultValues = true
        }
    }

    lint {
        // Hearth is sideloaded onto a Fire TV Stick, never published to Google Play; the
        // targetSdk is pinned to 28 to match Fire OS 8's expected runtime behavior.
        disable += "ExpiredTargetSdkVersion"
        checkReleaseBuilds = true
        abortOnError = true
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.3")

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")

    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.runtime:runtime")

    implementation("androidx.tv:tv-foundation:1.0.0-alpha12")
    implementation("androidx.tv:tv-material:1.0.1")

    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
}

// Copies every theme folder under themes/ into assets/themes/<id>/, so the app can load
// them bundled. The build fails clearly if themes/default is missing or incomplete, since
// the theme contract requires a default theme to always exist.
val hearthThemesSrc = file("${project.rootDir}/themes")
val hearthThemesAssetsDir = layout.buildDirectory.dir("generated/hearthThemes/assets")

val copyHearthThemes = tasks.register<Copy>("copyHearthThemes") {
    val defaultThemeJson = File(hearthThemesSrc, "default/theme.json")
    doFirst {
        if (!defaultThemeJson.isFile) {
            throw GradleException(
                "themes/default/theme.json is missing at $defaultThemeJson. " +
                    "The default theme must exist and be bundled; see hearth-spec.md."
            )
        }
    }
    from(hearthThemesSrc)
    into(hearthThemesAssetsDir.map { it.dir("themes") })
    include("*/theme.json", "*/*.webp", "*/*.jpg", "*/*.jpeg", "*/*.png", "*/*.ttf", "*/*.otf")
}

android.sourceSets.getByName("main").assets.srcDir(hearthThemesAssetsDir)

tasks.named("preBuild") {
    dependsOn(copyHearthThemes)
}
