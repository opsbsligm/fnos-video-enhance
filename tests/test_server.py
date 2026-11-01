import os,sys,tempfile,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
class ServerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory()
  with patch.dict(os.environ,{'TRIM_PKGVAR':cls.temp.name}):
   import server
   cls.server=server
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def test_submission_identity(self):
  s=self.server;data=dict(request_id='a'*64,path='/example',mode='upscale');a=s.request_identity(data,1000);self.assertEqual(a,s.request_identity(data,1000));self.assertNotEqual(a[0],s.request_identity(data,1001)[0]);changed=dict(data,mode='interpolate');self.assertEqual(a[0],s.request_identity(changed,1000)[0]);self.assertNotEqual(a[1],s.request_identity(changed,1000)[1])
  with self.assertRaises(ValueError):s.request_identity(dict(request_id='../bad'),1000)
 def test_probe_limit(self):
  s=self.server;s.PROBE_SLOTS.acquire();s.PROBE_SLOTS.acquire()
  try:
   with self.assertRaisesRegex(RuntimeError,'同时探测'):s.probe_fd(-1)
  finally:s.PROBE_SLOTS.release();s.PROBE_SLOTS.release()
 def test_probe_releases_on_failure(self):
  s=self.server
  with patch.object(s,'_probe_fd',side_effect=ValueError('bad')):
   with self.assertRaises(ValueError):s.probe_fd(-1)
  self.assertTrue(s.PROBE_SLOTS.acquire(False));self.assertTrue(s.PROBE_SLOTS.acquire(False));s.PROBE_SLOTS.release();s.PROBE_SLOTS.release()
 def test_restart_states(self):
  import json
  s=self.server;s.initialize()
  with s.db() as c:
   c.execute('DELETE FROM jobs')
   for jid,state,version in [('new','running','0.3.0'),('old','held','0.1.0'),('pause','paused','0.3.0')]:
    c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(jid,1000,json.dumps(dict(pipeline_version=version)),state,'',0))
  s.initialize()
  with s.db() as c:rows={r['id']:r['state'] for r in c.execute('SELECT id,state FROM jobs')}
  self.assertEqual(rows,dict(new='held',old='interrupted',pause='paused'))
if __name__=='__main__':unittest.main()
