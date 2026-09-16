#!/usr/bin/env bash
# install_env.sh - rebuild the whole software-GL dev/proof environment on a
# bare Debian box (or a fresh sandbox: system state does not survive a
# snapshot, but the engine lives in /opt and the packages in apt).
#
#   sudo bash install_env.sh
#
# Installs:
#   * the headless compositor stack (sway headless + pixman, Xwayland,
#     pulseaudio) and the Mesa llvmpipe software GL,
#   * xvfb/ffmpeg for the timeline/verify pipeline,
#   * UPBGE 0.50 (linux x64) into /opt/upbge-0.50-linux-x64,
#   * the 3D-audio segfault patch (tools/patch_upbge_audio3d.py),
#   * a 6 G swapfile (editor + llvmpipe + game share one process and a
#     2 GB box OOMs the game start otherwise).
set -eu
PKGS="sway wlr-randr xwayland pulseaudio libpulse0 libgl1-mesa-dri \
mesa-utils x11-xserver-utils xvfb ffmpeg curl ca-certificates"
echo "==> apt packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS

UPBGE_DIR=/opt/upbge-0.50-linux-x64
if [ ! -x "$UPBGE_DIR/blender" ]; then
  echo "==> downloading UPBGE 0.50 (~408 MB) into /opt"
  sudo bash -c 'set -e; cd /opt && \
    curl -sSL -o upbge.tar.xz \
      https://github.com/UPBGE/upbge/releases/download/v0.50/upbge-0.50-linux-x64.tar.xz \
      && tar xf upbge.tar.xz && rm upbge.tar.xz'
fi
echo "==> patching 3D-audio startup (segfault fix, reversible)"
python3 tools/patch_upbge_audio3d.py "$UPBGE_DIR"

if ! swapon --show | grep -q .; then
  echo "==> adding 6 G swapfile"
  sudo fallocate -l 6G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
fi

echo "==> OK: $( "$UPBGE_DIR/blender" --version 2>/dev/null | head -1 )"
echo "next:  sh tools/run_sway_capture.sh talking_robots_capture.blend"
