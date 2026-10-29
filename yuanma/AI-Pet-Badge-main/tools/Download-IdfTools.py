"""Download official pinned tool archives, resume when supported, verify SHA-256."""
import hashlib
import json
import os
import time
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

# HTTPX 0.28.1 cannot parse an IPv6 /128 bypass. Normalize this loopback rule only.
for proxy_key in ('NO_PROXY', 'no_proxy'):
    if proxy_key in os.environ:
        os.environ[proxy_key] = ','.join(
            '::1' if entry.strip() in {'::1/128', '[::1]', '[::1]/128'} else entry
            for entry in os.environ[proxy_key].split(',')
        )

spec_path = Path(r'C:\Espressif\downloads\idf-tools-v5.5.3.json')
download_dir = Path(r'C:\Espressif\dist')
selected = {
    'xtensa-esp-elf-gdb', 'xtensa-esp-elf', 'riscv32-esp-elf',
    'esp32ulp-elf', 'cmake', 'openocd-esp32', 'ninja', 'idf-exe',
    'ccache', 'dfu-util', 'esp-rom-elfs',
}


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_large_archive(url, target, expected_size):
    start_offset = target.stat().st_size if target.exists() else 0
    if start_offset >= expected_size:
        start_offset = 0
    chunk_dir = download_dir / 'verified-range-parts'
    chunk_dir.mkdir(exist_ok=True)
    ranges = [
        (start, min(start + 32 * 1024 * 1024 - 1, expected_size - 1))
        for start in range(start_offset, expected_size, 32 * 1024 * 1024)
    ]

    def get_part(bounds):
        start, end = bounds
        part = chunk_dir / ('%s.%d-%d' % (target.name, start, end))
        if part.exists() and part.stat().st_size == end - start + 1:
            return part
        for attempt in range(3):
            try:
                with httpx.stream('GET', url, headers={'Range': 'bytes=%d-%d' % (start, end)}, follow_redirects=True, timeout=90) as response:
                    response.raise_for_status()
                    expected_range = 'bytes %d-%d/%d' % (start, end, expected_size)
                    if response.status_code != 206 or response.headers.get('content-range') != expected_range:
                        raise RuntimeError('Server did not honor the requested exact byte range.')
                    with part.open('wb') as output:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            output.write(chunk)
                if part.stat().st_size != end - start + 1:
                    raise OSError('Incomplete range response')
                print('RANGE ready %s %d-%d' % (target.name, start, end), flush=True)
                return part
            except (httpx.HTTPError, OSError):
                if attempt == 2:
                    raise
                time.sleep(2)

    print('RANGED DOWNLOAD %s existing=%.1f MiB pieces=%d' % (target.name, start_offset / 1048576, len(ranges)), flush=True)
    with ThreadPoolExecutor(max_workers=6) as executor:
        parts = list(executor.map(get_part, ranges))
    with target.open('ab' if start_offset else 'wb') as output:
        for part in parts:
            with part.open('rb') as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)


for tool in json.loads(spec_path.read_text(encoding='utf-8-sig'))['tools']:
    if tool['name'] not in selected:
        continue
    recommended = next(v for v in tool['versions'] if v['status'] == 'recommended')
    archive = recommended.get('win64') or recommended['any']
    url = archive['url']
    if urlparse(url).hostname not in {'github.com', 'dl.espressif.com'}:
        raise RuntimeError('Unexpected official tool host: ' + urlparse(url).hostname)
    target = download_dir / Path(urlparse(url).path).name
    if target.exists() and target.stat().st_size == archive['size'] and digest(target) == archive['sha256']:
        print('VERIFIED cached ' + target.name, flush=True)
        continue
    if archive['size'] > 64 * 1024 * 1024:
        download_large_archive(url, target, archive['size'])
        if target.stat().st_size != archive['size'] or digest(target) != archive['sha256']:
            raise RuntimeError('Ranged archive failed pinned SHA-256 verification: ' + target.name)
        print('VERIFIED ' + target.name, flush=True)
        continue
    for attempt in range(1, 4):
        current = target.stat().st_size if target.exists() else 0
        if current >= archive['size']:
            current = 0
        headers = {'Range': 'bytes=%d-' % current} if current else {}
        print('DOWNLOAD %s attempt=%d cached=%.1f MiB' % (target.name, attempt, current / 1048576), flush=True)
        started = time.monotonic()
        last_report = started
        try:
            with httpx.stream('GET', url, headers=headers, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                if response.status_code == 206:
                    content_range = response.headers.get('content-range', '')
                    if not content_range.startswith('bytes %d-' % current):
                        raise RuntimeError('Unexpected content range: ' + content_range)
                    mode = 'ab'
                elif response.status_code == 200:
                    current = 0
                    mode = 'wb'
                else:
                    raise RuntimeError('Unexpected response status: %d' % response.status_code)
                with target.open(mode) as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        output.write(chunk)
                        current += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 15:
                            output.flush()
                            print('PROGRESS %s %.1f/%.1f MiB' % (target.name, current / 1048576, archive['size'] / 1048576), flush=True)
                            last_report = now
            if target.stat().st_size != archive['size'] or digest(target) != archive['sha256']:
                raise RuntimeError('Archive size or SHA-256 does not match pinned SDK tools.json: ' + target.name)
            print('VERIFIED ' + target.name, flush=True)
            break
        except (httpx.HTTPError, OSError) as error:
            print('RETRY %s %s' % (target.name, type(error).__name__), flush=True)
            if attempt == 3:
                raise
            time.sleep(2)

print('All selected official tool archives passed SHA-256 verification.', flush=True)
