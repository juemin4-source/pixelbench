#!/usr/bin/env python3
"""PixelBench server — stdlib-only ThreadingHTTPServer.
Run:  python bench_server.py            (port 8321)
      PIXELBENCH_MOCK=1 python bench_server.py   (mock generate, no GPU)
"""
import json, os, re, sys, io, time, random, shutil, zipfile, threading
import urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(HERE, 'workspaces')
SPEC_GLOBAL = json.load(open(os.path.join(HERE, 'spec.json'), encoding='utf-8'))
MOCK = os.environ.get('PIXELBENCH_MOCK') == '1'

# ---------------- ComfyUI client (pure HTTP port of dsh-qwenimage) ----------------
COMFY = os.environ.get('PIXELBENCH_COMFY', 'http://127.0.0.1:8188')
UNET = 'qwen_image_2.1_int8_convrot.safetensors'
CLIP = 'qwen3vl_8b_int8_convrot.safetensors'
VAE = 'qwen_image_2.1_vae_bf16.safetensors'
SHIFT, CFG, STEPS_DEFAULT = 3.1, 4.0, 20

def http_json(url, data=None, method=None, timeout=30):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method or ('POST' if body else 'GET'))
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def http_bytes(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()

def comfy_ping():
    try:
        d = http_json(COMFY + '/system_stats', timeout=4)
        dev = (d.get('devices') or [{}])[0]
        return True, round(dev.get('vram_free', 0) / 1024 ** 3, 1)
    except Exception:
        return False, 0

def upload_ref(bytes_, name, subfolder='dsh'):
    import uuid
    boundary = uuid.uuid4().hex
    fp = os.path.join(WS, '.upload_tmp_%s.png' % uuid.uuid4().hex[:8])
    open(fp, 'wb').write(bytes_)
    try:
        with open(fp, 'rb') as f:
            img = f.read()
        parts = []
        def part(name, value, ctype=None, filename=None):
            head = ('Content-Disposition: form-data; name="%s"%s\r\n' %
                    (name, ('; filename="%s"' % filename) if filename else ''))
            if ctype:
                head += 'Content-Type: %s\r\n' % ctype
            parts.append(head.encode() + b'\r\n')
            parts.append(value if isinstance(value, bytes) else str(value).encode())
            parts.append(b'\r\n')
        part('image', img, 'image/png', name)
        part('subfolder', subfolder)
        part('overwrite', 'true')
        body = b'--' + boundary.encode() + b'\r\n' + b''.join(parts) + b'--' + boundary.encode() + b'--\r\n'
        req = urllib.request.Request(COMFY + '/upload/image', data=body, method='POST')
        req.add_header('Content-Type', 'multipart/form-data; boundary=' + boundary)
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        return d.get('subfolder') and '%s/%s' % (d['subfolder'], d['name']) or d['name']
    finally:
        os.path.exists(fp) and os.remove(fp)

def resolve_size(quality, width=None, height=None):
    if width and height:
        return int(width), int(height)
    return {'fast': 1024, 'standard': 1536, 'hq': 2048}.get(quality, 1024), {'fast': 1024, 'standard': 1536, 'hq': 2048}.get(quality, 1024)

def build_graph(prompt, negative, width, height, steps, seed, ref_files, transparent):
    def g(t, i): return {'class_type': t, 'inputs': i}
    p = {
        '1': g('UNETLoader', {'unet_name': UNET, 'weight_dtype': 'default'}),
        '2': g('CLIPLoader', {'clip_name': CLIP, 'type': 'qwen_image', 'device': 'default'}),
        '3': g('VAELoader', {'vae_name': VAE}),
    }
    image_inputs = {}
    for i, f in enumerate(ref_files):
        nid = str(10 + i)
        p[nid] = g('LoadImage', {'image': f})
        image_inputs['images.image_%d' % (i + 1)] = [nid, 0]
    final_prompt = prompt
    if transparent and not re.search(r'RGBA|alpha|transparent|透明', final_prompt, re.I):
        final_prompt = (prompt + ' This is an RGBA image with transparency. The image has alpha '
                         'channel and the background is fully transparent, no background.')
    p['4'] = g('TextEncodeQwenImage21', {
        'clip': ['2', 0], 'prompt': final_prompt, 'negative_prompt': negative or '',
        'resolution': min(width, height),
        **({'vae': ['3', 0]} if ref_files else {}), **image_inputs})
    p['5'] = g('EmptySD3LatentImage', {'width': width, 'height': height, 'batch_size': 1})
    p['6'] = g('ModelSamplingAuraFlow', {'model': ['1', 0], 'shift': SHIFT})
    p['7'] = g('KSampler', {'model': ['6', 0], 'positive': ['4', 0], 'negative': ['4', 0],
                            'latent_image': ['5', 0], 'seed': seed, 'steps': steps, 'cfg': CFG,
                            'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0})
    p['8'] = g('VAEDecode', {'samples': ['7', 0], 'vae': ['3', 0]})
    p['9'] = g('SaveImage', {'images': ['8', 0], 'filename_prefix': 'dsh/pixelbench'})
    return p

# ---------------- workspace model ----------------
os.makedirs(WS, exist_ok=True)
TASKS_FILE = os.path.join(WS, 'tasks.json')

def load_tasks():
    if os.path.exists(TASKS_FILE):
        try:
            return json.load(open(TASKS_FILE, encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            return {}   # 损坏时放弃旧任务表，不让服务起不来
    return {}

TASKS_LOCK = threading.Lock()

def save_tasks(t):
    """原子写：先写临时文件再替换，避免并发下半截 JSON。"""
    with TASKS_LOCK:
        tmp = TASKS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(t, f, indent=1, ensure_ascii=False)
        os.replace(tmp, TASKS_FILE)

def safe_join(base, *parts):
    """把不可信片段拼到 base 下，并确保结果不逃出 base（防路径穿越）。"""
    target = os.path.normpath(os.path.join(base, *[str(x) for x in parts]))
    base_n = os.path.normpath(base)
    if target != base_n and not target.startswith(base_n + os.sep):
        raise ValueError('path escapes workspace')
    return target

def proj_dir(project):
    safe = re.sub(r'[^A-Za-z0-9_\-\u4e00-\u9fff]', '_', project)
    return os.path.join(WS, safe)

def ent_dir(project, name):
    safe = re.sub(r'[^A-Za-z0-9_\-\u4e00-\u9fff]', '_', name)
    return os.path.join(proj_dir(project), safe)

def proj_spec(project):
    p = os.path.join(proj_dir(project), 'project.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    return {}

def ent_spec(project, name):
    p = os.path.join(ent_dir(project, name), 'spec.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    return {}

def project_state(project):
    d = proj_dir(project)
    if not os.path.isdir(d):
        return None
    ents = [x for x in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d, x))]
    return {'name': project, 'spec': proj_spec(project), 'entities': ents}

def entity_state(project, name):
    d = ent_dir(project, name)
    if not os.path.isdir(d):
        return None
    spec = ent_spec(project, name)
    anims = spec.get('animations', [])
    refs = []
    rdir = os.path.join(d, 'refs')
    if os.path.isdir(rdir):
        for f in sorted(os.listdir(rdir)):
            if f.lower().endswith(('.png', '.jpg', '.webp')):
                refs.append({'name': f, 'kind': f.split('_')[0] if f.startswith('style_') or f.startswith('base_') else 'other'})
    frames = {}
    for a in anims:
        slots = []
        cdir = os.path.join(d, 'candidates', a)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            m = re.match(r'^%s_(\d{3})\.png$' % re.escape(a), f)
            if m:
                slots.append(int(m.group(1)))
        n_adopted = len(slots)
        # candidate dirs
        cands = {}
        if os.path.isdir(cdir):
            for idx_s in sorted(os.listdir(cdir)):
                cd = os.path.join(cdir, idx_s)
                if os.path.isdir(cd):
                    cands[idx_s] = sorted(x for x in os.listdir(cd) if x.endswith('.png'))
        # adopted 同时给出数量与真实槽位：帧号可能不连续（采纳 0、2 而跳过 1），
        # 前端必须按真实槽位判定，否则会把不存在的帧当成已采纳（404 破图）
        frames[a] = {'adopted': n_adopted, 'adopted_slots': slots, 'candidates': cands,
                     'max_frame': max(slots) if slots else 0}
    return {'name': name, 'spec': spec, 'refs': refs, 'frames': frames,
            'sheets': os.listdir(os.path.join(d, 'sheets')) if os.path.isdir(os.path.join(d, 'sheets')) else []}

# ---------------- mock generator ----------------
MOCK_POOL = None
def mock_frame(entity, anim, idx, out):
    sys.path.insert(0, HERE)
    from make_test_frames import draw_frame
    import math
    draw_frame(out, (idx * 0.37 + time.time() * 0.01) % 1, anim)

# ---------------- task runner ----------------
TASKS = load_tasks()
# recover: running tasks -> poll history; if not found -> interrupted
for tid, t in TASKS.items():
    if t['status'] in ('queued', 'running'):
        t['status'] = 'recovered'

def track_task(tid, project, entity, anim, idx, prompt_meta):
    """Background poller: history -> download outputs -> prune candidates."""
    t = TASKS[tid]
    d = ent_dir(project, entity)
    cdir = os.path.join(d, 'candidates', anim, str(idx))
    os.makedirs(cdir, exist_ok=True)
    deadline = time.time() + 25 * 60
    while time.time() < deadline:
        try:
            h = http_json(COMFY + '/history/' + tid, timeout=5)
            entry = h.get(tid)
            if entry:
                st = (entry.get('status') or {}).get('status_str')
                outs = []
                for node in (entry.get('outputs') or {}).values():
                    outs += node.get('images', [])
                if outs or st == 'success':
                    for img in outs:
                        url = '%s/view?filename=%s&subfolder=%s&type=%s' % (
                            COMFY, img['filename'], img.get('subfolder', ''), img.get('type', 'output'))
                        data = http_bytes(url)
                        fn = 'c%02d.png' % (len(os.listdir(cdir)))
                        open(os.path.join(cdir, fn), 'wb').write(data)
                    # prune to 3
                    files = sorted(x for x in os.listdir(cdir) if x.endswith('.png'))
                    for old in files[:-3]:
                        os.remove(os.path.join(cdir, old))
                    t.update(status='completed', progress=100,
                             candidates=[x for x in sorted(os.listdir(cdir)) if x.endswith('.png')],
                             finishedAt=time.time())
                    save_tasks(TASKS)
                    return
                if st == 'error':
                    t.update(status='failed', error='ComfyUI error', finishedAt=time.time())
                    save_tasks(TASKS)
                    return
                t['progress'] = 5
                t['status'] = 'running'
        except Exception as e:
            if 'HTTP Error 404' in str(e):
                t.update(status='interrupted', error='task not in ComfyUI history (server restarted?)')
                save_tasks(TASKS)
                return
            t['status'] = t.get('status', 'running')
        time.sleep(1.2)
    t.update(status='timeout', finishedAt=time.time())
    save_tasks(TASKS)

# ---------------- request handler ----------------
class H(BaseHTTPRequestHandler):
    server_version = 'PixelBench/0.1'

    def log_message(self, fmt, *args):
        with open(os.path.join(HERE, 'bench.log'), 'a', encoding='utf-8') as f:
            f.write(time.strftime('%H:%M:%S ') + (fmt % args) + '\n')

    def send_json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype):
        b = open(path, 'rb').read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        ct = self.headers.get('Content-Type', '')
        if ct.startswith('application/json'):
            return json.loads(raw.decode('utf-8'))
        return {'__bytes__': raw, '__content_type__': ct,
                '__filename__': self.headers.get('X-Filename', 'upload.png')}

    def route(self, method):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        try:
            # --- static ---
            if method == 'GET' and p in ('/', '/bench.html'):
                return self.send_file(os.path.join(HERE, 'bench.html'), 'text/html; charset=utf-8')
            # --- status ---
            if p == '/api/status':
                online, vram = comfy_ping()
                active = [t for t in TASKS.values() if t['status'] in ('queued', 'running', 'recovered')]
                return self.send_json(200, {'comfy': online, 'vram_free_gb': vram,
                                            'mock': MOCK, 'active_tasks': len(active)})
            # --- tasks ---
            if p == '/api/tasks':
                return self.send_json(200, list(TASKS.values())[-40:])
            m = re.match(r'^/api/tasks/([\w-]+)/cancel$', p)
            if m and method == 'POST':
                t = TASKS.get(m.group(1))
                if t and t['status'] in ('queued', 'running', 'recovered'):
                    t.update(status='cancelled', finishedAt=time.time())
                    save_tasks(TASKS)
                return self.send_json(200, t or {})
            # --- projects ---
            if p == '/api/projects' and method == 'GET':
                ps = []
                if os.path.isdir(WS):
                    for d in sorted(os.listdir(WS)):
                        dd = os.path.join(WS, d)
                        if os.path.isdir(dd) and not d.startswith('.'):
                            n = len([x for x in os.listdir(dd) if os.path.isdir(os.path.join(dd, x))])
                            ps.append({'name': d, 'entities': n})
                return self.send_json(200, ps)
            if p == '/api/projects' and method == 'POST':
                b = self.body()
                pname = (b.get('name') or '').strip()
                if not pname:
                    return self.send_json(400, {'error': 'name required'})
                d = proj_dir(pname)
                if os.path.isdir(d):
                    return self.send_json(400, {'error': 'exists'})
                os.makedirs(d)
                spec = {'name': pname, 'game': b.get('game', pname), 'notes': b.get('notes', ''),
                        'style_bible': b.get('style_bible', '')}
                json.dump(spec, open(os.path.join(d, 'project.json'), 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=1)
                return self.send_json(200, project_state(pname))
            pm = re.match(r'^/api/projects/([^/]+)$', p)
            if pm and method == 'GET':
                st = project_state(pm.group(1))
                return self.send_json(200 if st else 404, st or {'error': 'not found'})
            if pm and method == 'DELETE':
                d = proj_dir(pm.group(1))
                if os.path.isdir(d):
                    shutil.rmtree(d)
                return self.send_json(200, {'ok': True})
            # --- entities (project-scoped) ---
            em0 = re.match(r'^/api/projects/([^/]+)/entities$', p)
            if em0 and method == 'GET':
                st = project_state(em0.group(1))
                return self.send_json(200 if st else 404, (st or {}).get('entities', []))
            if em0 and method == 'POST':
                project = em0.group(1)
                if not os.path.isdir(proj_dir(project)):
                    return self.send_json(404, {'error': 'project not found'})
                b = self.body()
                name = b.get('name', '').strip()
                if not name:
                    return self.send_json(400, {'error': 'name required'})
                d = ent_dir(project, name)
                if os.path.isdir(d):
                    return self.send_json(400, {'error': 'exists'})
                for sub_d in ('refs', 'candidates', 'normalized', 'sheets'):
                    os.makedirs(os.path.join(d, sub_d))
                spec = {'name': name, 'animations': b.get('animations') or SPEC_GLOBAL['entity_template']['animations'],
                        'fps': b.get('fps', SPEC_GLOBAL['entity_template'].get('fps', 12)),
                        'notes': ''}
                json.dump(spec, open(os.path.join(d, 'spec.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                return self.send_json(200, entity_state(project, name))
            m = re.match(r'^/api/projects/([^/]+)/entities/([^/]+)$', p)
            em = re.match(r'^/api/projects/([^/]+)/entities/([^/]+)/(.+)$', p)
            if m and not em:
                project, name = m.group(1), m.group(2)
                if method == 'GET':
                    st = entity_state(project, name)
                    return self.send_json(200 if st else 404, st or {'error': 'not found'})
                if method == 'DELETE':
                    d = ent_dir(project, name)
                    if os.path.isdir(d):
                        shutil.rmtree(d)
                    return self.send_json(200, {'ok': True})
            if em:
                project, name, sub = em.group(1), em.group(2), em.group(3)
                d = ent_dir(project, name)
                if not os.path.isdir(d):
                    return self.send_json(404, {'error': 'entity not found'})
                # spec update
                if sub == 'spec' and method == 'POST':
                    b = self.body()
                    spec = ent_spec(project, name)
                    if 'animations' in b:
                        a = b['animations']
                        if not isinstance(a, list) or not all(isinstance(x, str) for x in a):
                            return self.send_json(400, {'error': 'animations must be a list of strings'})
                        a = [x.strip() for x in a if x.strip()]
                        if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x) for x in a):
                            return self.send_json(400, {'error': 'animation names must be [A-Za-z_][A-Za-z0-9_]*'})
                        spec['animations'] = a
                    for k in ('fps', 'notes'):
                        if k in b:
                            spec[k] = b[k]
                    json.dump(spec, open(os.path.join(d, 'spec.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                    return self.send_json(200, entity_state(project, name))
                # static file under entity
                if sub.startswith('files/'):
                    try:
                        fp = safe_join(d, sub[6:])
                    except ValueError:
                        return self.send_json(400, {'error': 'bad path'})
                    if os.path.isfile(fp):
                        return self.send_file(fp, 'image/png')
                    return self.send_json(404, {'error': 'file not found'})
                # refs
                if sub.startswith('refs/') and method == 'POST':
                    b = self.body()
                    if '__bytes__' not in b:
                        return self.send_json(400, {'error': 'binary body expected'})
                    fn = os.path.basename(b.get('__filename__') or 'ref.png')
                    if not fn or fn.startswith('.'):
                        return self.send_json(400, {'error': 'bad file name'})
                    dest = safe_join(d, 'refs', fn)
                    with open(dest, 'wb') as f:
                        f.write(b['__bytes__'])
                    return self.send_json(200, entity_state(project, name))
                # generate
                mg = re.match(r'^frames/([A-Za-z0-9_]+)/(\d+)/generate$', sub)
                if mg and method == 'POST':
                    anim, idx = mg.group(1), int(mg.group(2))
                    if anim not in ent_spec(project, name).get('animations', []):
                        return self.send_json(400, {'error': 'unknown animation: %s' % anim})
                    b = self.body()
                    prompt = b.get('prompt', '') or ''
                    if not isinstance(prompt, str):
                        return self.send_json(400, {'error': 'prompt must be a string'})
                    refs = b.get('refs', []) or []
                    if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
                        return self.send_json(400, {'error': 'refs must be a list of names'})
                    try:
                        seed = b.get('seed') if b.get('seed') not in (None, '', 'random') else random.randint(1, 2 ** 31)
                        seed = int(seed)
                        steps = int(b.get('steps', STEPS_DEFAULT))
                    except (TypeError, ValueError):
                        return self.send_json(400, {'error': 'seed/steps must be integers'})
                    quality = b.get('quality', 'fast')
                    w, h = resolve_size(quality, b.get('width'), b.get('height'))
                    transparent = bool(b.get('transparent'))
                    if MOCK:
                        tid = 'mock-%d' % int(time.time() * 1000)
                        TASKS[tid] = {'id': tid, 'project': project, 'entity': name, 'anim': anim, 'frame': idx,
                                      'status': 'running', 'progress': 10, 'prompt': prompt,
                                      'seed': seed, 'submittedAt': time.time(), 'candidates': []}
                        save_tasks(TASKS)
                        def mock_run():
                            try:
                                time.sleep(1.5)
                                cdir = os.path.join(d, 'candidates', anim, str(idx))
                                os.makedirs(cdir, exist_ok=True)
                                fn = 'c%02d.png' % len([x for x in os.listdir(cdir) if x.endswith('.png')])
                                mock_frame(name, anim, idx, os.path.join(cdir, fn))
                                files = sorted(x for x in os.listdir(cdir) if x.endswith('.png'))
                                for old in files[:-3]:
                                    os.remove(os.path.join(cdir, old))
                                TASKS[tid].update(status='completed', progress=100,
                                                  candidates=sorted(x for x in os.listdir(cdir) if x.endswith('.png')),
                                                  finishedAt=time.time())
                            except Exception as e:
                                # 必须落盘为 failed：否则任务永久停在 running，前端一直显示"生成中"
                                TASKS[tid].update(status='failed', error=str(e)[:200], finishedAt=time.time())
                            save_tasks(TASKS)
                        threading.Thread(target=mock_run, daemon=True).start()
                        return self.send_json(200, {'task_id': tid})
                    # real ComfyUI path: upload refs
                    ref_files = []
                    for rn in refs:
                        try:
                            fp = safe_join(d, 'refs', os.path.basename(rn))
                        except ValueError:
                            return self.send_json(400, {'error': 'bad ref: %s' % rn})
                        if not os.path.isfile(fp):
                            return self.send_json(400, {'error': 'ref missing: %s' % rn})
                        ref_files.append(upload_ref(open(fp, 'rb').read(),
                                                     'pb_%s_%s_%d.png' % (anim, idx, int(time.time() * 1000) % 100000)))
                    graph = build_graph(prompt, b.get('negative', ''), w, h, steps, seed, ref_files, transparent)
                    sub_r = http_json(COMFY + '/prompt', {'prompt': graph, 'client_id': 'pixelbench'})
                    tid = sub_r['prompt_id']
                    TASKS[tid] = {'id': tid, 'project': project, 'entity': name, 'anim': anim, 'frame': idx,
                                  'status': 'running', 'progress': 0, 'prompt': prompt, 'seed': seed,
                                  'refs': ref_files, 'submittedAt': time.time(), 'candidates': []}
                    save_tasks(TASKS)
                    threading.Thread(target=track_task, args=(tid, project, name, anim, idx, b), daemon=True).start()
                    return self.send_json(200, {'task_id': tid})
                # adopt
                ma = re.match(r'^frames/([A-Za-z0-9_]+)/(\d+)/adopt$', sub)
                if ma and method == 'POST':
                    anim, idx = ma.group(1), int(ma.group(2))
                    b = self.body()
                    cand = b.get('candidate', '')
                    if not isinstance(cand, str) or not cand:
                        return self.send_json(400, {'error': 'candidate must be a file name'})
                    try:
                        src = safe_join(d, 'candidates', anim, str(idx), os.path.basename(cand))
                    except ValueError:
                        return self.send_json(400, {'error': 'bad candidate'})
                    if not os.path.isfile(src):
                        return self.send_json(400, {'error': 'candidate missing'})
                    dst = os.path.join(d, '%s_%03d.png' % (anim, idx))
                    shutil.copyfile(src, dst)
                    return self.send_json(200, entity_state(project, name))
                # discard
                md = re.match(r'^frames/([A-Za-z0-9_]+)/(\d+)/discard$', sub)
                if md and method == 'POST':
                    anim, idx = md.group(1), int(md.group(2))
                    cdir = os.path.join(d, 'candidates', anim, str(idx))
                    if os.path.isdir(cdir):
                        shutil.rmtree(cdir)
                    return self.send_json(200, entity_state(project, name))
                # normalize / pack / validate / export
                if sub == 'normalize' and method == 'POST':
                    return self.run_cpu_tool(project, name, 'normalize')
                if sub == 'pack' and method == 'POST':
                    return self.run_cpu_tool(project, name, 'pack')
                if sub == 'validate' and method == 'GET':
                    return self.run_cpu_tool(project, name, 'validate')
                if sub == 'export' and method == 'POST':
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
                        for root, _, fs in os.walk(d):
                            for f in fs:
                                if not (f.endswith('.png') or f.endswith('.json')):
                                    continue
                                full = os.path.join(root, f)
                                rel = os.path.relpath(full, d)
                                # 归档路径必须仍在 <entity>/ 下
                                arc = os.path.normpath(os.path.join(name, rel))
                                if arc.startswith('..') or os.path.isabs(arc):
                                    continue
                                z.write(full, arc)
                    data = buf.getvalue()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Disposition', 'attachment; filename="%s.zip"' % name)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            return self.send_json(404, {'error': 'no route: %s %s' % (method, p)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self.send_json(500, {'error': str(e)})

    def run_cpu_tool(self, project, name, tool):
        d = ent_dir(project, name)
        sys.path.insert(0, HERE)
        import asset_tool as T
        if tool == 'normalize':
            results = []
            src_dir = d
            out_dir = os.path.join(d, 'normalized')
            os.makedirs(out_dir, exist_ok=True)
            for f in sorted(os.listdir(src_dir)):
                m = re.match(r'^([A-Za-z0-9_]+)_(\d{2,4})\.png$', f)
                if m:
                    r = T.normalize(os.path.join(src_dir, f), os.path.join(out_dir, f))
                    results.append(r['out'])
            return self.send_json(200, {'normalized': results})
        if tool == 'pack':
            idx = T.pack(d, os.path.join(d, 'sheets'))
            return self.send_json(200, {'animations': list(idx['animations'].keys()), 'index': os.path.join(d, 'sheets')})
        if tool == 'validate':
            rep = T.validate(d)
            json.dump(rep, open(os.path.join(d, 'validation.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            return self.send_json(200, rep)
        return self.send_json(400, {'error': 'unknown tool'})

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')

    def do_DELETE(self):
        self.route('DELETE')

H._pool = None
import threading  # noqa: E402  (used in mock_run above)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8321))
    srv = ThreadingHTTPServer(('127.0.0.1', port), H)
    print('PixelBench http://127.0.0.1:%d  (mock=%s)' % (port, MOCK))
    srv.serve_forever()
