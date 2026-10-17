#!/bin/bash
# ExecStartPre for reachy-mini-daemon: wait until the robot's USB camera and the
# PulseAudio sink for Reachy Mini Audio exist. The daemon detects media devices
# only once at startup, and after boot they show up well after /dev/ttyACM0.
# Never fails: after TIMEOUT seconds the daemon starts anyway.

TIMEOUT=${TIMEOUT:-120}
CAMERA_NAME="Reachy Mini Camera"
PULSE_SINK="Pollen_Robotics_Reachy_Mini_Audio"

camera_ready() {
    grep -qs "$CAMERA_NAME" /sys/class/video4linux/video*/name
}

audio_ready() {
    pactl list short sinks 2>/dev/null | grep -q "$PULSE_SINK"
}

for ((i = 0; i < TIMEOUT; i++)); do
    if camera_ready && audio_ready; then
        echo "Reachy media devices ready after ${i}s"
        exit 0
    fi
    sleep 1
done

camera_ready || echo "WARNING: camera '$CAMERA_NAME' not found after ${TIMEOUT}s"
audio_ready || echo "WARNING: PulseAudio sink '$PULSE_SINK' not found after ${TIMEOUT}s"
exit 0
