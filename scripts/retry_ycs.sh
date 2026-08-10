#!/bin/bash
# YCS François Chollet retry script
# Run manually or from cron; paths resolve relative to this repository by default.
# Note: anchor.fm is restricted on this network, requires proxy

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE_DIR="${PODCAST_PIPELINE_HOME:-${PIPELINE_DIR:-$SCRIPT_ROOT}}"
PROXY_URL="${PODCAST_PIPELINE_PROXY:-http://127.0.0.1:7890}"
AUDIO_DIR="$PIPELINE_DIR/download/$(date +%Y-%m-%d)"
mkdir -p "$AUDIO_DIR"
LOG="$PIPELINE_DIR/ycs_retry.log"
TARGET="YCS_Chollet_AGI"

echo "[$(date)] Attempting YCS download with proxy..." >> "$LOG"

# Try with proxy (anchor.fm requires proxy on this network)
http_proxy="$PROXY_URL" \
https_proxy="$PROXY_URL" \
curl -L --max-time 60 \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  -o "$AUDIO_DIR/${TARGET}.mp3" \
  "https://anchor.fm/s/8c1524bc/podcast/play/117542318/https%3A%2F%2Fd3ctxlq1ktw2nl.cloudfront.net%2Fstaging%2F2026-2-27%2F420855592-44100-2-b4a8cc8c5dca1.mp3" 2>> "$LOG"

SIZE=$(wc -c < "$AUDIO_DIR/${TARGET}.mp3" 2>/dev/null || echo 0)
if [ "$SIZE" -gt 5000000 ]; then
  echo "[$(date)] SUCCESS: ${TARGET}.mp3 downloaded ($SIZE bytes, $(du -h "$AUDIO_DIR/${TARGET}.mp3" | cut -f1))" >> "$LOG"
  echo "✅ YCS audio downloaded: $AUDIO_DIR/${TARGET}.mp3 ($(du -h "$AUDIO_DIR/${TARGET}.mp3" | cut -f1))"
  exit 0
fi
rm -f "$AUDIO_DIR/${TARGET}.mp3"
echo "[$(date)] Proxy attempt failed (${SIZE} bytes), trying Spotify RSS..." >> "$LOG"

# Try Spotify RSS as backup
http_proxy="$PROXY_URL" \
https_proxy="$PROXY_URL" \
curl -L --max-time 60 \
  -H "User-Agent: Mozilla/5.0" \
  -o "$AUDIO_DIR/${TARGET}.mp3" \
  "https://open.spotify.com/show/1tgqafxZAB0Bjd8nkwVtE4" 2>> "$LOG"

SIZE=$(wc -c < "$AUDIO_DIR/${TARGET}.mp3" 2>/dev/null || echo 0)
if [ "$SIZE" -gt 5000000 ]; then
  echo "[$(date)] SUCCESS via Spotify RSS" >> "$LOG"
  exit 0
fi
rm -f "$AUDIO_DIR/${TARGET}.mp3"
echo "[$(date)] All sources failed, next retry in 6h" >> "$LOG"
exit 1
