"""
labeler/label_server.py
Single-file captcha label-entry web app for the IRICA captcha (ENGLISH).

Unlike the hamta labeler, there is NO number->words conversion: the IRICA
captcha is a raw lowercase alphanumeric string (a-z + 0-9, exactly 5 chars), so
you just type exactly what you see and it's stored verbatim.

Run (no install needed — Python serves it):
    python labeler/label_server.py
then open:  http://localhost:8001

Output CSV: ../captchas_irica/labels_irica.csv  (columns: filename,text)
The port is 8001 so it can run alongside the hamta labeler (which uses 8000).
"""
from __future__ import annotations
import csv
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --------------------------------------------------------------------------- #
# Config — paths default to the collected IRICA captchas.
# --------------------------------------------------------------------------- #
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE.parent.parent / "captchas_irica"     # ai_projects/irica/captchas_irica
OUT_CSV = RAW_DIR / "labels_irica.csv"              # output: filename,text
FILE_GLOB = "captcha_*.png"
PORT = 8001

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
CAPTCHA_LEN = 5


def normalize(raw: str) -> str:
    """Lowercase, keep only alphabet chars. Returns the cleaned string."""
    return "".join(ch for ch in raw.strip().lower() if ch in ALPHABET)


def is_valid(text: str) -> bool:
    return len(text) == CAPTCHA_LEN and all(c in ALPHABET for c in text)


# --------------------------------------------------------------------------- #
# CSV read/write (filename -> text)
# --------------------------------------------------------------------------- #
def load_labels() -> dict:
    out = {}
    if OUT_CSV.is_file():
        with open(OUT_CSV, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for row in reader:
                if len(row) >= 2:
                    out[row[0]] = row[1]
    return out


def save_labels(labels: dict) -> None:
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filename", "text"])
        for fname, text in labels.items():
            w.writerow([fname, text])


def list_files() -> list:
    files = sorted(
        RAW_DIR.glob(FILE_GLOB),
        key=lambda p: [int(t) if t.isdigit() else t.lower()
                       for t in re.split(r"(\d+)", p.name)],
    )
    return [p.name for p in files]


# --------------------------------------------------------------------------- #
# HTML page
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IRICA captcha labeler</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --fg:#e8e8ea; --accent:#4da3ff;
          --ok:#3ecf7a; --bad:#ff6b6b; --muted:#8b909a; --border:#2a2e37; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Segoe UI', Tahoma, sans-serif; background:var(--bg);
         color:var(--fg); display:flex; min-height:100vh; align-items:center;
         justify-content:center; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:16px;
          padding:28px 32px; width:min(560px, 92vw); box-shadow:0 8px 40px rgba(0,0,0,.4); }
  h1 { font-size:18px; margin:0 0 4px; font-weight:600; }
  .sub { color:var(--muted); font-size:13px; margin:0 0 14px; }
  .bar { height:8px; background:#000; border-radius:99px; overflow:hidden; }
  .bar > div { height:100%; background:linear-gradient(90deg,#3ecf7a,#4da3ff);
               width:0%; transition:width .3s; }
  .stat { color:var(--muted); font-size:13px; margin-top:6px; display:flex;
          justify-content:space-between; }
  .imgbox { background:#e8e8e8; border-radius:10px; padding:14px; text-align:center;
            min-height:120px; display:flex; align-items:center; justify-content:center;
            margin-top:16px; }
  .imgbox img { max-width:100%; }
  .fname { text-align:center; color:var(--muted); font-size:12px; margin:8px 0 16px;
           direction:ltr; font-family:monospace; }
  input[type=text] { width:100%; font-size:30px; text-align:center; padding:12px;
                     border-radius:10px; border:2px solid var(--border);
                     background:#0d0f13; color:#fff; letter-spacing:6px; direction:ltr;
                     font-family:monospace; text-transform:lowercase; }
  input[type=text]:focus { outline:none; border-color:var(--accent); }
  input.bad { border-color:var(--bad); }
  input.ok  { border-color:var(--ok); }
  .hint { text-align:center; font-size:13px; margin:10px 0 4px; min-height:18px; }
  .hint.bad { color:var(--bad); }
  .hint.ok  { color:var(--ok); }
  .btns { display:flex; gap:10px; margin-top:12px; }
  button { flex:1; padding:12px; border:none; border-radius:10px; font-size:15px;
           cursor:pointer; background:var(--accent); color:#fff; font-weight:600; }
  button.ghost { background:transparent; border:1px solid var(--border); color:var(--muted); }
  .done { text-align:center; padding:40px 0; font-size:20px; color:var(--ok); }
  kbd { background:#000; border:1px solid var(--border); border-radius:5px;
        padding:1px 6px; font-size:12px; color:var(--muted); }
</style>
</head>
<body>
<div class="card">
  <h1>IRICA captcha labeler</h1>
  <p class="sub">Type the 5 characters you see (lowercase a-z / 0-9), then
     <kbd>Enter</kbd>. <kbd>Esc</kbd> skips.</p>
  <div class="bar"><div id="fill"></div></div>
  <div class="stat"><span id="doneTxt"></span><span id="remTxt"></span></div>

  <div id="work">
    <div class="imgbox"><img id="cap" alt="captcha"></div>
    <div class="fname" id="fname"></div>
    <input id="inp" type="text" maxlength="5" autocomplete="off" autocapitalize="off"
           autocorrect="off" spellcheck="false">
    <div class="hint" id="hint">&nbsp;</div>
    <div class="btns">
      <button id="save">Save &amp; next</button>
      <button class="ghost" id="skip">Skip</button>
    </div>
  </div>
  <div id="finished" class="done" style="display:none">✓ All images labeled. Nice.</div>
</div>

<script>
const REMAINING = __REMAINING__;
const PREFILL   = __PREFILL__;
let done = __DONE__;
const total = __TOTAL__;
let queue = REMAINING.slice();

const $ = id => document.getElementById(id);
const ALPHA = /^[a-z0-9]*$/;

function refreshStat(){
  $('doneTxt').textContent = 'labeled: ' + done + ' / ' + total;
  $('remTxt').textContent  = 'remaining: ' + queue.length;
  $('fill').style.width = (total ? (done/total*100) : 0) + '%';
}

function current(){ return queue[0]; }

function show(){
  if(queue.length === 0){
    $('work').style.display = 'none';
    $('finished').style.display = 'block';
    refreshStat();
    return;
  }
  const f = current();
  $('cap').src = '?img=' + encodeURIComponent(f) + '&t=' + Date.now();
  $('fname').textContent = f;
  const inp = $('inp');
  inp.value = (PREFILL && PREFILL[f]) ? PREFILL[f] : '';
  inp.className = '';
  validate();
  refreshStat();
  inp.focus();
}

function validate(){
  const v = $('inp').value.toLowerCase();
  const inp = $('inp'), hint = $('hint');
  if(!ALPHA.test(v)){ inp.className='bad'; hint.className='hint bad';
    hint.textContent='only a-z and 0-9'; return false; }
  if(v.length !== 5){ inp.className=''; hint.className='hint';
    hint.textContent = v.length + '/5'; return false; }
  inp.className='ok'; hint.className='hint ok'; hint.textContent='ok — press Enter';
  return true;
}

async function save(){
  const f = current();
  const text = $('inp').value.toLowerCase();
  if(!validate()) return;
  const res = await fetch('/save', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'filename='+encodeURIComponent(f)+'&text='+encodeURIComponent(text)});
  const j = await res.json();
  if(j.ok){ done = j.done; queue.shift(); show(); }
  else { $('hint').className='hint bad'; $('hint').textContent = j.error || 'error'; }
}

function skip(){ queue.push(queue.shift()); show(); }

$('inp').addEventListener('input', e => { e.target.value = e.target.value.toLowerCase(); validate(); });
$('inp').addEventListener('keydown', e => {
  if(e.key === 'Enter'){ e.preventDefault(); save(); }
  else if(e.key === 'Escape'){ e.preventDefault(); skip(); }
});
$('save').addEventListener('click', save);
$('skip').addEventListener('click', skip);
show();
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "img" in qs:
            name = Path(qs["img"][0]).name          # prevent path traversal
            path = RAW_DIR / name
            if path.is_file():
                self._send(200, path.read_bytes(), "image/png")
            else:
                self._send(404, "not found", "text/plain")
            return
        files = list_files()
        labels = load_labels()
        remaining = [f for f in files if f not in labels]
        prefill = {}
        pf = RAW_DIR / "prefill.json"
        if pf.is_file():
            try:
                prefill = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                prefill = {}
        html = (PAGE
                .replace("__REMAINING__", json.dumps(remaining, ensure_ascii=False))
                .replace("__PREFILL__", json.dumps(prefill, ensure_ascii=False))
                .replace("__DONE__", str(len(labels)))
                .replace("__TOTAL__", str(len(files))))
        self._send(200, html)

    def do_POST(self):
        if urlparse(self.path).path.rstrip("/") != "/save":
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        form = parse_qs(raw)
        fname = Path(form.get("filename", [""])[0]).name
        text = normalize(form.get("text", [""])[0])
        if not fname or not is_valid(text):
            self._send(200, json.dumps({"ok": False,
                       "error": f"need exactly {CAPTCHA_LEN} chars a-z/0-9"}),
                       "application/json; charset=utf-8")
            return
        labels = load_labels()
        labels[fname] = text
        save_labels(labels)
        self._send(200, json.dumps({"ok": True, "filename": fname,
                                    "text": text, "done": len(labels)}),
                   "application/json; charset=utf-8")

    def log_message(self, *args):
        pass  # quiet


def main():
    if not RAW_DIR.is_dir():
        raise SystemExit(f"captcha folder not found: {RAW_DIR}")
    n_raw = len(list_files())
    n_done = len(load_labels())
    print(f"captcha folder : {RAW_DIR}")
    print(f"raw images     : {n_raw}   |   labeled so far: {n_done}")
    print(f"output CSV     : {OUT_CSV}")
    print(f"\n>>> open your browser:  http://localhost:{PORT}\n"
          f"    (stop with Ctrl+C)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
