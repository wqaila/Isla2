cmake_minimum_required(VERSION 3.14)

project("qwenchat")

# 设置 C++ 标准
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 11)

# 设置 llama.cpp 构建选项 - 使用静态库
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)

# 禁用 AVX 等高级指令集以兼容更多设备
set(LLAMA_NATIVE OFF CACHE BOOL "" FORCE)

# 后端开关默认全部关闭。
# Vulkan / BLAS / NNAPI 都需要额外的第三方依赖，若强行开启而依赖不存在会直接导致构建失败。
# 如需启用，请自行安装对应依赖后把下面的 OFF 改成 ON（或在 Gradle 的
# externalNativeBuild.cmake.arguments 中传入 -DLLAMA_VULKAN=ON 等参数）。
set(LLAMA_VULKAN OFF CACHE BOOL "" FORCE)

set(LLAMA_BLAS OFF CACHE BOOL "" FORCE)
# 注意：这里必须是 "变量名 值 CACHE 类型 描述" 的形式，不能写成 LLAMA_BLAS_VENDOR=OpenBLAS
set(LLAMA_BLAS_VENDOR OpenBLAS CACHE STRING "BLAS vendor (仅在 LLAMA_BLAS=ON 时生效)")

set(LLAMA_NNAPI OFF CACHE BOOL "" FORCE)

# 添加 llama.cpp 子目录
add_subdirectory(${CMAKE_SOURCE_DIR}/llama.cpp ${CMAKE_BINARY_DIR}/llama)

# 创建 JNI 库 - 直接编译所有源码到单个 SO 文件
add_library(llama-helper SHARED
    src/main/cpp/llama-helper.cpp
)

# 包含目录
target_include_directories(llama-helper PRIVATE
    ${CMAKE_SOURCE_DIR}/llama.cpp/include
)

# 链接 llama 静态库和其他依赖
target_link_libraries(llama-helper PRIVATE
    llama
    android
    log
    m
    dl
)

# 设置编译选项
target_compile_options(llama-helper PRIVATE
    -O2
    -ffast-math
)