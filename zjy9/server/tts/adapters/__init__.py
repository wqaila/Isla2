"""各 TTS 引擎的适配器实现。

新增引擎时：

1. 在本目录下建一个模块，继承 :class:`tts.base.TTSEngine`
2. 实现 ``is_available()`` 与 ``synthesize()``
3. 在 :func:`tts.controller._discover_engines` 里登记
4. 需要的话把它加进 ``DEFAULT_PRIORITY``

Adapter 内部要**延迟导入**第三方包，这样没装该引擎时包仍能正常加载
（``is_available()`` 返回 False 即可），而不是 import 就炸。
"""
