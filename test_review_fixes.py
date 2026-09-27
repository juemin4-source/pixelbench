#!/usr/bin/env python3
"""回归测试：验证代码审查修复项（安全加固 + 采纳链路）。"""
import json, time, urllib.request, urllib.error, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE = os.environ.get('PB_BASE', 'http://127.0.0.1:8321')

def req(path, data=None, method=None, raw=None, headers=None):
    body = json.dumps(data).encode() if data is not None else raw
    m = method or ('POST' if body else 'GET')
    r = urllib.request.Request(BASE + path, data=body, method=m)
    if data is not None:
        r.add_header('Content-Type', 'application/json')
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            b = resp.read()
            if resp.headers.get('Content-Type', '').startswith('application/zip'):
                return resp.status, b
            return resp.status, json.loads(b)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}

fails = []
def check(name, cond, detail=''):
    print('%s %s %s' % ('PASS' if cond else 'FAIL', name, detail))
    if not cond:
        fails.append(name)

# 准备项目/实体
try: req('/api/projects/sec_test', method='DELETE')
except Exception: pass
req('/api/projects', {'name': 'sec_test'})
E = '/api/projects/sec_test/entities'
req(E, {'name': 'knight'})

# --- 1. 未定义动画名被拒 ---
st, r = req(E + '/knight/frames/evil/0/generate', {'prompt': 'x'})
check('未定义动画被拒', st == 400 and 'unknown animation' in str(r), '%s %s' % (st, r))

# --- 2. seed 非整数被拒（不产生 500） ---
st, r = req(E + '/knight/frames/idle/0/generate', {'prompt': 'x', 'seed': 'abc'})
check('seed=abc 返回 400 而非 500', st == 400, '%s %s' % (st, r))

# --- 3. prompt 非字符串被拒 ---
st, r = req(E + '/knight/frames/idle/0/generate', {'prompt': {'a': 1}})
check('prompt 非字符串被拒', st == 400, '%s %s' % (st, r))

# --- 4. 正常生成（mock）---
st, r = req(E + '/knight/frames/idle/0/generate', {'prompt': 'knight idle', 'seed': 7})
check('正常生成受理', st == 200 and 'task_id' in r, str(r)[:80])
tid = r.get('task_id')
for _ in range(40):
    time.sleep(0.4)
    _, tasks = req('/api/tasks')
    t = [x for x in tasks if x['id'] == tid]
    if t and t[0]['status'] in ('completed', 'failed', 'timeout'):
        break
check('生成完成', t and t[0]['status'] == 'completed', t[0]['status'] if t else '?')

# --- 5. adopt body 传数组（旧前端的 bug 形态）→ 应 400 而非 500 ---
_, stt = req(E + '/knight')
cands = stt['frames']['idle']['candidates']['0']
st, r = req(E + '/knight/frames/idle/0/adopt', {'candidate': cands})
check('adopt 传数组被拒(400 非 500)', st == 400, '%s %s' % (st, r))

# --- 6. adopt 传正确文件名 → 成功 ---
st, r = req(E + '/knight/frames/idle/0/adopt', {'candidate': cands[0]})
check('adopt 传文件名成功', st == 200 and r['frames']['idle']['adopted'] == 1, str(r.get('frames', {}).get('idle', {}))[:60])

# --- 7. adopt 路径穿越 ---
st, r = req(E + '/knight/frames/idle/0/adopt', {'candidate': '../../../spec.json'})
check('adopt 路径穿越被拒', st == 400, '%s %s' % (st, r))

# --- 8. 静态路由路径穿越 ---
st, r = req(E + '/knight/files/../../../spec.json')
check('files 路径穿越被挡', st in (400, 404), str(st))

# --- 9. spec 动画名非法被拒 ---
st, r = req(E + '/knight/spec', {'animations': ['idle', '../evil']})
check('非法动画名被拒', st == 400, '%s %s' % (st, r))
st, r = req(E + '/knight/spec', {'animations': ['idle', 'walk']})
check('合法动画名接受', st == 200, str(r.get('spec', {}).get('animations')))

# --- 10. 原子写后 tasks.json 仍是合法 JSON ---
import pathlib
tp = pathlib.Path(__file__).parent / 'workspaces' / 'tasks.json'
ok = False
if tp.exists():
    try:
        json.load(open(tp, encoding='utf-8')); ok = True
    except Exception as e:
        ok = False
check('tasks.json 合法 JSON', ok)

# --- 11. 完整链 S5/S6/S7 ---
st, r = req(E + '/knight/normalize', {})
check('S5 归一化', st == 200 and len(r.get('normalized', [])) >= 1, str(r)[:60])
st, r = req(E + '/knight/pack', {})
check('S6 打包', st == 200 and 'idle' in r.get('animations', []), str(r)[:60])
st, r = req(E + '/knight/validate')
check('S7 校验', st == 200 and 'ok' in r, str(r)[:60])

print('\n%s  (%d failed)' % ('ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails), len(fails)))
sys.exit(1 if fails else 0)
