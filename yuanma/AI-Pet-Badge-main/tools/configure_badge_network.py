"""Generate the ignored network header from local credentials."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
values = {}
config = root / '.env.wifi'
if config.exists():
    for line in config.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator:
            values[key.strip()] = value.strip()
ssid, password = values.get('WIFI_SSID', ''), values.get('WIFI_PASSWORD', '')
if len(ssid.encode()) > 32 or len(password.encode()) > 63:
    raise ValueError('Wi-Fi configuration exceeds ESP32 limits')
header = ('/* Generated locally. Do not commit. */\n#pragma once\n'
          f'#define BADGE_WIFI_SSID {json.dumps(ssid, ensure_ascii=True)}\n'
          f'#define BADGE_WIFI_PASSWORD {json.dumps(password, ensure_ascii=True)}\n')
target = root / 'firmware/ai_pet_badge/main/wifi_config.h'
if not target.exists() or target.read_text(encoding='utf-8') != header:
    target.write_text(header, encoding='utf-8')
print('Local Wi-Fi configuration ready.' if ssid else 'Wi-Fi unconfigured; dashboard will work offline.')
