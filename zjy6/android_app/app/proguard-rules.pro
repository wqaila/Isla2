# 本项目的最小 ProGuard/R8 规则
# 说明：release 构建的 minifyEnabled 目前为 false，但 proguardFiles 引用的文件必须存在。

# 保留本应用自己的类（JNI 通过类名/方法名反射查找，混淆后会找不到）
-keep class com.example.qwenchat.** { *; }

# 保留所有声明了 native 方法的类的类名与 native 方法名
-keepclasseswithmembernames class * {
    native <methods>;
}

# 保留 JNI 注册时用到的 LlamaEngine（JNI_OnLoad 里按全限定名 FindClass）
-keep class com.example.qwenchat.LlamaEngine { *; }

# 保留 ViewBinding 生成的类
-keep class com.example.qwenchat.databinding.** { *; }
