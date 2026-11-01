# Keep kotlinx.serialization generated serializers.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class dev.jacobcoffee.hearth.**$$serializer { *; }
-keepclassmembers class dev.jacobcoffee.hearth.** {
    *** Companion;
}
-keepclasseswithmembers class dev.jacobcoffee.hearth.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Accessibility service / receivers referenced only from the manifest.
-keep class dev.jacobcoffee.hearth.homekey.** { *; }
-keep class dev.jacobcoffee.hearth.config.ConfigProvider { *; }
