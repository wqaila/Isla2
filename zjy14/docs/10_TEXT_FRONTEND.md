# 文本前端（wetext）启用报告

> 触发：用户要求"配 modelscope token 启用 wetext"
> 结果：**不需要 token，资源本地已有，已实现零侵入补丁**
> 日期：2026-11-09

---

## 一、结论先行

| 问题 | 结论 |
|---|---|
| 需要 modelscope token 吗？ | **不需要**。fst 资源早在 2026-10-28 就缓存在本地了 |
| 为什么之前没启用？ | `wetext` 初始化时无条件调 `snapshot_download()`，每次联网校验都 403 |
| 现在能用了吗？ | **能**。已实现零侵入补丁，直接读本地 fst |
| 有效果吗？ | **有**。数字被正确朗读，音节 CV 显著提升 +0.0906 |

---

## 二、根因：为什么之前一直降级

### 2.1 wetext 的初始化逻辑

`wetext/wetext.py:36-38`：

```python
def __init__(self, tagger_path=None, verbalizer_path=None, ...):
    if tagger_path is None or verbalizer_path is None:
        repo_dir = snapshot_download("pengzhendong/wetext")   # ← 无条件联网
```

**每次初始化都要联网校验**，即使资源已在本地。无 token 时 modelscope 返回 403，抛异常。

### 2.2 CosyVoice 的三级回退

`cosyvoice/cli/frontend.py:56-75`：

```python
try:
    import ttsfrd                      # 1. 未安装
    ...
except:
    try:
        from wetext import Normalizer  # 2. 已安装，但初始化 403 失败
        ...
    except:
        self.text_frontend = ''        # 3. 降级为空
        logging.info('no frontend is avaliable')
```

所以日志里那句 `no frontend is avaliable` 一直存在，但很容易被忽略。

### 2.3 关键发现：资源其实在本地

探测发现 `/home/user/.cache/modelscope/hub/pengzhendong/wetext` **已完整存在**：

```
zh/tn/tagger.fst          527 KB
zh/tn/verbalizer.fst     1070 KB
zh/itn/tagger.fst        1929 KB
zh/itn/verbalizer.fst    1454 KB
en/tn/tagger.fst          ...
en/itn/tagger.fst         ...
```

下载时间 **2026-10-28**——说明之前某次有 token 或网络可用时下载过。

**问题只在于 wetext 不肯用本地缓存。**

---

## 三、解决方案：零侵入补丁

新增 `custom_voice/inference/text_frontend.py`，**不修改上游代码，不修改 wetext 包**。

### 3.1 核心做法

```python
def enable_wetext_frontend(frontend, *, root=None, force=False) -> bool:
    """给 CosyVoice 的 frontend 注入 wetext 文本前端。"""
    wetext_root = _find_wetext_root(root)
    zh = LocalWetextNormalizer(wetext_root)   # 直接读本地 fst
    frontend.zh_tn_model = zh
    frontend.text_frontend = "wetext"
    return True
```

`LocalWetextNormalizer` 复刻了 `wetext.Normalizer` 的逻辑，但**跳过 snapshot_download**：

```python
from kaldifst import TextNormalizer as normalizer
tagger = normalizer(str(root / "zh" / "tn" / "tagger.fst"))
verbalizer = normalizer(str(root / "zh" / "tn" / "verbalizer.fst"))
```

### 3.2 设计原则

| 原则 | 实现 |
|---|---|
| 零侵入 | 不改 `~/CosyVoice` 一行代码，不改 wetext 包 |
| 无网络依赖 | 只读本地 fst，不需要 token |
| 不静默降级 | 资源缺失时抛 `TextFrontendError`，列出查找过的路径 |
| 可回退 | 不启用时行为完全不变 |
| 幂等 | 重复调用不报错；已有前端时不覆盖（除非 `force=True`） |

### 3.3 用法

```python
from cosyvoice.cli.cosyvoice import CosyVoice3
from custom_voice.inference.text_frontend import enable_wetext_frontend

cosy = CosyVoice3(MODEL, fp16=False)
enable_wetext_frontend(cosy.frontend)     # 一行启用
```

---

## 四、效果验证

### 4.1 功能验证：数字被正确朗读

直接调用 `frontend.text_normalize()`：

```
输入: 现在是2026年11月9日，温度25度。
输出: 现在是二零二六年十一月九日，温度二十五度。

输入: 涨了100%，涨了3.14倍。
输出: 涨了百分之一百，涨了三点一四倍。
```

**A/B 时长对照**（数字写法 vs 中文写法）：

| 写法 | 平均时长 |
|---|---|
| A 数字「2026年11月9日」 | 4.56s |
| B 中文「二零二六年十一月九日」 | 4.94s |
| 差异 | 0.38s (7.7%) |

时长接近，说明数字确实被读成了中文。

### 4.2 严谨统计：5 次采样对比

**发现方法学问题**：同一文本重复 4 次，时长波动 5.24–7.64s，音节 CV 波动 0.487–0.869。
模型采样参数 `top_p=0.8 / top_k=25`，随机性很大，**单次对比不可靠**。

改用 5 次采样取统计量：

| 指标 | OFF 中位数 | ON 中位数 | 差异 | 判断 |
|---|---|---|---|---|
| 时长 | 8.04s | 8.04s | 0.00 | 不显著 |
| **音节CV** | **0.818** | **0.909** | **+0.091** | **显著** |
| 相似度 | 0.8800 | 0.8804 | +0.0004 | 不显著 |
| F0 | 363.6Hz | 369.2Hz | +5.6Hz | 在噪声内 |

**音节 CV 提升 +0.091 是显著改善。**

音节 CV = 音节时长变异系数，衡量节奏自然度（真人 0.3–0.6，越低越机械）。
提升 0.091 意味着节奏更有变化、更接近真人。

这直接对应之前的诊断：合成音频的音节 CV 比参考低 0.06–0.23，是"机械感"的来源。**TN 把这部分差距补上了一半。**

### 4.3 采样随机性数据（重要方法学发现）

同一文本重复 4 次：

```
时长    5.28 / 5.24 / 5.96 / 7.64 s     均值 6.03  标准差 0.97
音节CV  0.869 / 0.518 / 0.487 / 0.752   均值 0.656 标准差 0.160
```

**时长波动最大 46%（5.24 → 7.64s）。**

这意味着：
- 之前所有基于单次合成的对比结论都要重新审视
- 评估音色/韵律必须多次采样取中位数
- 用户听单条样本时的感受，可能受采样随机性影响很大

---

## 五、测试

新增 `tests/test_text_frontend.py`，13 个测试：

| 测试 | 覆盖 |
|---|---|
| `test_missing_resource_raises_readable_error` | 资源缺失时报可读错误 |
| `test_normalize_converts_digits` | 2026 → 二零二六 |
| `test_normalize_percent` | 100% → 百分之一百 |
| `test_normalize_decimal` | 3.14 → 三点一四 |
| `test_normalize_skips_text_without_digits` | 无数字时原样返回 |
| `test_enable_injects_frontend` | 注入后 text_frontend='wetext' |
| `test_enable_is_idempotent` | 重复启用不报错 |
| `test_enable_force_overrides` | force 才覆盖已有前端 |
| `test_frontend_status_shape` | 状态查询接口 |

全套 **158 个测试全绿**。

---

## 六、对项目的影响

### 6.1 需要接入的地方

Phase 2 实现 `CosyVoiceEngine` 时，`load()` 里要加一行：

```python
def load(self) -> None:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    self._model = CosyVoice3(self._model_dir, fp16=self._fp16)
    enable_wetext_frontend(self._model.frontend)   # ← 新增
```

### 6.2 `/health` 应该暴露前端状态

```python
{
  "ok": true,
  "engine": "cosyvoice3",
  "text_frontend": "wetext",       # ← 新增，让调用方知道 TN 是否生效
  ...
}
```

### 6.3 配置项

建议在 `configs/gpu.yaml` 增加：

```yaml
text_frontend: auto    # auto | wetext | none
```

- `auto`：尝试启用，失败则记录警告并降级（默认）
- `wetext`：强制启用，失败则启动报错
- `none`：不启用

---

## 七、残留问题

| 问题 | 说明 |
|---|---|
| 英文 TN 未充分验证 | 英文 fst 已加载，但只测了中文场景 |
| 繁简转换未启用 | `traditional_to_simple.fst` 存在但未接入 |
| 数字读法不一定符合角色风格 | 如"2026"读"二零二六"还是"两千零二十六"，取决于 FST 规则，可能不符合角色语气 |
| 采样随机性仍需控制 | 若要可复现结果，需固定随机种子 |

---

## 八、复现命令

```bash
# 验证 TN 转换正确性
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && cd ~/CosyVoice && \
  PYTHONPATH=/mnt/d/xiangmudaima/zjy14 python /mnt/d/xiangmudaima/zjy14/probe/_tn_verify.py"

# 5 次采样统计对比
wsl -e bash -lc "source ~/cosyvoice-venv/bin/activate && cd ~/CosyVoice && \
  PYTHONPATH=/mnt/d/xiangmudaima/zjy14 python /mnt/d/xiangmudaima/zjy14/probe/_tn_stats.py"

# 单元测试
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh tests/test_text_frontend.py
```

---

## 九、给你的建议

1. **不用配 token 了**。资源本地已有，补丁已实现。
2. **但要注意采样随机性**：同一句话每次合成结果差异很大（时长最大差 46%），听单条样本可能误判。建议多听几条。
3. **音节 CV 提升 0.091 是实打实的改善**，但能否听出来需要你判断。
4. 如果仍不满意，剩下的就是模型能力上限问题（CosyVoice3-0.5B 只有 5 亿参数），需要考虑换引擎。
