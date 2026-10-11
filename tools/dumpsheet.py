"""Contact sheet of PS2X_GS_DUMP frames: one 320x224 tile per dump, labelled with its tick
(a dump with nothing presented, presented=0x0 and no .rgba, gets a dark "no frame" tile).
    python tools/dumpsheet.py <dump dir> <out.png>
"""
import sys, re, glob, os
import numpy as np
from PIL import Image, ImageDraw

d = sys.argv[1]
out = sys.argv[2]
tiles = []
for txt in sorted(glob.glob(os.path.join(d, 'gsdump_*.txt')), key=lambda p: int(re.findall(r'(\d+)', os.path.basename(p))[0])):
    tick = int(re.findall(r'(\d+)', os.path.basename(txt))[0])
    m = re.search(r'presented=(\d+)x(\d+)', open(txt).read())
    w, h = (int(m.group(1)), int(m.group(2))) if m else (640, 448)
    rgba = txt[:-4] + '.rgba'
    if w and h and os.path.exists(rgba):
        raw = np.fromfile(rgba, dtype=np.uint8).reshape(512, 640, 4)
        img = Image.fromarray(raw[:h, :w, :3].copy()).resize((320, 224))
    else:  # nothing presented at that tick (display not set up yet): no .rgba
        img = Image.new('RGB', (320, 224), (16, 16, 48))
        ImageDraw.Draw(img).text((110, 106), 'no frame presented', fill=(200, 200, 200))
    ImageDraw.Draw(img).text((4, 4), str(tick), fill=(255, 0, 255))
    tiles.append(img)
cols = 5
rows = (len(tiles) + cols - 1) // cols
sheet = Image.new('RGB', (cols * 322, rows * 226), (40, 40, 40))
for i, t in enumerate(tiles):
    sheet.paste(t, ((i % cols) * 322, (i // cols) * 226))
sheet.save(out)
print(len(tiles), 'tiles ->', out)
