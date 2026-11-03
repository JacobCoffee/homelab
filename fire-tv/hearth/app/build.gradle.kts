import com.android.build.api.variant.ApplicationAndroidComponentsExtension
import java.io.FileInputStream
import java.util.Properties
import org.gradle.api.file.DirectoryProperty

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
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
    compileSdk = 37

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
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
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
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")

    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")

    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.runtime:runtime")

    implementation("androidx.tv:tv-foundation:1.0.0")
    implementation("androidx.tv:tv-material:1.1.0")

    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")

    implementation("com.squareup.okhttp3:okhttp:5.5.1")

    testImplementation("junit:junit:4.13.2")
}

// Copies every theme folder under themes/ into assets/themes/<id>/, so the app can load
// them bundled. The build fails clearly if themes/default is missing or incomplete, since
// the theme contract requires a default theme to always exist.
val hearthThemesSrc = file("${project.rootDir}/themes")
val hearthThemesAssetsDir = layout.buildDirectory.dir("generated/hearthThemes/assets")

abstract class CopyHearthThemesTask : Copy() {
    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty
}

val copyHearthThemes = tasks.register<CopyHearthThemesTask>("copyHearthThemes") {
    val defaultThemeJson = File(hearthThemesSrc, "default/theme.json")
    doFirst {
        if (!defaultThemeJson.isFile) {
            throw GradleException(
                "themes/default/theme.json is missing at $defaultThemeJson. " +
                    "The default theme must exist and be bundled; see hearth-spec.md."
            )
        }
    }
    outputDirectory.set(hearthThemesAssetsDir.map { it.dir("themes") })
    from(hearthThemesSrc)
    into(outputDirectory)
    include("*/theme.json", "*/*.webp", "*/*.jpg", "*/*.jpeg", "*/*.png", "*/*.ttf", "*/*.otf")
}

val androidComponents = extensions.getByType<ApplicationAndroidComponentsExtension>()
androidComponents.onVariants { variant ->
    variant.sources.assets?.addGeneratedSourceDirectory(
        copyHearthThemes,
        CopyHearthThemesTask::outputDirectory
    )
}

tasks.named("preBuild") {
    dependsOn(copyHearthThemes)
}
