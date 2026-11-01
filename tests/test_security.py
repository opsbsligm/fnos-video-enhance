import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from security import open_beneath,matching_root,content_allowed
import guard
class Security(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.r=Path(self.t.name).resolve();(self.r/'video.mkv').write_bytes(b'ok');(self.r/'link').symlink_to('/etc/passwd')
 def tearDown(self):self.t.cleanup()
 def test_readonly(self):
  f=open_beneath(self.r,self.r/'video.mkv')
  try:
   self.assertEqual(os.read(f,2),b'ok')
   with self.assertRaises(OSError):os.write(f,b'no')
  finally:os.close(f)
 def test_symlink(self):
  with self.assertRaises(OSError):open_beneath(self.r,self.r/'link')
 def test_fifo(self):
  os.mkfifo(self.r/'pipe.mp4')
  with self.assertRaises(PermissionError):open_beneath(self.r,self.r/'pipe.mp4')
 def test_directory_as_file(self):
  with self.assertRaises(PermissionError):open_beneath(self.r,self.r)
 def test_dotdot(self):
  with self.assertRaises(PermissionError):open_beneath(self.r,self.r/'../no')
 def test_root(self):
  with self.assertRaises(PermissionError):matching_root('/etc/passwd',[str(self.r)])
 def test_content_filter_lifted(self):self.assertTrue(content_allowed('/vol2/1000/NO_SAY/a.mp4'));self.assertTrue(content_allowed('/vol2/1000/缘之空/a.mp4'))
 def test_failclosed(self):
  with patch('subprocess.check_output',side_effect=OSError()):
   self.assertFalse(guard.snapshot()['available'])
   with self.assertRaises(RuntimeError):guard.require_gpu('test')
 def test_foreign(self):
  with patch('subprocess.check_output',side_effect=['GPU-a, P100, 16384, 12000, 65, 0\n','GPU-a, 1, llama-server, 10000\n']):self.assertFalse(guard.snapshot()['gpus'][0]['eligible'])
if __name__=='__main__':unittest.main()
