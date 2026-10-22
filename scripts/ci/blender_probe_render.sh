#!/usr/bin/env bash
# TẠM THỜI: render N frame cảnh thật (scene_basic.py) và in thời gian. Dùng: blender_probe_render.sh <label> <eevee|cycles> <w> <h> <frames>
set -uo pipefail
label=$1; engine=$2; w=$3; h=$4; frames=$5
d=$(mktemp -d); mkdir -p "$d/frames"
cat > "$d/cfg.json" <<EOF
{"frames_dir": "$d/frames", "blend_path": "$d/scene.blend", "width": $w, "height": $h, "fps": 12, "frames": $frames,
 "shape": "torus", "object_color": "#e4572e", "background_color": "#1b2a41", "camera_move": "orbit", "engine": "$engine", "samples": 16}
EOF
t0=$(date +%s.%N)
"$BLENDER_BIN" -b --factory-startup --python-exit-code 3 --python video_engine/engines/blender_scripts/scene_basic.py -- --config "$d/cfg.json" > "$d/log.txt" 2>&1
rc=$?
t1=$(date +%s.%N)
n=$(ls "$d/frames" | wc -l)
echo "PROBE $label engine=$engine ${w}x${h} frames_requested=$frames frames_written=$n exit=$rc seconds=$(echo "$t1 - $t0" | bc)"
grep -E 'SCENE_READY|FRAME_DONE 1/|Error|Traceback|EGL' "$d/log.txt" | sort | uniq -c | head -8
[ "$rc" -ne 0 ] && tail -15 "$d/log.txt"
exit $rc
