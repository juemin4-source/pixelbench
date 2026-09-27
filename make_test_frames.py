#!/usr/bin/env python3
"""Generate synthetic test frames to verify S5-S7 pipeline before spending GPU."""
import os, math
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_entity')
os.makedirs(OUT, exist_ok=True)

def draw_frame(path, phase, anim):
    """A crude 'knight' block figure with pose varying by phase (0..1)."""
    W, H = 800, 1000
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = 400
    # body bob
    bob = int(12 * math.sin(phase * 2 * math.pi)) if anim != 'atkA' else 0
    head_y = 180 + bob
    body_y = 300 + bob
    leg_spread = int(60 * math.sin(phase * 2 * math.pi)) if anim == 'walk' else 20
    # head (circle)
    d.ellipse([cx - 70, head_y, cx + 70, head_y + 140], fill=(220, 60, 60, 255))
    # body (rect)
    d.rectangle([cx - 90, body_y, cx + 90, body_y + 300], fill=(60, 90, 180, 255))
    # legs
    d.rectangle([cx - 80, body_y + 300, cx - 20, body_y + 560 + leg_spread // 2], fill=(40, 40, 60, 255))
    d.rectangle([cx + 20, body_y + 300, cx + 80, body_y + 560 - leg_spread // 2], fill=(40, 40, 60, 255))
    # sword: swings for atkA
    if anim == 'atkA':
        ang = math.radians(-90 + 180 * phase)
        sx = int(cx + 140 * math.cos(ang))
        sy = int(body_y + 150 + 140 * math.sin(ang))
        d.line([(cx, body_y + 150), (sx, sy)], fill=(200, 200, 220, 255), width=24)
    img.save(path)
    print('wrote', os.path.basename(path))

for i in range(8):
    draw_frame(os.path.join(OUT, 'idle_%03d.png' % i), i / 8, 'idle')
for i in range(8):
    draw_frame(os.path.join(OUT, 'walk_%03d.png' % i), i / 8, 'walk')
for i in range(8):
    draw_frame(os.path.join(OUT, 'atkA_%03d.png' % i), i / 8, 'atkA')
print('24 synthetic frames in', OUT)
