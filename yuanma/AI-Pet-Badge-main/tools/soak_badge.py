"""Monitor a badge without resetting it; retain console, samples and a soak summary.

An optional pause-file releases USB while another tool captures the screen.
Append diagnostic commands to control-file to run them in the monitor's serial session.
"""
import argparse
import json
import statistics
from pathlib import Path
import time

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--control-file', type=Path)
    parser.add_argument('--pause-file', type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    samples, imus, dashboards, faults = [], [], [], []
    started = time.monotonic()
    port = None
    pending = bytearray()
    control_offset = 0
    next_poll = next_slow = next_report = started
    allowed = {'STATUS', 'IMU', 'DASH', 'NEXT', 'IMU CALIBRATE', 'IMU SELFTEST', 'IMU BENCH'}

    def send(command):
        port.write((command+'\n').encode('ascii'))
        port.flush()

    with (args.output_dir/'console.log').open('w', encoding='utf-8') as log:
        try:
            while time.monotonic()-started < args.seconds:
                now = time.monotonic()
                if args.pause_file and args.pause_file.exists():
                    if port:
                        port.close()
                        port = None
                    time.sleep(.1)
                    continue
                if port is None:
                    port = serial.Serial(port=None, baudrate=115200, timeout=.1)
                    port.dtr = False
                    port.rts = False
                    port.port = args.port
                    port.open()
                    next_poll = next_slow = now
                if now >= next_poll:
                    send('STATUS')
                    next_poll = now+1
                if now >= next_slow:
                    send('IMU')
                    send('DASH')
                    next_slow = now+5
                if args.control_file and args.control_file.exists():
                    control = args.control_file.read_text(encoding='utf-8').splitlines()
                    for command in control[control_offset:]:
                        command = command.strip()
                        if command in allowed:
                            send(command)
                        elif command:
                            raise ValueError(f'Unsupported diagnostic command: {command}')
                    control_offset = len(control)
                pending.extend(port.read(4096))
                while b'\n' in pending:
                    raw, _, pending = pending.partition(b'\n')
                    line = raw.decode('utf-8', errors='replace').rstrip('\r')
                    log.write(f'{now-started:.3f} {line}\n')
                    for prefix, target in (('BADGE_STATUS ', samples), ('BADGE_IMU ', imus),
                                           ('BADGE_DASHBOARD ', dashboards)):
                        if line.startswith(prefix):
                            try:
                                target.append({'elapsed': now-started, **json.loads(line[len(prefix):])})
                            except json.JSONDecodeError:
                                faults.append({'elapsed': now-started, 'line': line, 'kind': 'parse'})
                    if any(word in line.lower() for word in ('guru meditation', 'panic', 'watchdog', 'rst:0x')):
                        faults.append({'elapsed': now-started, 'line': line, 'kind': 'reset_or_panic'})
                log.flush()
                if now >= next_report:
                    if samples:
                        s = samples[-1]
                        print(f'{now-started:.0f}s samples={len(samples)} stage={s["stage"]} '
                              f'frames={s["frames"]} psram={s["psram"]} faults={len(faults)}', flush=True)
                    next_report = now+30
        finally:
            if port:
                port.close()
    for left, right in zip(samples, samples[1:]):
        if right['frames'] < left['frames'] or right['touches'] < left['touches']:
            faults.append({'elapsed': right['elapsed'], 'kind': 'counter_reset'})
    warmed = [s for s in samples if s['elapsed'] >= 120] or samples
    first_window = warmed[:30]
    last_window = warmed[-30:]
    def change(key):
        if not warmed:
            return None
        return statistics.median(s[key] for s in last_window)-statistics.median(s[key] for s in first_window)
    summary = {
        'duration_seconds': time.monotonic()-started, 'samples': len(samples),
        'imu_samples': len(imus), 'dashboard_samples': len(dashboards), 'faults': faults,
        'imu_error_max': max((s['errors'] for s in imus), default=None),
        'imu_unavailable_samples': sum(not s['available'] for s in imus if s['elapsed'] > 10),
        'free_heap_change_after_warmup': change('heap'),
        'psram_change_after_warmup': change('psram'),
        'free_heap_min_after_warmup': min((s['heap'] for s in warmed), default=None),
        'free_heap_max_after_warmup': max((s['heap'] for s in warmed), default=None),
        'psram_min_after_warmup': min((s['psram'] for s in warmed), default=None),
        'psram_max_after_warmup': max((s['psram'] for s in warmed), default=None),
        'last_status': samples[-1] if samples else None,
        'last_dashboard': dashboards[-1] if dashboards else None,
    }
    (args.output_dir/'samples.json').write_text(
        json.dumps({'status': samples, 'imu': imus, 'dashboard': dashboards}, ensure_ascii=False, indent=2),
        encoding='utf-8')
    (args.output_dir/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if len(samples) < args.seconds*.75 or faults or summary['imu_unavailable_samples']:
        raise SystemExit('Soak incomplete or faults detected; inspect console and summary')


if __name__ == '__main__':
    main()
