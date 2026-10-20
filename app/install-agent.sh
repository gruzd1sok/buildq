#!/bin/bash
# Register BuildQBar.app as a login LaunchAgent and (re)start it.
# Usage: install-agent.sh <path/to/BuildQBar.app> <path/to/buildq>   |   install-agent.sh --uninstall
set -euo pipefail
LABEL=io.github.gruzd1sok.buildq.bar
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$PLIST"
  echo "BuildQBar LaunchAgent removed"
  exit 0
fi

APP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
CLI="$2"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.buildq"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/BuildQBar</string></array>
  <key>EnvironmentVariables</key><dict><key>BUILDQ_CLI</key><string>$CLI</string></dict>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardErrorPath</key><string>$HOME/.buildq/bar.err.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "BuildQBar running from $APP (LaunchAgent $LABEL)"
