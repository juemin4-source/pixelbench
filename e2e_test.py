#!/usr/bin/env python3
"""E2E test: drive the full PixelBench API in mock mode (project-scoped)."""
import json, time, urllib.request

BASE = 'http://127.0.0.1:8321'
PROJ = 'ever_eclipse'

def req(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    m = method or ('POST' if body else 'GET')
    r = urllib.request.Request(BASE + path, data=body, method=m)
    if body:
        r.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(r, timeout=30) as resp:
        b = resp.read()
        if resp.headers.get('Content-Type', '').startswith('application/zip'):
            return b
        return json.loads(b)

# 0. projects
for old in ('ever_eclipse',):
    try:
        req('/api/projects/%s' % old, method='DELETE')
    except Exception:
        pass
proj = req('/api/projects', {'name': PROJ, 'game': '永蚀 Ever Eclipse', 'notes': '横版搜打撤'})
print('project:', proj['name'], '| entities:', proj['entities'])
plist = req('/api/projects')
print('project list:', plist)
assert any(x['name'] == PROJ for x in plist)

E = '/api/projects/%s/entities' % PROJ

# 1. entity
ent = req(E, {'name': 'knight'})
print('created entity:', ent['name'], ent['spec']['animations'])

# 2. generate 3 frames
for i in range(3):
    r = req('%s/knight/frames/idle/%d/generate' % (E, i), {'prompt': 'knight idle', 'seed': 100 + i})
    tid = r['task_id']
    for _ in range(40):
        time.sleep(0.4)
        t = [x for x in req('/api/tasks') if x['id'] == tid]
        if t and t[0]['status'] in ('completed', 'failed', 'timeout'):
            break
    t = [x for x in req('/api/tasks') if x['id'] == tid][0]
    print('  frame %d -> %s cands=%s' % (i, t['status'], t.get('candidates')))
    assert t['status'] == 'completed', t

# 3. adopt
st = req('%s/knight' % E)
c0 = st['frames']['idle']['candidates']['0'][0]
a = req('%s/knight/frames/idle/0/adopt' % E, {'candidate': c0})
print('adopted ->', a['frames']['idle']['adopted'])
assert a['frames']['idle']['adopted'] == 1

# 4. S5 / S6 / S7
n = req('%s/knight/normalize' % E, {})
print('S5 normalize:', len(n['normalized']), 'frames')
p = req('%s/knight/pack' % E, {})
print('S6 pack:', p['animations'])
v = req('%s/knight/validate' % E)
print('S7 validate: ok=%s %s' % (v['ok'], {k: len(d['problems']) for k, d in v['animations'].items()}))

# 5. export
z = req('%s/knight/export' % E, {})
open('e2e_export.zip', 'wb').write(z)
import zipfile
names = zipfile.ZipFile('e2e_export.zip').namelist()
print('export zip:', len(names), 'files')
assert any('index' in x for x in names)

# 6. file serving (project-scoped path)
u = '%s/knight/files/idle_000.png' % E
with urllib.request.urlopen(BASE + u, timeout=10) as r:
    print('static png:', r.status, len(r.read()), 'bytes')

# 7. spec update
su = req('%s/knight/spec' % E, {'animations': ['idle', 'walk'], 'fps': 10, 'notes': 'e2e'})
print('spec:', su['spec']['animations'], su['spec']['fps'])

print('\nE2E PASS (project-scoped)')
