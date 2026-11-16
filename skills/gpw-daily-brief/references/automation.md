# Automating the brief (documentation only)

The skill never installs a schedule. Show the user these options and set
one up only when asked. Pick a time after **18:00 Warsaw time**: the session
ends 17:05 and `gpwlib` treats a day's quotes as final from 18:00.

Replace `SKILL` with the skill folder (`~/.claude/skills/gpw-daily-brief`
for an installed skill, or `<repo>/skills/gpw-daily-brief`) and `REPORTS`
with a folder that keeps the reports (e.g. `~/gpw-reports`). Keep the
folder between runs: yesterday's reports are the "what changed" baseline.

## cron (Linux, macOS)

```cron
# weekdays 18:15 - daily pipeline + brief
15 18 * * 1-5  cd SKILL && /usr/bin/python3 scripts/gpw_daily_brief.py run --output-dir REPORTS >> REPORTS/brief.log 2>&1
# Fridays 18:45 - weekly variant (macro, portfolio risk, post-mortem, dividends)
45 18 * * 5    cd SKILL && /usr/bin/python3 scripts/gpw_daily_brief.py run --weekly --output-dir REPORTS >> REPORTS/brief.log 2>&1
```

cron uses the machine's time zone; if it is not Europe/Warsaw, shift the
hour (some cron implementations accept `CRON_TZ=Europe/Warsaw` as the first
line; macOS cron does not). On Polish holidays the steps simply find no new
session and the brief repeats the last one.

## launchd (macOS)

`~/Library/LaunchAgents/pl.gpw.daily-brief.plist`, then
`launchctl load ~/Library/LaunchAgents/pl.gpw.daily-brief.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>pl.gpw.daily-brief</string>
  <key>WorkingDirectory</key><string>/Users/USER/.claude/skills/gpw-daily-brief</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>scripts/gpw_daily_brief.py</string>
    <string>run</string>
    <string>--output-dir</string><string>/Users/USER/gpw-reports</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>15</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/Users/USER/gpw-reports/brief.log</string>
  <key>StandardErrorPath</key><string>/Users/USER/gpw-reports/brief.log</string>
</dict>
</plist>
```

launchd runs a missed job when the Mac wakes up, which cron does not.

## Claude Code `/schedule` (cloud routine)

In Claude Code, `/schedule` creates a routine that runs a prompt on a cron
schedule in the cloud, e.g.:

```
/schedule weekdays at 18:15 Europe/Warsaw: run gpw-daily-brief
  (python3 scripts/gpw_daily_brief.py run --output-dir REPORTS) and give me
  the "Wymaga uwagi" items first
```

A cloud routine works on the repository checkout, not on the user's local
`~/.gpw-trading-skills` journal, watchlist or archive cache, so positions
and the watchlist are missing there unless the user keeps them in the
repository. For a brief about the user's own positions, prefer local cron or
launchd and let Claude read the newest `gpw_daily_brief_*.md` in the
conversation.

## What to tell the user

- The first run fills the GPW archive cache (20-40 min, once).
- `brief.log` holds the run log (each step's status, exit code, duration).
- The newest `gpw_daily_brief_*.md` in REPORTS is the page to read; the
  `.json` is what Claude reads when asked "what does today's brief say?".
