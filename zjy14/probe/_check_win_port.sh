#!/usr/bin/env bash
# 检查 WSL 能否访问 Windows 侧 zjy13 的设备接入端口。
# 用途：_p8_device_check.py 在 WSL 里跑，而 zjy13 跑在 Windows，
# WSL2 的 127.0.0.1 不等于 Windows 的 127.0.0.1，需要显式探测。
PORT="${1:-8765}"

echo "--- default gateway ---"
GW=$(ip route show default | awk '{print $3}' | head -1)
echo "GW=$GW"

echo "--- 127.0.0.1:$PORT ---"
if timeout 3 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
  echo "OK 127.0.0.1"
else
  echo "FAIL 127.0.0.1"
fi

if [ -n "$GW" ]; then
  echo "--- $GW:$PORT ---"
  if timeout 3 bash -c "cat < /dev/null > /dev/tcp/$GW/$PORT" 2>/dev/null; then
    echo "OK $GW"
  else
    echo "FAIL $GW"
  fi
fi

echo "--- HTTP /api/devices/health via 127.0.0.1 ---"
curl -s --max-time 4 "http://127.0.0.1:$PORT/api/devices/health" || echo "(no response)"

echo
echo "--- HTTP /api/devices/health via gateway ---"
if [ -n "$GW" ]; then
  curl -s --max-time 4 "http://$GW:$PORT/api/devices/health" || echo "(no response)"
fi
echo
