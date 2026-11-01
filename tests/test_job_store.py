import sys,os,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from job_store import Store,load_checkpoint,fingerprint
class StoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=os.open(self.tmp.name,os.O_RDONLY|os.O_DIRECTORY);self.s=Store(self.root,'a'*32);self.spec=dict(id='a'*32,source_size=2,source_mtime_ns=1)
 def tearDown(self):self.s.close();os.close(self.root);self.tmp.cleanup()
 def commit(self):
  name,fd=self.s.create('.mkv');os.write(fd,b'test');os.close(fd);self.s.rename(name,'part-000000.mkv')
  cp=dict(version=1,fingerprint=fingerprint(self.spec),frames_done=12,parts=[dict(name='part-000000.mkv',start=0,frames=12,sha256=self.s.sha('part-000000.mkv'))]);self.s.atomic_json('checkpoint.json',cp);return cp
 def test_empty(self):self.assertEqual(load_checkpoint(self.s,self.spec)['frames_done'],0)
 def test_resume(self):self.commit();self.assertEqual(load_checkpoint(self.s,self.spec)['frames_done'],12)
 def test_changed_source(self):
  self.commit();self.spec['source_size']=3
  with self.assertRaises(ValueError):load_checkpoint(self.s,self.spec)
 def test_corrupt(self):
  self.commit();fd=os.open('part-000000.mkv',os.O_WRONLY,dir_fd=self.s.fd);os.write(fd,b'evil');os.close(fd)
  with self.assertRaises(ValueError):load_checkpoint(self.s,self.spec)
 def test_order(self):
  cp=self.commit();cp['parts'][0]['start']=1;self.s.atomic_json('checkpoint.json',cp)
  with self.assertRaises(ValueError):load_checkpoint(self.s,self.spec)
 def test_symlink(self):
  os.symlink('/etc/passwd','evil',dir_fd=self.s.fd)
  with self.assertRaises(OSError):self.s.open_read('evil')
 def test_job_symlink(self):
  os.symlink('/tmp','b'*32,dir_fd=self.root)
  with self.assertRaises(OSError):Store(self.root,'b'*32)
 def test_invalid_parts(self):
  cp=self.commit();cp['parts']=[None];self.s.atomic_json('checkpoint.json',cp)
  with self.assertRaises(ValueError):load_checkpoint(self.s,self.spec)
 def test_bad_name(self):
  with self.assertRaises(ValueError):self.s.read('../evil')

 def test_patch_upgrade_preserves_checkpoint_fingerprint(self):
  from version import VERSION, PIPELINE_VERSIONS, PIPELINE_TAG
  self.assertEqual(VERSION,'0.3.7')
  self.assertEqual(PIPELINE_TAG,'0.3.0-cfr-segments')
  for old in ('0.3.4','0.3.6'):
   self.assertIn(old,PIPELINE_VERSIONS)
   self.spec['pipeline_version']=old
   self.commit()
   upgraded=dict(self.spec,pipeline_version=VERSION)
   self.assertEqual(load_checkpoint(self.s,upgraded)['frames_done'],12)
if __name__=='__main__':unittest.main()
