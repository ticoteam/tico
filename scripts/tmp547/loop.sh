#!/bin/bash
# 4 parallel loops of chat-send-signin; print each failure's error.
n=${1:-12}
fails=0
for w in 1 2 3 4; do
  ( for i in $(seq "$n"); do
      if ! TICO_BROWSER_CHANNEL= node ui/tests/chat-send-signin.cjs > /tmp/l$w.$i.log 2>&1; then
        echo "FAIL loop $w run $i"; grep -m3 -E "Error|did not settle|assert" /tmp/l$w.$i.log | cut -c1-4000
      fi
    done ) &
done
wait
echo "done $((4*n)) runs"
