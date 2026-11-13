'use strict';
// Starts the server at login and keeps it running (a macOS launchd job).
//
//   npm run autostart-on     write the job file and switch it on
//   npm run autostart-off    switch it off and remove the job file
//
// The server's output goes to data/server.log.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const c = require('./common');

const LABEL = 'local.recomender';
const plist = path.join(os.homedir(), 'Library', 'LaunchAgents', LABEL + '.plist');
const domain = `gui/${process.getuid()}`;

function launchctl(...args) {
  const r = spawnSync('launchctl', args, { encoding: 'utf8' });
  return { ok: r.status === 0, text: (r.stdout + r.stderr).trim() };
}

function on() {
  const cfg = c.loadConfig();
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${esc(process.execPath)}</string>
    <string>${esc(path.join(cfg.root, 'src', 'server.js'))}</string>
  </array>
  <key>WorkingDirectory</key><string>${esc(cfg.root)}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>${esc(cfg.logPath)}</string>
  <key>StandardErrorPath</key><string>${esc(cfg.logPath)}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${esc(path.dirname(process.execPath))}:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
`;
  fs.mkdirSync(path.dirname(plist), { recursive: true });
  launchctl('bootout', `${domain}/${LABEL}`); // in case it is already on
  fs.writeFileSync(plist, xml);
  const r = launchctl('bootstrap', domain, plist);
  if (!r.ok) {
    console.error('Could not switch the job on: ' + r.text);
    process.exit(1);
  }
  console.log(`Autostart is ON. The server runs now and after every login: http://localhost:${cfg.port}`);
  console.log(`Job file: ${plist}`);
  console.log(`Log: ${cfg.logPath}`);
}

function off() {
  const r = launchctl('bootout', `${domain}/${LABEL}`);
  fs.rmSync(plist, { force: true });
  console.log(r.ok ? 'Autostart is OFF. The server was stopped.' : 'Autostart was not on. The job file is removed.');
}

const cmd = process.argv[2];
if (cmd === 'on') on();
else if (cmd === 'off') off();
else console.log('Use: node src/autostart.js on   or   node src/autostart.js off');
