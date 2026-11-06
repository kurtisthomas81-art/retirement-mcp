#!/usr/bin/env python3
"""Screenshots for KVibeOS (PROTECTED; stdlib only).

  screenshot.py --suite <suite>[@MARKER] --out file.png [--allow-blank]
      Boot the ISO via `tools/qemu-run.sh shot <suite>`, wait for MARKER on serial
      (default KVIBE-SCREEN-READY), QMP-screendump to PNG, quit QEMU.
      Fails if the image is a single colour (unless --allow-blank).
      The build agent can open the PNG to check the UI visually.

  screenshot.py --contact-sheet out.html [--title T] a.png b.png ...
      Self-contained HTML page of screenshots for a human to skim.
"""
import argparse, base64, html, json, os, socket, struct, subprocess, sys, time, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, os.environ.get("LOGS", "build/logs"))
VMDIR = os.path.join(ROOT, os.environ.get("VMDIR", "build/vm"))


def png_pixels(path):
    """Decode an 8-bit RGB/RGBA PNG. Returns (width, height, rows-of-tuples)."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype not in (2, 6):
                raise ValueError(f"unsupported PNG depth={depth} type={ctype}")
            bpp = 3 if ctype == 2 else 4
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride, prev, rows, i = w * bpp, bytearray(w * bpp), [], 0
    for _ in range(h):
        f, line = raw[i], bytearray(raw[i + 1:i + 1 + stride]); i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x:x + 3]) for x in range(0, stride, bpp)])
        prev = line
    return w, h, rows


def distinct_colours(path, limit=64):
    _, _, rows = png_pixels(path)
    seen = set()
    for row in rows[::4]:
        seen.update(row[::4])
        if len(seen) >= limit:
            break
    return len(seen)


class QMP:
    def __init__(self, path, deadline):
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX); self.s.connect(path); break
            except OSError:
                if time.time() > deadline: raise TimeoutError("QMP socket never appeared")
                time.sleep(0.05)
        self.f = self.s.makefile("rw")
        json.loads(self.f.readline())  # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n"); self.f.flush()
        while True:
            msg = json.loads(self.f.readline())
            if "return" in msg or "error" in msg:
                if "error" in msg: raise RuntimeError(msg["error"])
                return msg["return"]


def shoot(spec, out, allow_blank, timeout=25.0):
    suite, _, marker = spec.partition("@")
    marker = marker or "KVIBE-SCREEN-READY"
    sock = os.path.join(VMDIR, f"qmp-{suite}.sock")
    serial = os.path.join(LOGS, f"serial-shot-{suite}.log")
    for p in (sock, serial):
        if os.path.exists(p): os.unlink(p)
    proc = subprocess.Popen([os.path.join(ROOT, "tools/qemu-run.sh"), "shot", suite], cwd=ROOT)
    deadline = time.time() + timeout
    try:
        qmp = QMP(sock, deadline)
        while marker not in (open(serial, errors="replace").read() if os.path.exists(serial) else ""):
            if proc.poll() is not None: raise RuntimeError(f"QEMU exited ({proc.returncode}) before {marker!r}")
            if time.time() > deadline: raise TimeoutError(f"marker {marker!r} not seen on serial")
            time.sleep(0.05)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        qmp.cmd("screendump", filename=os.path.abspath(out), format="png")
        try: qmp.cmd("quit")
        except Exception: pass
    finally:
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill()
    n = distinct_colours(out)
    print(f"{out}: {n} distinct colour(s) sampled")
    if n < 2 and not allow_blank:
        print("screen is blank (single colour)", file=sys.stderr); return 1
    return 0


def contact_sheet(out, title, pngs):
    cards = []
    for p in pngs:
        b64 = base64.b64encode(open(p, "rb").read()).decode()
        cards.append(f'<figure><img src="data:image/png;base64,{b64}" alt="{html.escape(os.path.basename(p))}">'
                     f'<figcaption>{html.escape(os.path.basename(p))}</figcaption></figure>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#f6f6f4;--fg:#1d1d1b;--card:#fff;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecece8;--card:#222;--line:#333}}}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 16px}} main{{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}}
figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
img{{display:block;width:100%;height:auto;image-rendering:pixelated}} figcaption{{padding:8px 12px;font-size:13px}}
</style></head><body><h1>{html.escape(title)}</h1><main>{''.join(cards) or '<p>No screenshots.</p>'}</main></body></html>"""
    open(out, "w").write(page)
    print(out)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite"); ap.add_argument("--out"); ap.add_argument("--allow-blank", action="store_true")
    ap.add_argument("--contact-sheet"); ap.add_argument("--title", default="KVibeOS screenshots")
    ap.add_argument("pngs", nargs="*")
    a = ap.parse_args()
    if a.contact_sheet:
        return contact_sheet(a.contact_sheet, a.title, [p for p in a.pngs if os.path.exists(p)])
    if not (a.suite and a.out): ap.error("--suite and --out required")
    return shoot(a.suite, a.out, a.allow_blank)


if __name__ == "__main__":
    sys.exit(main())
