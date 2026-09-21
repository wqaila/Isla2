#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公共工具：配置加载 / 路径解析 / 随机种子设置

所有脚本统一通过本模块定位项目根目录，避免依赖当前工作目录（cwd）。
"""

import json, os, random
from pathlib import Path

# 项目根目录（本文件所在目录），配置中的相对路径均以此为基准
PROJECT_ROOT = Path(__file__).resolve().parent


def load_config(config_path=None):
    """加载 train_config.json（默认取项目根目录下的配置）"""
    path = Path(config_path) if config_path else PROJECT_ROOT / "train_config.json"
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(p):
    """把配置中的相对路径解析为基于项目根目录的绝对路径"""
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p


def set_seed(seed=42):
    """统一设置 random / numpy / torch（含 CUDA）随机种子，保证训练可复现"""
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass
