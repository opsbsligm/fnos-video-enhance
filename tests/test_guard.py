import sys,time,unittest,copy,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import guard
from unittest.mock import patch
class GuardTests(unittest.TestCase):
 def setUp(self):
  self.g=dict(uuid='G',temperature=79,total_mib=16384,used_mib=12049,utilization=0,processes=[dict(pid=1,memory_mib=12049)])
  self.p=dict(validated_shared_gpus=['G'],protected_targets=[dict(pid=1)],max_temperature=80,budget_mib=1024,reserve_mib=2048)
  self.i=dict(available=True,updated_at=100,processing=0,deferred=0,last_active_at=90)
 def reason(self):return guard.evaluate(self.g,self.p,self.i,now=100)
 def test_idle(self):self.assertEqual(self.reason(),'')
 def test_busy(self):
  for field in ('processing','deferred'):
   self.i[field]=1;self.assertTrue(self.reason());self.i[field]=0
 def test_temperature(self):self.g['temperature']=80;self.assertTrue(self.reason())
 def test_memory(self):self.g['used_mib']=14000;self.assertTrue(self.reason())
 def test_unknown(self):self.g['processes'].append(dict(pid=2,memory_mib=1));self.assertTrue(self.reason())
 def test_headroom_allows_foreign(self):
  self.g['processes'].append(dict(pid=2,memory_mib=1));self.p['headroom_mode']=True;self.assertEqual(self.reason(),'')
  self.g['temperature']=90;self.assertTrue(self.reason())
  self.g['temperature']=70;self.g['used_mib']=15000;self.assertTrue(self.reason())
 def test_full_power(self):
  self.p.update(headroom_mode=True,full_power=True,gpu_duty=1.0,budget_mib=0,max_start_utilization=70)
  self.g['utilization']=95;self.g['processes'].append(dict(pid=2,memory_mib=9000))
  self.assertEqual(self.reason(),'')  # 全功率：忙碌与并发进程均不拦截
  self.g['temperature']=85;self.assertTrue(self.reason())  # 温度闸门仍生效
 def test_headroom_budget_zero_skips_own_check(self):
  self.p.update(headroom_mode=True,budget_mib=0);self.g['processes'].append(dict(pid=7,memory_mib=9000))
  self.assertEqual(guard.evaluate(self.g,self.p,self.i,own_pid=7,now=100),'')
 def test_missing_model(self):self.g['processes']=[];self.assertTrue(self.reason())
 def test_stale(self):self.i['updated_at']=98;self.assertTrue(self.reason())
 def test_unavailable(self):self.i['available']=False;self.assertTrue(self.reason())
 def test_unvalidated(self):self.p['validated_shared_gpus']=[];self.assertTrue(self.reason())
 def test_pilot_expiry(self):
  self.p.update(validated_shared_gpus=[],pilot_gpus=['G'],pilot_expires_at=101);self.assertEqual(self.reason(),'')
  self.p['pilot_expires_at']=99;self.assertTrue(self.reason())
 def test_metrics(self):
  text='\n'.join(f'llamacpp:{k} 0' for k in guard.METRICS);self.assertEqual(len(guard.parse_metrics(text)),4)
  for bad in ('NaN','Inf','1e309','-1'):
   with self.assertRaises(ValueError):guard.parse_metrics(text.replace('requests_processing 0','requests_processing '+bad))
  with self.assertRaises(ValueError):guard.parse_metrics('')
 def test_gpu_timestamp_tracks_measurement_not_metrics_start(self):
  with patch.object(guard.subprocess,'check_output',side_effect=['G,P100,16384,12049,78,90\n','G,1,llama,12049\n']),patch.object(guard.time,'time',return_value=123):
   result=guard.raw_snapshot()
   self.assertTrue(result['available']);self.assertEqual(result['sampled_at'],123)
   self.assertEqual(result['gpus'][0]['temperature'],78)
 def test_real_stale_or_failed_gpu_sampling_still_stops_worker(self):
  with patch.object(guard,'STATE') as state,patch.object(guard.time,'time',return_value=100),patch.object(guard,'policy',return_value=self.p):
   state.read_text.return_value=json.dumps(dict(updated_at=99,available=True,gpus=[self.g],inference=self.i))
   self.assertEqual(guard.require_gpu('G')['uuid'],'G')
   state.read_text.return_value=json.dumps(dict(updated_at=97,available=True,gpus=[self.g],inference=self.i))
   with patch.object(guard,'raw_snapshot',return_value=dict(available=False,error='采样过期，复核失败')):
    with self.assertRaisesRegex(guard.ProtectedWork,'采样过期'):guard.require_gpu('G')
   state.read_text.return_value=json.dumps(dict(updated_at=99,available=False,gpus=[],error='GPU指标采集失败：TimeoutExpired'))
   with self.assertRaisesRegex(guard.ProtectedWork,'TimeoutExpired'):guard.require_gpu('G')
 def test_stale_monitor_rechecks_real_gpu_without_refreshing_file(self):
  self.p.update(headroom_mode=True)
  with patch.object(guard,'STATE') as state,patch.object(guard.time,'time',return_value=100),patch.object(guard,'policy',return_value=self.p),patch.object(guard,'raw_snapshot') as sample:
   state.read_text.return_value=json.dumps(dict(updated_at=97,available=True,gpus=[self.g],inference=self.i))
   sample.return_value=dict(available=True,sampled_at=100,gpus=[self.g])
   self.assertEqual(guard.require_gpu('G')['uuid'],'G')
   state.write_text.assert_not_called()
   sample.assert_called_once()
   hot=dict(self.g,temperature=90)
   sample.return_value=dict(available=True,sampled_at=100,gpus=[hot])
   with self.assertRaisesRegex(guard.ProtectedWork,'温度保护'):guard.require_gpu('G')
   sample.return_value=dict(available=True,sampled_at=97,gpus=[self.g])
   with self.assertRaisesRegex(guard.ProtectedWork,'复核采样过期'):guard.require_gpu('G')
 def test_gpu_recheck_does_not_bypass_shared_inference_age(self):
  with patch.object(guard,'STATE') as state,patch.object(guard.time,'time',return_value=100),patch.object(guard,'policy',return_value=self.p),patch.object(guard,'raw_snapshot',return_value=dict(available=True,sampled_at=100,gpus=[self.g])):
   old=dict(self.i,updated_at=97)
   state.read_text.return_value=json.dumps(dict(updated_at=97,available=True,gpus=[self.g],inference=old))
   with self.assertRaisesRegex(guard.ProtectedWork,'推理采样过期'):guard.require_gpu('G')
if __name__=='__main__':unittest.main()
