"""临时启动器：修正 DSH 会话注入的畸形 no_proxy 后启动服务。

背景：本会话环境里存在重复的大小写变体环境变量
  no_proxy / NO_PROXY = "10.1.41.110,.local,localhost,127.0.0.1,::1,[::1]"
其中 "[::1]" 会被 httpx 当成 URL 解析并抛 InvalidURL: Invalid port: ':1]'，
导致 main.py 在 import cloud_client 阶段就崩溃。

在 Python 进程内改写 os.environ 可以可靠生效（Windows 环境块的重复条目
无法通过 PowerShell 的 $env: 覆盖）。仅影响本次启动的进程，不写入任何配置。
"""
import os
import runpy
import sys

SERVER = r'D:\xiangmudaima\zjy9\server'
CLEAN_NO_PROXY = '10.1.41.110,.local,localhost,127.0.0.1,::1'

os.chdir(SERVER)
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

os.environ['NO_PROXY'] = CLEAN_NO_PROXY
os.environ['no_proxy'] = CLEAN_NO_PROXY

runpy.run_path(os.path.join(SERVER, 'main.py'), run_name='__main__')
