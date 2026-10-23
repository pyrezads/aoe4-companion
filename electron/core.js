// Runs the Python core (main.py --core) and talks to it: one JSON message per line
// on its stdout, commands as JSON lines on its stdin. Logs arrive on stderr.

const { spawn } = require('child_process');
const path = require('path');
const readline = require('readline');
const fs = require('fs');

const ALLOWED = new Set(['list_builds', 'use_build', 'auto_build', 'coach_skip', 'set_monitor', 'reading_check']);

class Core {
  constructor({ root, configPath, onMessage, onState }) {
    this.root = root;
    this.configPath = configPath;
    this.onMessage = onMessage;
    this.onState = onState;
    this.proc = null;
    this.running = false;
    this.error = null;
    this.log = [];
    this.queue = [];          // commands sent before the core said "ready"
    this.ready = false;
    this.wanted = false;
  }

  // Packaged app: resources/core/aoe4-core.exe (added with the installer step).
  // Development: python on PATH running main.py. AOE4_PYTHON overrides the interpreter.
  command() {
    const exe = process.resourcesPath && path.join(process.resourcesPath, 'core', 'aoe4-core.exe');
    if (exe && fs.existsSync(exe)) return [[exe, []]];
    const script = path.join(this.root, 'main.py');
    const pythons = process.env.AOE4_PYTHON ? [process.env.AOE4_PYTHON]
      : process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
    return pythons.map((p) => [p, [script]]);
  }

  start() {
    this.wanted = true;
    this.tryStart(this.command());
  }

  tryStart(candidates) {
    if (!candidates.length) return this.fail('Python not found. Install Python 3 or set AOE4_PYTHON.');
    const [cmd, pre] = candidates[0];
    // AOE4_CORE_ARGS: extra arguments for testing, e.g. "--offline --replay calib/s02.png,calib/s07.png"
    const extra = (process.env.AOE4_CORE_ARGS || '').split(' ').filter(Boolean);
    const args = [...pre, '--core', '--config', this.configPath, ...extra];
    let proc;
    try {
      proc = spawn(cmd, args, { cwd: this.root, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
        env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
    } catch (e) {
      return this.tryStart(candidates.slice(1));
    }
    this.proc = proc;
    this.ready = false;
    let started = false;

    proc.on('error', (e) => {                     // e.g. ENOENT: this python doesn't exist
      if (!started && e.code === 'ENOENT') return this.tryStart(candidates.slice(1));
      this.fail(e.message);
    });
    readline.createInterface({ input: proc.stdout }).on('line', (line) => {
      let msg;
      try { msg = JSON.parse(line); } catch { return this.addLog(line); }
      if (msg.type === 'ready') {
        started = true;
        this.ready = true;
        this.running = true;
        this.error = null;
        this.emitState();
        for (const c of this.queue.splice(0)) this.write(c);
      }
      this.onMessage(msg.type, msg.data);
    });
    readline.createInterface({ input: proc.stderr }).on('line', (line) => this.addLog(line));
    proc.on('exit', (code) => {
      if (this.proc !== proc) return;            // an old process we replaced
      this.proc = null;
      this.running = this.ready = false;
      if (this.wanted) this.fail(code === 0 ? 'The Python part stopped.' : `The Python part stopped (code ${code}). ${this.lastError()}`);
      else this.emitState();
    });
  }

  addLog(line) {
    console.log('[core]', line);
    this.log.push(line);
    if (this.log.length > 40) this.log.shift();
  }

  lastError() {
    const err = [...this.log].reverse().find((l) => /Error|error|No module/.test(l));
    return err ? err.trim() : '';
  }

  fail(message) {
    this.running = false;
    this.error = message;
    this.emitState();
  }

  emitState() {
    this.onState({ running: this.running, error: this.error });
  }

  write(cmd) {
    if (this.proc && this.proc.stdin.writable) this.proc.stdin.write(JSON.stringify(cmd) + '\n');
  }

  send(cmd) {
    if (!cmd || !ALLOWED.has(cmd.cmd)) return false;
    if (this.ready) this.write(cmd);
    else this.queue.push(cmd);
    return true;
  }

  stop() {
    this.wanted = false;
    if (this.proc) {
      this.write({ cmd: 'quit' });
      const p = this.proc;
      setTimeout(() => { try { p.kill(); } catch {} }, 1500);
      this.proc = null;
    }
    this.running = this.ready = false;
  }

  restart() {
    this.stop();
    this.emitState();                             // pages see it stop, then start again
    this.queue = [];
    setTimeout(() => this.start(), 300);
  }
}

module.exports = { Core };
