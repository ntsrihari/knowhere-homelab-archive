#!/bin/sh
# Use Home Assistant's TV key (already allowed on the TV), keep adb connected, run the viewer.
mkdir -p /root/.android && cp /keys/adbkey /root/.android/adbkey && chmod 600 /root/.android/adbkey
adb start-server
(while true; do adb connect "$TV" >/dev/null 2>&1; sleep 20; done) &
exec node index.js
