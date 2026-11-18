"""SQLite 表结构定义。

Phase 1 只建表，不做业务 CRUD（业务在 Phase 3）。

设计说明：
- 时间统一存 ISO8601 字符串（UTC），便于跨语言读取
- 主键用 TEXT 而非自增 INT，因为 character_id / speaker_id 需要人类可读且可跨设备
- 外键开启，删除角色时级联处理
"""

from __future__ import annotations

#: 当前 schema 版本。每次改表结构必须 +1，并在 MIGRATIONS 里加一步。
#: v2：为合成记录查询补索引（voice_id / error_code / 复合时间索引）。
SCHEMA_VERSION = 2

#: 建表语句。用 IF NOT EXISTS 保证幂等。
SCHEMA_SQL = """
-- 角色档案：一个人设，绑定一个声音
CREATE TABLE IF NOT EXISTS characters (
    character_id        TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    personality         TEXT NOT NULL DEFAULT '',
    speaking_habits     TEXT NOT NULL DEFAULT '',
    default_emotion     TEXT NOT NULL DEFAULT 'neutral',
    default_style       TEXT NOT NULL DEFAULT '',
    speaker_id          TEXT,
    language            TEXT NOT NULL DEFAULT 'zh',
    allowed_emotions    TEXT NOT NULL DEFAULT '',   -- 逗号分隔
    voice_instruction_template TEXT NOT NULL DEFAULT '',
    model_id            TEXT NOT NULL DEFAULT '',
    model_version       TEXT NOT NULL DEFAULT '',
    config_version      TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (speaker_id) REFERENCES speakers(speaker_id) ON DELETE SET NULL
);

-- 声音档案：一份参考音频及其元数据
CREATE TABLE IF NOT EXISTS speakers (
    speaker_id          TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL DEFAULT '',
    audio_path          TEXT NOT NULL,
    transcript          TEXT NOT NULL DEFAULT '',
    sample_rate         INTEGER NOT NULL DEFAULT 0,
    channels            INTEGER NOT NULL DEFAULT 0,
    duration_s          REAL NOT NULL DEFAULT 0,
    quality_report      TEXT NOT NULL DEFAULT '{}',  -- JSON
    consent_status      TEXT NOT NULL DEFAULT 'unknown',
    source              TEXT NOT NULL DEFAULT '',
    feature_cache_key   TEXT NOT NULL DEFAULT '',
    model_version       TEXT NOT NULL DEFAULT '',
    preprocessing_version TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- 参考音频元数据：保留原始文件信息，不覆盖
CREATE TABLE IF NOT EXISTS reference_audio (
    audio_id            TEXT PRIMARY KEY,
    speaker_id          TEXT,
    original_path       TEXT NOT NULL,
    stored_path         TEXT NOT NULL,
    sha256              TEXT NOT NULL DEFAULT '',
    sample_rate         INTEGER NOT NULL DEFAULT 0,
    channels            INTEGER NOT NULL DEFAULT 0,
    duration_s          REAL NOT NULL DEFAULT 0,
    source              TEXT NOT NULL DEFAULT '',
    license_note        TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    FOREIGN KEY (speaker_id) REFERENCES speakers(speaker_id) ON DELETE CASCADE
);

-- 合成记录：每次请求一行，用于事后分析
CREATE TABLE IF NOT EXISTS synthesis_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id          TEXT NOT NULL,
    character_id        TEXT,
    speaker_id          TEXT,
    text                TEXT NOT NULL,
    emotion             TEXT NOT NULL DEFAULT '',
    style               TEXT NOT NULL DEFAULT '',
    speed               REAL NOT NULL DEFAULT 1.0,
    instruction         TEXT NOT NULL DEFAULT '',
    sample_rate         INTEGER NOT NULL DEFAULT 0,
    duration_s          REAL NOT NULL DEFAULT 0,
    total_ms            REAL NOT NULL DEFAULT 0,
    first_audio_ms      REAL,
    rtf                 REAL NOT NULL DEFAULT 0,
    peak_vram_mb        REAL,
    engine              TEXT NOT NULL DEFAULT '',
    error_code          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_synthesis_request ON synthesis_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_synthesis_created ON synthesis_logs(created_at);

-- v2 新增：合成记录查询需要的索引。
-- 查询场景：按音色筛选、按错误筛选、按时间倒序分页。
CREATE INDEX IF NOT EXISTS idx_synthesis_speaker ON synthesis_logs(speaker_id);
CREATE INDEX IF NOT EXISTS idx_synthesis_emotion ON synthesis_logs(emotion);
CREATE INDEX IF NOT EXISTS idx_synthesis_error ON synthesis_logs(error_code);
CREATE INDEX IF NOT EXISTS idx_synthesis_created_desc
    ON synthesis_logs(created_at DESC, id DESC);

-- 性能指标时序：用于仪表盘
CREATE TABLE IF NOT EXISTS metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    value               REAL NOT NULL,
    labels              TEXT NOT NULL DEFAULT '{}',  -- JSON
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_name_created ON metrics(name, created_at);

-- schema 版本记录
CREATE TABLE IF NOT EXISTS schema_version (
    version             INTEGER PRIMARY KEY,
    applied_at          TEXT NOT NULL
);
"""

__all__ = ["SCHEMA_SQL", "SCHEMA_VERSION"]
