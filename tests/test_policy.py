import importlib.util, importlib.machinery, unittest, tempfile, pathlib, contextlib, json, datetime
from unittest.mock import patch
loader=importlib.machinery.SourceFileLoader('policy',str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-policy'))
p=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(p)
class SpeedtestEnvironmentTests(unittest.TestCase):
 def test_background_speedtest_gets_home_when_missing_or_empty(self):
  for environment in ({'PATH':'/usr/bin'}, {'PATH':'/usr/bin','HOME':''}):
   with self.subTest(environment=environment),patch.dict(p.os.environ,environment,clear=True), \
        patch.object(p.pwd,'getpwuid',return_value=type('User',(),{'pw_dir':'/root'})()),patch.object(p.S,'run') as run:
    p.run('/usr/local/bin/speedtest','--interface=awg0',timeout=150)
    self.assertEqual(run.call_args.kwargs['env'],{'PATH':'/usr/bin','HOME':'/root'})
    self.assertEqual(run.call_args.kwargs['timeout'],150)
 def test_explicit_home_is_preserved(self):
  with patch.dict(p.os.environ,{'HOME':'/var/lib/test-speedtest'},clear=True),patch.object(p.S,'run') as run:
   p.run('/usr/local/bin/speedtest','--version')
   self.assertEqual(run.call_args.kwargs['env']['HOME'],'/var/lib/test-speedtest')

class PolicyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.addCleanup(patch.stopall)
  patch.object(p,'B',pathlib.Path(self.tmp.name)).start();(p.B/'profiles').mkdir()
  for name in ('a','b','c'): (p.B/'profiles'/(name+'.conf')).touch()
  (p.B/'selected-profile').write_text('a');(p.B/'vpn-enabled').write_text('on')
  p.save('vpn-policy.json',{'mode':'default','primary':'a','backup':'b'})
  patch.object(p,'alive',return_value=True).start()
  self.sw=patch.object(p,'switch',side_effect=lambda name:(p.B/'selected-profile').write_text(name)).start()
  patch.object(p,'interface',side_effect=lambda name:contextlib.nullcontext('test-'+name)).start()
 def test_manual_and_disabled_never_fail_over(self):
  p.save('vpn-policy.json',{'mode':'manual','primary':'a','backup':'b'})
  with patch.object(p,'probe') as probe: p.health();probe.assert_not_called()
  p.save('vpn-policy.json',{'mode':'default','primary':'a','backup':'b'});(p.B/'vpn-enabled').write_text('off')
  with patch.object(p,'probe') as probe: p.health();probe.assert_not_called()
  self.sw.assert_not_called()
 def test_default_requires_three_failures_and_uses_only_reserve(self):
  with patch.object(p,'probe',side_effect=[False,False,False,True]):
   p.health();p.health();self.sw.assert_not_called();p.health()
  self.sw.assert_called_once_with('b');self.assertEqual(p.read('vpn-health.json',{})['failures'],0)
 def test_default_returns_to_restored_primary(self):
  (p.B/'selected-profile').write_text('b')
  with patch.object(p,'probe',return_value=True):p.health()
  self.sw.assert_called_once_with('a')
 def test_failed_auto_keeps_selection_and_last_successful_ranking(self):
  p.save('vpn-policy.json',{'mode':'auto','primary':'a','backup':'b'})
  p.save('vpn-speeds.json',{'day':'2020-01-01','ranking':['b','a','c']})
  with patch.object(p,'measure',side_effect=lambda n:{'name':n,'ok':False,'error':'offline'}):p.auto(force=True)
  self.sw.assert_not_called();self.assertEqual(p.selected(),'a');self.assertEqual(p.read('vpn-speeds.json',{})['ranking'],['b','a','c'])
 def test_auto_ranks_download_then_ping_and_skips_same_day(self):
  p.save('vpn-policy.json',{'mode':'auto','primary':'a','backup':'b'})
  rows={'a':{'name':'a','ok':True,'mbps':10,'ping_ms':30},'b':{'name':'b','ok':True,'mbps':20,'ping_ms':50},'c':{'name':'c','ok':True,'mbps':20,'ping_ms':25}}
  with patch.object(p,'measure',side_effect=rows.get) as measure:
   p.auto(force=True);p.auto();self.assertEqual(measure.call_count,3)
  self.sw.assert_called_once_with('c');self.assertEqual(p.read('vpn-speeds.json',{})['ranking'],['c','b','a'])
 def test_entering_auto_reuses_today_ranking_and_selects_fastest_available(self):
  p.save('vpn-policy.json',{'mode':'auto','primary':'a','backup':'b'})
  p.save('vpn-speeds.json',{'day':datetime.datetime.now().astimezone().date().isoformat(),'ranking':['b','a']})
  with patch.object(p,'measure') as measure,patch.object(p,'probe',return_value=True):
   p.auto(apply_cached=True);measure.assert_not_called()
  self.sw.assert_called_once_with('b')
if __name__=='__main__':unittest.main()
