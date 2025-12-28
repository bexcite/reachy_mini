# Reachy Mini Lite + Pi 5 Setup Summary (auto start of daemon + convo app)

**IMPORTANT NOTE**: Claude Code was heavily used for everything, so some things may not be optimal or
even correct, though it works fine on my setup.

## Motivation

Set up Raspberry Pi 5 as a Reachy Mini Lite computer that runs reachy-mini-daemon and reachy-conversation-app
with reliable auto-start (and restart) on power ON, power cycle of robot, and Pi reboot. (i.e. kid-level reliability)

## Hardware

- Reachy Mini Lite + Official Power Supply
- Pi 5, 8Gb + 64Gb SD Card + Official Power Supply
- Pi 5 case - Pimoroni 3D Printed
- Cable from Pi 5 to Reachy Mini Lite (USB-A -> USB-C)

## Initial Pi 5 Software + WiFi/SSH Setup

This part is not described in detail (ask your LLM of choice to help):

- Raspberry Pi OS Lite [Download](https://www.raspberrypi.com/software/operating-systems/)
- Flash pi 5 image + enable wifi + enable ssh + add user `reachy` + set hostname `reachy-brain`
- Ensure that you can connect over SSH to the WiFi-connected Pi (and verify that after a power cycle, WiFi comes up and you can SSH again, e.g. `ssh reachy@reachy-brain.local`)

## Get reachy-mini repo and dependencies

On Pi 5 we assume that we have a folder:
```
mkdir ~/code
```

Then follow official installation steps from source code: https://github.com/pollen-robotics/reachy_mini/blob/develop/docs/SDK/installation.md

Expected Result:
- Code pulled to `~/code/reachy_mini` (I used branch `origin/develop`)
- `uv` creates the environment in `~/code/reachy_mini/.venv` (this is the default location when running `uv sync` or `uv run` commands from the repo directory).

NOTE: Some system-level dependencies may be required (list is not exhaustive):
```
sudo apt update
sudo apt install git git-lfs libcairo2-dev pulseaudio
```

## Trying to run `uv run reachy-mini-daemon` and fixing issues

Assuming the `~/code/reachy_mini` repo is pulled and the `uv` environment is in `~/code/reachy_mini/.venv`.

If you try to run `cd ~/code/reachy_mini && uv run reachy-mini-daemon`, there will be several
errors to fix.

I'm leaving this in the format that Claude Code produced.

### 1. Audio Device Access
- Problem: Error querying device -1 - daemon couldn't access audio
- Cause: User reachy not in audio group
- Fix: `sudo usermod -aG audio reachy`

### 2. Camera Access
- Problem: RuntimeError: Camera not found
- Cause: User reachy not in video group
- Fix: `sudo usermod -aG video reachy`

### 3. Audio Sharing Between Daemon & Apps (systemd service)
- Problem: ValueError: Not an output device: 'default' when running as systemd service
- Cause: Service couldn't access PulseAudio (no user session)
- Fix: Added `Environment="XDG_RUNTIME_DIR=/run/user/1000"` to `reachy-mini-daemon.service` file

### 4. Daemon Not Exiting on Power Cycle
- Problem: When Reachy Mini power is turned off, daemon got stuck instead of restarting
- Cause: Rust panic in `close()` bypassed Python exception handler (`pyo3_runtime.PanicException` is `BaseException`, not `Exception`)
- Fix: Changed `except Exception` to `except BaseException` in both:
  - `src/reachy_mini/daemon/daemon.py` - added `os._exit(1)` on backend crash
  - `src/reachy_mini/daemon/backend/abstract.py` - wrapped `close()` in `try/except`

At this point, install and run `reachy_mini_conversation_app`:
- Ensure that `reachy-mini-daemon` is running, then open `http://reachy-brain.local:8000/` and install `reachy_mini_conversation_app` from the "Install from 🤗 Hugging Face" section.
- Toggle `reachy_mini_conversation_app` to ON state and continue (if voice output is not working, proceed to issue #5)

## Files to Create on Pi

### /etc/systemd/system/reachy-mini-daemon.service
```
[Unit]
Description=Reachy Mini Daemon
After=network.target pulseaudio.service
# Wait for USB serial device to be available
Wants=dev-ttyACM0.device
After=dev-ttyACM0.device

[Service]
Type=simple
User=reachy
Group=reachy
WorkingDirectory=/home/reachy/code/reachy_mini
Environment="PATH=/home/reachy/.local/bin:/usr/local/bin:/usr/bin:/bin"
# PulseAudio access for audio sharing between daemon and apps
Environment="XDG_RUNTIME_DIR=/run/user/1000"
ExecStart=/home/reachy/.local/bin/uv run reachy-mini-daemon
Restart=always
RestartSec=5

# Give it time to gracefully shutdown
TimeoutStopSec=30

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=reachy-mini-daemon

[Install]
WantedBy=multi-user.target
```

### /etc/systemd/system/reachy-conversation-app.service
```
[Unit]
Description=Start Reachy Mini Conversation App
After=reachy-mini-daemon.service
Requires=reachy-mini-daemon.service

[Service]
Type=oneshot
User=reachy
Group=reachy
ExecStart=/home/reachy/start-conversation-app.sh
RemainAfterExit=yes
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### /home/reachy/.asoundrc

This fixes issue #5 (Audio Sharing Between Daemon & Conversation App):
- Problem: Conversation app audio not playing - daemon held ALSA device directly, blocking other processes
- Cause: PortAudio compiled without PulseAudio backend (only ALSA/OSS available)
- Diagnosis: `fuser -v /dev/snd/*` showed daemon holding pcmC3D0p directly; PulseAudio sink was SUSPENDED

```
# Configure dmix for Reachy Mini Audio to allow sharing
pcm.reachy_dmix {
    type dmix
    ipc_key 1024
    slave {
        pcm "hw:3,0"
        rate 48000
        channels 2
    }
}

pcm.reachy_dsnoop {
    type dsnoop
    ipc_key 1025
    slave {
        pcm "hw:3,0"
        rate 16000
        channels 6
    }
}

pcm.!default {
    type plug
    slave.pcm "reachy_dmix"
}

ctl.!default {
    type hw
    card 3
}
```

### ~/.config/pulse/default.pa

Ensures PulseAudio loads the Reachy Mini Audio device on startup (prevents null sink fallback):

```
# Include the default PulseAudio config
.include /etc/pulse/default.pa

# Load Reachy Mini Audio device explicitly
.ifexists module-alsa-sink.so
load-module module-alsa-sink device=hw:Audio rate=48000 sink_name=reachy_audio sink_properties=device.description=Reachy_Mini_Audio
set-default-sink reachy_audio
.endif
```

After creating the service files, enable them:
```bash
sudo systemctl daemon-reload
sudo systemctl enable reachy-mini-daemon.service
sudo systemctl enable reachy-conversation-app.service
```

### /home/reachy/start-conversation-app.sh

Create this file and make it executable with `chmod +x /home/reachy/start-conversation-app.sh`:

```bash
#!/bin/bash
MAX_ATTEMPTS=30
ATTEMPT=0

echo "Waiting for reachy-mini-daemon to be ready..."

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if curl -s -X POST http://localhost:8000/health-check > /dev/null 2>&1; then
        echo "Daemon is ready..."
        sleep 2

        # Set volume to 100%
        echo "Setting volume to 100%..."
        curl -s -X POST http://localhost:8000/api/volume/set \
            -H "Content-Type: application/json" \
            -d '{"volume": 100}' > /dev/null 2>&1

        # Start the conversation app
        echo "Starting conversation app..."
        curl -s -X POST http://localhost:8000/api/apps/start-app/reachy_mini_conversation_app

        if [ $? -eq 0 ]; then
            echo "Conversation app started successfully"
            exit 0
        else
            echo "Failed to start conversation app"
            exit 1
        fi
    fi

    ATTEMPT=$((ATTEMPT + 1))
    echo "Attempt $ATTEMPT/$MAX_ATTEMPTS - daemon not ready yet..."
    sleep 2
done

echo "Timeout waiting for daemon"
exit 1
```

## Code Changes

Changes to the reachy_mini repo, collected in this branch: [pb/pi-setup](https://github.com/bexcite/reachy_mini/tree/pb/pi-setup)
```
src/reachy_mini/daemon/daemon.py - Exit process on backend crash:
except BaseException as e:
    self.logger.error(f"Backend encountered an error: {e}")
    self.logger.error("Backend crashed, exiting process for restart...")
    import os
    os._exit(1)
```

```
src/reachy_mini/daemon/backend/abstract.py - Handle Rust panics in close():
except BaseException as e:
    self.error = str(e)
    try:
        self.close()
    except BaseException as close_error:
        import logging
        logging.getLogger(__name__).error(f"Error during close(): {close_error}")
    raise e
```

```
src/reachy_mini/media/audio_sounddevice.py - Use system default device & force stereo:
# In __init__:
# Use None to let sounddevice pick the system default output device
# This avoids race conditions with PulseAudio during startup
self._output_device_id: int | None = None

# In start_playing():
self._output_stream = sd.OutputStream(
    samplerate=self.get_output_audio_samplerate(),
    device=self._output_device_id,
    channels=2,  # Force stereo to avoid PulseAudio's 32-channel default
    callback=self._output_callback,
)

# In get_output_channels():
def get_output_channels(self) -> int:
    # Force stereo output regardless of device capability
    return 2
```

## Useful Commands

For convenience, add this to your local `~/.ssh/config`:
```
Host reachy-brain
    HostName reachy-brain.local
    User reachy
```

Then you can use `ssh reachy-brain` instead of `ssh reachy@reachy-brain.local`.

For debugging:

```
# View daemon logs
ssh reachy@reachy-brain.local "sudo journalctl -u reachy-mini-daemon -f"

# Restart daemon
ssh reachy@reachy-brain.local "sudo systemctl restart reachy-mini-daemon"

# Check status
ssh reachy@reachy-brain.local "sudo systemctl status reachy-mini-daemon"

# Manual conversation app start
ssh reachy@reachy-brain.local "sudo systemctl start reachy-conversation-app"
```

## Summary of Behavior

| Event                | Result                                                |
|----------------------|-------------------------------------------------------|
| Pi boots             | Daemon waits for serial device, then starts           |
| Reachy power on      | Daemon connects, volume→100%, conversation app starts |
| Reachy power off     | Daemon detects failure, exits, systemd restarts it    |
| Reachy power back on | Daemon reconnects automatically                       |
