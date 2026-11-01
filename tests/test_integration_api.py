"""真 Unix socket HTTP 集成测试：验证前端消费的响应形状（接口合同）。

不启动 scheduler：只证明 HTTP 层、鉴权/CSRF 与响应形状，不证明任务执行。
"""
import http.client, json, os, sys, tempfile, threading, unittest, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from unittest.mock import patch

class UnixConnection(http.client.HTTPConnection):
    def __init__(self, sockpath):
        super().__init__('localhost', timeout=5)
        self.sockpath = sockpath
    def connect(self):
        import socket
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.sockpath))

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name).resolve()  # macOS /var 为软链接，open_beneath 逐级 O_NOFOLLOW 需真实路径
        (root/'media').mkdir(); (root/'out').mkdir(); (root/'var').mkdir(); (root/'var'/'jobs').mkdir()
        cls.media = root/'media'; cls.out = root/'out'
        (cls.media/'a.mp4').write_bytes(b'x'*10)
        # 模块属性级隔离：无论 import 顺序如何，本测试读写自己的 VAR/DB。
        cls.env_patch = patch.multiple(server,
            VAR=root/'var', DB=root/'var'/'tasks.sqlite')
        cls.roots_patch = patch.object(server.platform_api, 'shared_roots',
            return_value=[str(cls.media), str(cls.out)])
        cls.acl_patch = patch.object(server.platform_api, 'check_acl', lambda uid, path, write=False: None)
        cls.call_patch = patch.object(server.platform_api, 'call',
            return_value=[{'path': str(cls.media/'a.mp4'), 'readable': True}])
        cls.auth_patch = patch.object(server.Handler, 'auth', lambda self: 1000)
        for p in (cls.env_patch, cls.roots_patch, cls.acl_patch, cls.call_patch, cls.auth_patch):
            p.start()
        server.initialize()
        cls.sockpath = root/'srv.sock'
        cls.srv = server.Server(str(cls.sockpath), server.Handler)
        cls.thread = threading.Thread(target=cls.srv.serve_forever, kwargs=dict(poll_interval=0.05), daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.thread.join(timeout=5)
        for p in (cls.env_patch, cls.roots_patch, cls.acl_patch, cls.call_patch, cls.auth_patch):
            p.stop()
        cls.temp.cleanup()
    def get(self, route, expect=200):
        conn = UnixConnection(self.sockpath)
        try:
            conn.request('GET', '/app/video-enhance'+route, headers={'X-Trim-Userid': '1000'})
            r = conn.getresponse(); body = r.read()
            self.assertEqual(r.status, expect, body[:400])
            return json.loads(body)
        finally:
            conn.close()
    def post(self, route, payload, csrf, expect=200):
        conn = UnixConnection(self.sockpath)
        try:
            try:
                conn.request('POST', '/app/video-enhance'+route, body=json.dumps(payload).encode(),
                    headers={'X-Trim-Userid': '1000', 'Content-Type': 'application/json', 'X-VE-CSRF': csrf})
            except BrokenPipeError:
                # 拒绝无效CSRF可早于正文发送；仍须读取并严格验证403响应。
                if expect != 403:
                    raise
            r = conn.getresponse(); body = r.read()
            self.assertEqual(r.status, expect, body[:400])
            return json.loads(body)
        finally:
            conn.close()
    def test_status_shape(self):
        s = self.get('/api/status')
        # 前端 settings 页消费字段（renderPolicy/能力卡片）必须齐全：
        for key in ('version','user_uid','csrf','gpu','capabilities','limits','execution_policy','demo_dir'):
            self.assertIn(key, s)
        self.assertIsInstance(s['capabilities'], list)
        self.assertIsInstance(s['limits'], list)
        self.assertIsInstance(s['gpu'], dict)
    def test_directories_shape(self):
        d = self.get('/api/directories')
        for key in ('shared','roles','inputs','outputs'):
            self.assertIn(key, d)
        self.assertIn(str(self.media), d['shared'])
        self.assertIn(str(self.media), d['inputs'])
        # 0.3.4 起成果直接落原片旁，不再维护独立成果目录：
        # outputs（可写候选）必须是 inputs 的子集，且包含应用自管的 demo 私有区
        self.assertTrue(set(d['outputs']).issubset(set(d['inputs'])))
        self.assertIn(str(self.media.parent/'var'/'demo'), d['outputs'])
    def test_browse_applies_acl(self):
        b = self.get('/api/browse?path='+str(self.media))
        self.assertEqual(b['path'], str(self.media))
        self.assertEqual([e['name'] for e in b['entries']], ['a.mp4'])
    def test_job_list_has_resume_capable(self):
        s = self.server
        with s.db() as c:
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                ('j1', 1000, json.dumps(dict(pipeline_version=s.VERSION, mode='both')), 'paused', '用户暂停', 1))
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                ('j2', 1000, json.dumps(dict(pipeline_version='0.1.0', mode='both')), 'interrupted', '旧版', 2))
        jobs = {j['id']: j for j in self.get('/api/jobs')}
        self.assertIn('j1', jobs); self.assertIn('j2', jobs)
        self.assertTrue(jobs['j1']['resume_capable'])
        self.assertFalse(jobs['j2']['resume_capable'])
        self.assertIn('progress', jobs['j1']); self.assertIn('detail', jobs['j1'])
    def test_post_directories_sets_role_and_csrf_required(self):
        csrf = self.get('/api/status')['csrf']
        r = self.post('/api/directories', dict(path=str(self.out), role='both'), csrf)
        self.assertEqual(r, {'ok': True})
        d = self.get('/api/directories')
        self.assertEqual(d['roles'].get(str(self.out)), 'both')
        # CSRF 缺失/错误必须 403：
        self.post('/api/directories', dict(path=str(self.out), role='input'), 'bad', expect=403)
    def test_preflight_checks_output_permission_before_support(self):
        info=dict(width=128,height=72,fps=24,duration=2,unsupported_reasons=[])
        route='/api/preflight?'+urllib.parse.urlencode(dict(path=str(self.media/'a.mp4'),mode='both',fps=60,scale=2))
        with patch.object(self.server,'roles',return_value={str(self.media):'input'}), patch.object(self.server,'probe_fd',return_value=info):
            result=self.get(route)
            self.assertFalse(result['supported']);self.assertFalse(result['output_writable'])
            self.assertIn('输入 + 输出',result['reasons'][0])
        with patch.object(self.server,'roles',return_value={str(self.media):'both'}), patch.object(self.server,'probe_fd',return_value=info):
            result=self.get(route)
            self.assertTrue(result['supported']);self.assertTrue(result['output_writable'])
            self.assertEqual(result['output_dir'],str(self.media))
            self.assertEqual(result['output_fps'],60)

    def test_preflight_rejects_application_filesystem_acl(self):
        info=dict(width=1920,height=1080,fps=24,duration=2,unsupported_reasons=[])
        route='/api/preflight?'+urllib.parse.urlencode(dict(path=str(self.media/'a.mp4'),mode='both',fps=60,scale=2))
        with patch.object(self.server,'roles',return_value={str(self.media):'both'}), patch.object(self.server,'probe_fd',return_value=info), patch.object(self.server.os,'access',return_value=False):
            result=self.get(route)
            self.assertFalse(result['supported']);self.assertFalse(result['output_writable'])
            self.assertIn('应用账号无法写入',result['reasons'][0])
            with self.assertRaises(PermissionError):
                self.server.output_directory(1000,str(self.media/'a.mp4'))

    def test_resume_checks_actual_write_and_preserves_legacy_output(self):
        s=self.server
        st=(self.media/'a.mp4').stat()
        spec=dict(pipeline_version=s.VERSION,source_path=str(self.media/'a.mp4'),
                  source_size=st.st_size,source_mtime_ns=st.st_mtime_ns,output_dir=str(self.out))
        with s.db() as c:
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                      ('resume-acl',1000,json.dumps(spec),'paused','test',3))
        csrf=self.get('/api/status')['csrf']
        with patch.object(s,'roles',return_value={str(self.media):'input',str(self.out):'output'}):
            with patch.object(s.os,'access',return_value=False):
                result=self.post('/api/resume',dict(id='resume-acl'),csrf,expect=403)
                self.assertIn('应用账号无法写入',result['error'])
            with s.db() as c:
                self.assertEqual(c.execute('SELECT state FROM jobs WHERE id=?',('resume-acl',)).fetchone()['state'],'paused')
            self.post('/api/resume',dict(id='resume-acl'),csrf)
        with s.db() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',('resume-acl',)).fetchone()
        self.assertEqual(row['state'],'queued')
        self.assertEqual(json.loads(row['spec'])['output_dir'],str(self.out))

    def test_static_index_served_with_csp(self):
        conn = UnixConnection(self.sockpath)
        try:
            conn.request('GET', '/app/video-enhance/')
            r = conn.getresponse(); body = r.read()
            self.assertEqual(r.status, 200)
            self.assertIn("Content-Security-Policy", ' '.join(f'{k}: {v}' for k, v in r.getheaders()))
            self.assertIn(b'app.js', body)
        finally:
            conn.close()
if __name__=='__main__':unittest.main()
