#!/usr/bin/env python3
"""art_tools: S5 normalize / S6 pack / S7 validate for HD-paint 2D character assets.
CLI:
  python asset_tool.py normalize  <frame.png> [-o out.png]
  python asset_tool.py pack       <entity_dir> [-o outdir]
  python asset_tool.py validate   <entity_dir>
  python asset_tool.py report     <entity_dir>
"""
import argparse, json, os, sys, re, math
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(HERE, 'spec.json'), encoding='utf-8'))
C = SPEC['canvas']
V = SPEC['validation']

def alpha_bboxes(img):
    a = np.asarray(img.convert('RGBA'))[:, :, 3]
    ys, xs = np.where(a > 16)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()

def foot_y(img, fallback=True):
    """Bottom-center anchor: lowest opaque pixel within central 40% column band.

    中央带没内容时（例如角色贴着画布左缘）回退到整幅图的最低不透明像素，
    否则会对完全正常的帧误报 "no central foot anchor"。
    """
    a = np.asarray(img.convert('RGBA'))[:, :, 3]
    w = a.shape[1]
    lo, hi = int(w * 0.3), int(w * 0.7)
    ys, _ = np.where(a[:, lo:hi] > 16)
    if len(ys) == 0:
        if not fallback:
            return None
        ys, _ = np.where(a > 16)
        if len(ys) == 0:
            return None
    return int(ys.max())

def normalize(path, out=None):
    """Crop to content with margin, align feet to ground_y, pad to canvas."""
    img = Image.open(path).convert('RGBA')
    bb = alpha_bboxes(img)
    if not bb:
        raise ValueError('empty frame')
    x0, y0, x1, y1 = bb
    crop = img.crop((x0, y0, x1 + 1, y1 + 1))
    fw, fh = C['frame_w'], C['frame_h']
    out_img = Image.new('RGBA', (fw, fh), (0, 0, 0, 0))
    # 底边对齐 ground_y
    paste_y = C['ground_y'] - (y1 - y0)
    if paste_y < 0: paste_y = 0
    # 水平居中（左对齐会让脚底锚点落在中央列带之外，且多帧播放时角色会横向漂移）
    paste_x = max(0, (fw - (x1 - x0 + 1)) // 2)
    out_img.paste(crop, (paste_x, paste_y), crop)
    out = out or os.path.splitext(path)[0] + '_n.png'
    out_img.save(out)
    return {'src': os.path.basename(path), 'out': out,
            'content_wh': (x1 - x0 + 1, y1 - y0 + 1),
            'paste': (paste_x, paste_y), 'foot_y': paste_y + (y1 - y0)}

def silhouette(img):
    a = np.asarray(img.convert('RGBA'))[:, :, 3]
    return (a > 16).astype(np.uint8)

def iou(a, b):
    inter = int(np.logical_and(a, b).sum())
    union = int(np.logical_or(a, b).sum())
    return inter / union if union else 1.0

def frames_of(entity_dir):
    """Group animation frames: <anim>_NNN.png. Returns {anim: [paths sorted]}."""
    out = {}
    for f in sorted(os.listdir(entity_dir)):
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)_(\d{2,4})\.png$', f)
        if m:
            out.setdefault(m.group(1), []).append(os.path.join(entity_dir, f))
    for k in out:
        out[k] = sorted(out[k], key=lambda p: int(re.search(r'_(\d{2,4})\.png$', p).group(1)))
    return out

def pack(entity_dir, outdir=None):
    """One sheet per animation: frames left-to-right, one row (wrap if over max_w).

    优先打包 normalized/ 下的 S5 产物；没有才退回实体根目录的原始采纳帧。
    """
    outdir = outdir or os.path.join(entity_dir, 'sheets')
    os.makedirs(outdir, exist_ok=True)
    norm = os.path.join(entity_dir, 'normalized')
    src_dir = norm if (os.path.isdir(norm) and frames_of(norm)) else entity_dir
    frames = frames_of(src_dir)
    idx = {'entity': os.path.basename(os.path.normpath(entity_dir)),
           'source_dir': os.path.basename(src_dir), 'canvas': C, 'animations': {}}
    for anim, paths in sorted(frames.items()):
        n = len(paths)
        cols = min(n, max(1, C['sheet_max_w'] // C['frame_w']))
        rows = math.ceil(n / cols)
        sheet = Image.new('RGBA', (cols * C['frame_w'], rows * C['frame_h']), (0, 0, 0, 0))
        cell = []
        for i, p in enumerate(paths):
            img = Image.open(p).convert('RGBA')
            if img.size != (C['frame_w'], C['frame_h']):
                # assume already normalized; letterbox defensively
                canvas = Image.new('RGBA', (C['frame_w'], C['frame_h']), (0, 0, 0, 0))
                canvas.paste(img, (0, 0), img)
                img = canvas
            x = (i % cols) * C['frame_w']
            y = (i // cols) * C['frame_h']
            sheet.paste(img, (x, y), img)
            cell.append({'file': os.path.basename(p), 'frame': i, 'x': x, 'y': y,
                         'w': C['frame_w'], 'h': C['frame_h']})
        sp = os.path.join(outdir, '%s_%s.png' % (idx['entity'], anim))
        sheet.save(sp)
        idx['animations'][anim] = {'sheet': os.path.relpath(sp, outdir),
                                   'frames': n, 'fps': SPEC['entity_template'].get('fps', 12),
                                   'cells': cell}
        print('packed %-12s %2d frames -> %s' % (anim, n, os.path.basename(sp)))
    jp = os.path.join(outdir, '%s_index.json' % idx['entity'])
    json.dump(idx, open(jp, 'w', encoding='utf-8'), indent=1)
    print('index ->', jp)
    return idx

def validate(entity_dir):
    """S7: foot anchor stability, opacity range, silhouette drift, frame order."""
    frames = frames_of(entity_dir)
    report = {'entity': os.path.basename(os.path.normpath(entity_dir)), 'animations': {}, 'ok': True}
    for anim, paths in sorted(frames.items()):
        sigs = []
        probs = []
        for p in paths:
            img = Image.open(p).convert('RGBA')
            a = np.asarray(img)[:, :, 3]
            ratio = float((a > 16).sum()) / a.size
            if ratio < V['min_opaque_ratio'] or ratio > V['max_opaque_ratio']:
                probs.append('%s opacity %.3f out of range' % (os.path.basename(p), ratio))
            fy = foot_y(img)
            sigs.append(silhouette(img))
            if fy is None:
                probs.append('%s no central foot anchor' % os.path.basename(p))
        # silhouette drift vs first frame
        base = sigs[0]
        for i in range(1, len(paths)):
            v = iou(base, sigs[i])
            if v < V['silhouette_min_iou']:
                probs.append('%s silhouette IoU vs frame0 = %.2f < %.2f'
                             % (os.path.basename(paths[i]), v, V['silhouette_min_iou']))
        report['animations'][anim] = {'frames': len(paths), 'problems': probs}
        if probs:
            report['ok'] = False
    return report

def report(entity_dir):
    r = validate(entity_dir)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['normalize', 'pack', 'validate', 'report'])
    ap.add_argument('path')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    if a.cmd == 'normalize':
        print(json.dumps(normalize(a.path, a.out), ensure_ascii=False))
    elif a.cmd == 'pack':
        pack(a.path, a.out)
    elif a.cmd == 'validate':
        ok = validate(a.path)
        print('VALIDATION %s' % ('PASS' if ok['ok'] else 'FAIL'))
        for anim, d in ok['animations'].items():
            for p in d['problems']:
                print('  %-10s %s' % (anim, p))
    elif a.cmd == 'report':
        report(a.path)

if __name__ == '__main__':
    main()
