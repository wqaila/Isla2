cmake_minimum_required(VERSION 3.16)

# ESP32-S3 智能角色语音交互系统 —— 固件工程
#
# 架构：本地 ASR + 云端 LLM + 本地 TTS（推理都在电脑侧）
# 板子只负责：录音上传 / 播放下行音频 / 显示文字 / 按键交互
#
# 目标板：Waveshare ESP32-S3-Touch-LCD-1.85B
#   ESP32-S3R8（8MB PSRAM + 16MB Flash）
#   音频：ES7210（4 通道 ADC，录音）/ ES8311（DAC，播放）

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(zjy13_voice)
