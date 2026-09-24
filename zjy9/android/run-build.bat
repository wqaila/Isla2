@echo off
REM ============================================================
REM  Build Debug APK for the Elysia Android client
REM
REM  NOTE: the original version of this script hard-coded paths
REM  from another machine (C:\Users\431\... and D:\zjy9\android),
REM  so it could never run here. Rewritten to use the project's
REM  own gradlew and resolve paths relative to this file.
REM
REM  This file is kept ASCII-only on purpose: batch files with
REM  non-ASCII content get garbled under the default GBK codepage.
REM  See README.md (Chinese) for the full explanation.
REM ============================================================

setlocal
cd /d "%~dp0"

REM --- JDK 17 is REQUIRED -------------------------------------
REM Gradle 8.5 supports Java up to 21. Android Studio's bundled
REM JBR on this machine is JDK 25, which fails immediately.
if "%JAVA_HOME%"=="" (
    set "JAVA_HOME=C:\Users\user\.workbuddy-ai\binaries\jdk\jdk17.0.20_10"
)

REM --- Android SDK --------------------------------------------
REM Can also be set in local.properties (sdk.dir=...).
if "%ANDROID_HOME%"=="" (
    set "ANDROID_HOME=D:\Android\Sdk"
)

echo JAVA_HOME    = %JAVA_HOME%
echo ANDROID_HOME = %ANDROID_HOME%
echo.

if not exist "%JAVA_HOME%\bin\java.exe" (
    echo [ERROR] java.exe not found under JAVA_HOME.
    echo         Install JDK 17 or set JAVA_HOME manually.
    exit /b 1
)

call gradlew.bat assembleDebug --stacktrace
if errorlevel 1 (
    echo.
    echo [FAILED] Build failed. Common causes:
    echo   1. Proxy is in "rule/split" mode - dl.google.com gets
    echo      blocked with a 502. Switch the VPN to GLOBAL mode.
    echo   2. JAVA_HOME is not JDK 17.
    echo   3. Android SDK platform 34 / build-tools 34 missing.
    exit /b 1
)

echo.
echo [OK] APK: app\build\outputs\apk\debug\app-debug.apk
endlocal
