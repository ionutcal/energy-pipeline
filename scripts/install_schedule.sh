#!/usr/bin/env bash
# Installs (or removes) a macOS launchd job that runs scripts/run_daily_local.sh
# every day at 06:00. If the Mac is asleep at that time, launchd runs the job
# when it wakes up.
#
#   scripts/install_schedule.sh            install or update the job
#   scripts/install_schedule.sh --remove   remove it
set -euo pipefail

label="com.energy-pipeline.daily"
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

# Unload any previous version first, so the job is never registered twice.
launchctl bootout "$domain/$label" 2>/dev/null || true

if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$plist"
    echo "Removed $label"
    exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$project_dir/logs"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$project_dir/scripts/run_daily_local.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <!-- Output that happens before the script's own log is opened. -->
    <key>StandardErrorPath</key>
    <string>$project_dir/logs/launchd.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$plist" >/dev/null
launchctl bootstrap "$domain" "$plist"
echo "Installed $label: daily at 06:00, logs in $project_dir/logs/"
echo "Run it now with: launchctl kickstart $domain/$label"
