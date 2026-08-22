#!/usr/bin/env bash
# Start conky with the right config per session:
# - Hyprland (omarchy): native Wayland layer-shell background widget
# - everything else (KDE Plasma): classic X11 window config
if [ "${XDG_CURRENT_DESKTOP:-}" = "Hyprland" ]; then
    exec conky --daemonize --pause=5 --config "$HOME/.config/conky/conky-hyprland.conf"
else
    exec env LC_ALL=en_US.UTF-8 conky --daemonize --pause=5
fi
