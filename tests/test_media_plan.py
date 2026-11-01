import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from media_plan import video_support,plan,derive_output_name
class MediaTests(unittest.TestCase):
 def video(self,**kw):return dict(width=1280,height=720,avg_frame_rate='24/1',r_frame_rate='24/1',**kw)
 def test_supported(self):self.assertEqual(video_support(self.video()),[])
 def test_rotation(self):self.assertTrue(video_support(self.video(side_data_list=[dict(rotation=-90)])))
 def test_sar(self):self.assertTrue(video_support(self.video(sample_aspect_ratio='4:3')))
 def test_hdr(self):self.assertTrue(video_support(self.video(color_transfer='smpte2084')))
 def test_vfr(self):v=self.video();v['r_frame_rate']='30/1';self.assertTrue(video_support(v))
 def test_odd_size(self):v=self.video();v['width']=1279;self.assertTrue(video_support(v))
 def test_plan(self):
  p=plan(dict(width=1280,height=720,fps=24),'upscale',60,4);self.assertEqual((p['output_width'],p['output_height']),(3840,2160));self.assertTrue(p['supported'])
 def test_plan_both(self):
  p=plan(dict(width=1280,height=720,fps=24),'both',60,4)
  self.assertTrue(p['supported']);self.assertEqual((p['output_width'],p['output_height'],p['output_fps']),(3840,2160,60))
  self.assertFalse(plan(dict(width=3840,height=2160,fps=24),'both',60,2)['supported'])
  self.assertFalse(plan(dict(width=1280,height=720,fps=60),'both',60,2)['supported'])
 def test_noop(self):self.assertFalse(plan(dict(width=3840,height=2160,fps=60),'interpolate',60)['supported']);self.assertFalse(plan(dict(width=3840,height=2160,fps=24),'upscale')['supported'])
 def test_invalid(self):
  with self.assertRaises(ValueError):plan(dict(width=1280,height=720,fps=24),'bad')
 def test_output_name(self):
  self.assertEqual(derive_output_name('/vol2/1000/NO_SAY/探花蜘蛛侠-01.mp4','both',2,60),'探花蜘蛛侠-01[AI超分×2·60fps].mkv')
  self.assertEqual(derive_output_name('/a/b/x.mkv','upscale',4,60),'x[AI超分×4].mkv')
  self.assertEqual(derive_output_name('/a/b/y.ts','interpolate',2,50),'y[50fps].mkv')
  self.assertEqual(derive_output_name('/a/b/....','upscale',2,60),'输出[AI超分×2].mkv')
  self.assertTrue(len(derive_output_name('/a/b/'+('长'*400)+'.mp4','upscale',2,60))<=151+12)
if __name__=='__main__':unittest.main()
