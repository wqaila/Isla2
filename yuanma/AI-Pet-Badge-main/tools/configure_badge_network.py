"""Generate the ignored network header from local credentials.

这个脚本会被 Build-Badge.ps1 在每次构建前调用，因此它生成的内容必须
覆盖固件用到的**全部** BADGE_* 宏。早期版本只写 SSID 和密码，
导致 wifi_config.h 里手工加的 BADGE_VOICE_AUTOCONNECT_* / BADGE_DNS_*
在下次构建时被抹掉，编译直接报未定义。

现在改成：必填项来自 .env.wifi，可选项支持覆盖，缺省用下面 DEFAULT_* 的值。
"""
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def c_string(value: str) -> str:
    """把 Python 字符串转成 C 字符串字面量。

    不能用 ``json.dumps``：它默认 ``ensure_ascii=True``，会把中文 SSID
    （如「没实力了吧2」）写成 ``"\\u6ca1\\u5b9e..."``。C 编译器**不解析**
    ``\\u`` 转义，固件最终拿到的是字面量反斜杠加字母，Wi-Fi 永远连不上，
    而且日志里看不出任何异常 —— 是典型的静默失效。

    这里只转义 C 真正需要的字符，非 ASCII 原样保留（源文件按 UTF-8 写）。
    """
    out = value.replace('\\', '\\\\').replace('"', '\\"')
    out = out.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return f'"{out}"'


# 可选宏的默认值（与热点网关一致）。可在 .env.wifi 里覆盖。
DEFAULT_VOICE_HOST = '192.168.137.1'
DEFAULT_VOICE_PORT = '8765'
DEFAULT_DNS_MAIN = '223.5.5.5'
DEFAULT_DNS_BACKUP = '8.8.8.8'

values = {}
config = root / '.env.wifi'
if config.exists():
    for line in config.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator:
            values[key.strip()] = value.strip()

ssid = values.get('WIFI_SSID', '')
password = values.get('WIFI_PASSWORD', '')
if len(ssid.encode()) > 32 or len(password.encode()) > 63:
    raise ValueError('Wi-Fi configuration exceeds ESP32 limits')

voice_host = values.get('VOICE_AUTOCONNECT_HOST', DEFAULT_VOICE_HOST)
voice_port = values.get('VOICE_AUTOCONNECT_PORT', DEFAULT_VOICE_PORT)
dns_main = values.get('DNS_MAIN', DEFAULT_DNS_MAIN)
dns_backup = values.get('DNS_BACKUP', DEFAULT_DNS_BACKUP)

if not voice_port.isdigit():
    raise ValueError('VOICE_AUTOCONNECT_PORT must be numeric')

header = (
    '/* Generated locally. Do not commit. */\n'
    '#pragma once\n'
    f'#define BADGE_WIFI_SSID {c_string(ssid)}\n'
    f'#define BADGE_WIFI_PASSWORD {c_string(password)}\n'
    '\n'
    '/* 语音服务端自动连接。置空 host 则关闭自动连接，仍可手动 VOICE <ip> [port]。 */\n'
    f'#define BADGE_VOICE_AUTOCONNECT_HOST {c_string(voice_host)}\n'
    f'#define BADGE_VOICE_AUTOCONNECT_PORT {voice_port}\n'
    '\n'
    '/* DNS 兜底：绕开可能失效的 ICS DNS 代理。DNS_MAIN 置空则沿用 DHCP 下发值。 */\n'
    f'#define BADGE_DNS_MAIN {c_string(dns_main)}\n'
    f'#define BADGE_DNS_BACKUP {c_string(dns_backup)}\n'
)

target = root / 'firmware/ai_pet_badge/main/wifi_config.h'

# 旧文件里的 SSID 与本次要写入的不同时，明确提示一句。
# 背景：wifi_config.h 是 .gitignore 的生成物，改热点后如果忘了同步 .env.wifi，
# 下次构建会把 SSID 悄悄改回去，表现为"板子突然连不上 WiFi"，很难联想到构建脚本。
previous_ssid = None
if target.exists():
    m = re.search(r'#define\s+BADGE_WIFI_SSID\s+"((?:[^"\\]|\\.)*)"',
                  target.read_text(encoding='utf-8', errors='replace'))
    if m:
        previous_ssid = m.group(1).replace('\\"', '"').replace('\\\\', '\\')

if not target.exists() or target.read_text(encoding='utf-8') != header:
    target.write_text(header, encoding='utf-8')

if previous_ssid is not None and previous_ssid != ssid:
    print(f'注意：SSID 由 "{previous_ssid}" 改为 "{ssid}"（来源 .env.wifi）。')
    print('      如果这不是你想要的，改 .env.wifi 的 WIFI_SSID 后重新构建。')

print('Local Wi-Fi configuration ready.' if ssid else 'Wi-Fi unconfigured; dashboard will work offline.')
