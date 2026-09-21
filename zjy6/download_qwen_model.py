import os
from pathlib import Path

# 项目根目录（本脚本所在目录），避免写死绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent
# 模型根目录：与 README 描述保持一致，下载后会得到 models/qwen/Qwen-7B-Chat-Int4/
MODELS_ROOT = PROJECT_ROOT / "models"

# 将下载端点指向阿里云国内镜像站
os.environ['MODELSCOPE_ENDPOINT'] = 'https://www.modelscope.cn'
# 可选：增加超时时间，防止因网络波动导致下载失败 (单位：秒)
os.environ['MODELSCOPE_DOWNLOAD_TIMEOUT'] = '600'
# 可选：指定缓存目录，便于管理和查找已下载的模型文件
os.environ['MODELSCOPE_CACHE'] = str(MODELS_ROOT)

from modelscope import snapshot_download

model_dir = snapshot_download(
    'qwen/Qwen-7B-Chat-Int4',
    cache_dir=str(MODELS_ROOT)  # 这里的参数会覆盖环境变量的设置
)

print(f"✅ 模型已成功下载到: {model_dir}")
