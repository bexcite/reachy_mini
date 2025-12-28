
# Reachy Mini Lite + Pi 5 Setup Summary (auto start of daemon + convo app)

IMPORTANT NOTE: Claude Code was heavily used for everything, so something maybe not optimal or
maybe not even correct, though it works fine on my setup.

# Motivation

Make Raspberry Pi 5 as a Reachy Mini Lite computer that runs the reachy-mini-daemon reachy-conversation-app
that auto starts (and restarts) realiably on power ON and powercycle of robot and off a pi host. (i.e. kids level reliability)

# Harwdware

- Reachy Mini Lite + Official Power Supply
- Pi 5, 8Gb + 64Gb SD Card + Official Power Supply
- Pi 5 case - Pimoroni 3D Printed
- Cable from Pi 5 to Reachy Mini Lite (USB-A -> USB-C)

## Initial Pi 5 Software + WiFi/SSH Setup

This part is not described in detail (ask your LLM of choice to get through) with this:

- Raspberry Pi OS Lite https://downloads.raspberrypi.com/raspios_lite_armhf/images/raspios_lite_armhf-2025-12-04/2025-12-04-raspios-trixie-armhf-lite.img.xz
- Flash pi 5 image + enable wifi + enable ssh + add user `reachy` + set hostname `reachy-brain`
- Ensure that you can connect over ssh to wifi connected pi, (and check that on pi powercycle the WiFi is UP and you can ssh to it again. e.g. `ssh reachy@reachy-brain.local`)

## Get reachy-mini repo and dependencies

On Pi 5 we assume that we have a folder:
```
mkdir ~/code
```

Then follow installation steps from source code: https://github.com/bexcite/reachy_mini/blob/develop/docs/SDK/installation.md

Result:
- code pulled to `~/code/reachy_mini`
- `uv` created environment in `~/code/reachy_mini/.venv` (for some reason `uv` want's to use this location if one runs `uv {sync,run}` commands from the repo dir `~/code/reachy_mini`).

NOTE: Some system level deps may require (list below is not exhaustive):
```
sudo apt update
sudo apt install git git-lfs libcairo2-dev pulseaudio
```

## Trying to run `uv run reachy-mini-daemon` and clear all issues

Assuming the `~/code/reachy_mini` repo pulled, and `uv` env in `~/code/reachy_mini/.venv`.

If we try to run `cd ~/code/reachy_mini && uv run reachy-mini-daemon` there are a bunch of
errors that we will try to fix.

I'm leaving this in the form that Claude Code formatted it and not as a one off results.

### 1. Audio Device Access
- Problem: Error querying device -1 - daemon couldn't access audio
- Cause: User reachy not in audio group
- Fix: `sudo usermod -aG audio reachy`

### 2. Camera Access
- Problem: RuntimeError: Camera not found
- Cause: User reachy not in video group
- Fix: `sudo usermod -aG video reachy`

On Pi (/etc/systemd/system/):
```
# /etc/systemd/system/reachy-mini-daemon.service
[Unit]
Description=Reachy Mini Daemon
After=network.target pulseaudio.service
BindsTo=dev-ttyACM0.device
After=dev-ttyACM0.device

[Service]
Type=simple
User=reachy
Group=reachy
WorkingDirectory=/home/reachy/code/reachy_mini
Environment="PATH=/home/reachy/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
ExecStart=/home/reachy/.local/bin/uv run reachy-mini-daemon
Restart=always
RestartSec=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=reachy-mini-daemon

[Install]
WantedBy=multi-user.target
```

### 3. Audio Sharing Between Daemon & Apps
- Problem: ValueError: Not an output device: 'default' when running as systemd service
- Cause: Service couldn't access PulseAudio (no user session)
- Fix: Added `Environment="XDG_RUNTIME_DIR=/run/user/1000"` to `reachy-mini-daemon.service` file

### 4. Daemon Not Exiting on Power Cycle
- Problem: When Reachy Mini power turned off, daemon got stuck instead of restarting
- Cause: Rust panic in `close()` bypassed Python exception handler; `pyo3_runtime.PanicException is BaseException not Exception`
- Fix: Changed except Exception to except BaseException in both:
- src/reachy_mini/daemon/daemon.py - added `os._exit(1)` on backend crash
- src/reachy_mini/daemon/backend/abstract.py - wrapped `close()` in `try/except`

At this point we install and run `reachy_mini_conversation_app`:
- Ensure that `reachy-mini-daemon` is running, then open `http://reachy-brain.local:8000/` and install `reachy_mini_conversation_app` from "Install from 🤗 Hugging Face" section.
- toggle `reachy_mini_conversation_app` to ON state and continue (if you have voice output not working, which is happening from time to time)

On Pi (`/etc/systemd/system/`):
```
# /etc/systemd/system/reachy-conversation-app.service
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

[Install]
WantedBy=multi-user.target
```

### 5. Audio Sharing Between Daemon & Conversation App
- Problem: Conversation app audio not playing - daemon held ALSA device directly, blocking other processes
- Cause: PortAudio compiled without PulseAudio backend (only ALSA/OSS available)
- Diagnosis: `fuser -v /dev/snd/*` showed daemon holding pcmC3D0p directly; PulseAudio sink was SUSPENDED
- Fix:
- Created ~/.asoundrc to set PulseAudio as default ALSA device
- Changed audio_sounddevice.py to use "default" device for output
- Force stereo output (channels=2) since PulseAudio reports 32 max channels

On Pi (`/home/reachy/`):
```
# /home/reachy/.asoundrc
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

On Pi (create file `/home/reachy/start-conversation-app.sh`):
```
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

---
Code Changes (in reachy_mini repo), collected in this branch [here](https://github.com/bexcite/reachy_mini/tree/pb/pi-setup)
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
src/reachy_mini/media/audio_sounddevice.py - Use default device & force stereo:
# In __init__:
self._output_device_id = self._get_device_id(
    ["default"], device_io_type="output"
)

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

## Useful Commands (on Pi)

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
