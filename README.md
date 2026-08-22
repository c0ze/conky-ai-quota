# conky-ai-quota

A Conky desktop widget with a twist: alongside the usual clock / weather /
system stats, it shows **weekly usage-quota bars for AI coding services** —
Claude (Claude Code), Codex (OpenAI), Grok (xAI) and Kimi (Moonshot AI) —
with days-to-reset next to each bar.

Two config variants are included:

- `conky/conky.conf` — classic X11 window (works on KDE Plasma and other X11/XWayland desktops)
- `conky/conky-hyprland.conf` — native **Wayland layer-shell background widget** for Hyprland (no window, sits behind your apps like a proper desktop widget), driven by `conky/start-conky.sh` which picks the right config based on `XDG_CURRENT_DESKTOP`

## Layout of the AI LIMITS section

```
AI LIMITS ─────────────────
Claude:            ▓▓░░░░░  03d
Codex:             ▓▓▓▓░░░  11h
Grok:              ▓░░░░░░  06d
Kimi:              ▓▓░░░░░  06d
```

Each bar is weekly-plan utilization, the text is time until the window
resets (zero-padded `NNd`/`NNh` so the values stay aligned).

## Install

```bash
cp bin/*-quota ~/.local/bin/          # the four quota pollers
cp conky/conky.conf ~/.config/conky/  # and/or conky-hyprland.conf
cp conky/start-conky.sh ~/.config/conky/
cp conky/conky.desktop.example ~/.config/autostart/conky.desktop  # then fix the Exec path inside
```

Requirements:

- `conky` (1.21+; the Hyprland variant needs a build with Wayland support — check `conky --version`)
- `python3` (standard library only)
- The AI CLI tools installed **and logged in** — the pollers reuse their local credentials:
  - `claude` → `~/.claude/.credentials.json`
  - `codex` → `~/.codex/auth.json`
  - `grok` → `~/.grok/auth.json`
  - `kimi` (kimi-code) → `~/.kimi-code/credentials/kimi-code.json`

No API keys to configure, no tokens in the repo: the scripts read the CLI's
own credential files at runtime and cache responses in `~/.cache/<name>-quota.json`.

## Token refresh behavior

- `claude-quota` and `kimi-quota` **self-refresh** their OAuth tokens (and write them back to the credential file).
- `codex-quota` and `grok-quota` **cannot self-refresh** — if the data goes stale, just run `codex` / `grok` once and it recovers.

## Machine-specific bits to adjust

The configs are written for an AMD desktop; tweak these for your machine:

- **Temperatures:** `${hwmon k10temp temp 1}` (AMD CPU) / `${hwmon amdgpu temp 1}` (AMD GPU). On Intel use `coretemp`; on a ThinkPad `${hwmon thinkpad fan 1}` gives fan RPM. See `ls /sys/class/hwmon/hwmon*/name`.
- **GPU VRAM:** reads `/sys/class/drm/card1/device/mem_info_vram_*` — change `card1` to your GPU, or delete the line (e.g. iGPU-only laptops).
- **Network interfaces:** `${addr enp3s0}` etc. — replace with your interface names (`ip -o link`).
- **Weather:** `wttr.in` with no location argument (IP geolocation). Add your city if you prefer: `wttr.in/Tokyo?format=%C+%t`.
- Make sure `~/.local/bin` is in your session `PATH` (the conky configs call the pollers by bare name).

## Hyprland notes

- The Wayland variant uses conky's layer-shell output (`out_to_wayland`, `own_window_type = 'desktop'`), so it renders on the background layer instead of tiling as a window.
- `start-conky.sh` switches configs per session; point your autostart entry at it (see `conky.desktop.example`).
- Conky quirk: `${execibar}` wants bar dimensions **before** the command (`${execibar 300 8,180 cmd}`), otherwise the numbers are passed to the script as arguments.

## License

MIT — see [LICENSE](LICENSE).
