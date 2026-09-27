#!/usr/bin/env python3
"""第二轮复核修复的专项回归：不连续采纳 / pack 消费 normalized / 归一化居中。"""
import os, sys, json, shutil, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from PIL import Image
import asset_tool as T

BASE = os.environ.get('PB_BASE', 'http://127.0.0.1:8321')
fails = []
def check(name, cond, detail=''):
    print('%s %s %s' % ('PASS' if cond else 'FAIL', name, detail))
    if not cond:
        fails.append(name)

def req(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method or ('POST' if body else 'GET'))
    if data is not None:
        r.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}

# ---------- A. 不连续采纳：服务端必须返回真实槽位 ----------
try: req('/api/projects/gap_test', method='DELETE')
except Exception: pass
req('/api/projects', {'name': 'gap_test'})
E = '/api/projects/gap_test/entities'
req(E, {'name': 'knight'})

def gen(anim, idx):
    _, r = req(E + '/knight/frames/%s/%d/generate' % (anim, idx), {'prompt': 'x'})
    return r.get('task_id')

# 生成 0/1/2 三帧，只采纳 0 和 2（跳过 1）
for i in (0, 1, 2):
    gen('idle', i)
import time
time.sleep(3.0)
for i in (0, 2):
    _, st = req(E + '/knight')
    cands = st['frames']['idle']['candidates'][str(i)]
    code, r = req(E + '/knight/frames/idle/%d/adopt' % i, {'candidate': cands[0]})
    if i == 0:
        check('采纳帧0', code == 200, str(code))

_, st = req(E + '/knight')
fd = st['frames']['idle']
check('adopted 计数 = 2', fd['adopted'] == 2, str(fd['adopted']))
check('adopted_slots = [0,2]（关键是缺 1）', fd.get('adopted_slots') == [0, 2], str(fd.get('adopted_slots')))
check('max_frame 不被误算成 slot 数', fd.get('max_frame') == 2, str(fd.get('max_frame')))

# 前端判定等价逻辑：slotSet 命中才算已采纳
slot_set = set(fd.get('adopted_slots') or [])
front = [i for i in range(fd.get('max_frame', 0) + 1) if i in slot_set]
check('前端只会把 0,2 当已采纳（不再误标 1）', front == [0, 2], str(front))

# 服务端确实只写了两个文件，没有 idle_001.png
d = os.path.join(HERE, 'workspaces', 'gap_test', 'knight')
files = sorted(f for f in os.listdir(d) if f.endswith('.png'))
check('磁盘上无 idle_001.png（验证误标会 404）', 'idle_001.png' not in files, str(files))

# ---------- B. pack 必须优先消费 normalized/ ----------
TMP = os.path.join(HERE, '_probe2')
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
import make_test_frames as M
for i in range(2):
    M.draw_frame(os.path.join(TMP, 'idle_%03d.png' % i), 0.2, 'idle')
os.makedirs(os.path.join(TMP, 'normalized'))
for i in range(2):
    T.normalize(os.path.join(TMP, 'idle_%03d.png' % i), os.path.join(TMP, 'normalized', 'idle_%03d.png' % i))
# 把 normalized 帧改成纯色标记，便于确认 pack 用的是哪一份
for i in range(2):
    Image.new('RGBA', (1024, 1024), (0, 255, 0, 255 if i == 0 else 0)).save(
        os.path.join(TMP, 'normalized', 'idle_%03d.png' % i))
idx = T.pack(TMP, os.path.join(TMP, 'sheets'))
check('pack 声明 source_dir=normalized', idx.get('source_dir') == 'normalized', str(idx.get('source_dir')))
sheet = Image.open(os.path.join(TMP, 'sheets', '%s_idle.png' % os.path.basename(TMP))).convert('RGBA')
px = sheet.getpixel((10, 10))
check('图集像素来自 normalized（绿）', px[:3] == (0, 255, 0), str(px))

# ---------- C. normalize 水平居中 ----------
img = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
for x in range(700, 900):
    for y in range(300, 900):
        img.putpixel((x, y), (255, 0, 0, 255))
src = os.path.join(TMP, 'raw.png')
img.save(src)
r = T.normalize(src, os.path.join(TMP, 'raw_n.png'))
out = Image.open(os.path.join(TMP, 'raw_n.png')).convert('RGBA')
a = np.asarray(out)[:, :, 3]
ys, xs = np.where(a > 16)
cx = (xs.min() + xs.max()) // 2
check('归一化后水平居中（中心≈512）', abs(cx - 512) <= 2, 'cx=%d' % cx)
check('底边落在 ground_y', int(ys.max()) == T.C['ground_y'], 'ymax=%d ground_y=%d' % (int(ys.max()), T.C['ground_y']))
check('foot_y 返回真实底边而非粘贴偏移', r['foot_y'] == int(ys.max()), '%s vs %s' % (r['foot_y'], int(ys.max())))
check('居中后 foot_y 可被找到（中央带内有内容）', T.foot_y(out) is not None, str(T.foot_y(out)))

# ---------- D. foot_y 回退：内容贴左缘时不再误报 None ----------
edge = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
for x in range(0, 150):
    for y in range(200, 900):
        edge.putpixel((x, y), (0, 0, 255, 255))
check('贴左缘素材 foot_y 不再为 None', T.foot_y(edge) == 899, str(T.foot_y(edge)))
check('fallback=False 时仍如实返回 None', T.foot_y(edge, fallback=False) is None, str(T.foot_y(edge, fallback=False)))

shutil.rmtree(TMP, ignore_errors=True)
print('\n%s  (%d failed)' % ('ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails), len(fails)))
sys.exit(1 if fails else 0)
