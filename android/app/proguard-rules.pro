# === OkHttp / WebSocket ===
-dontwarn okhttp3.**
-dontwarn okio.**
-keep class okhttp3.** { *; }
-keep class okio.** { *; }

# === Gson 序列化保护 ===
-keepattributes Signature
-keepattributes *Annotation*
-keep class com.google.gson.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken

# === 所有数据模型类（防止字段名被混淆） ===
-keep class com.elysia.ai.data.** { *; }

# === Compose Runtime ===
-keep class androidx.compose.** { *; }
-dontwarn androidx.compose.**

# === Kotlin Coroutines ===
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
-keep class kotlinx.coroutines.** { *; }

# === Room ===
-keep class * extends androidx.room.RoomDatabase { *; }
-dontwarn androidx.room.paging.**
