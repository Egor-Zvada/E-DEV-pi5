import importlib.machinery
import importlib.util
import pathlib
import subprocess as S
import tempfile
import unittest
from unittest.mock import patch

loader=importlib.machinery.SourceFileLoader('radio',str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-radio'))
r=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(r)


class RadioTests(unittest.TestCase):
    def test_three_failures_required_and_healthy_or_disabled_clears_them(self):
        state={}
        for stamp in (1000,1030):
            state,act=r.decision(state,'unresponsive',stamp,'boot');self.assertFalse(act)
        state,act=r.decision(state,'unresponsive',1060,'boot');self.assertTrue(act)
        for observation in ('healthy','disabled','inactive'):
            updated,act=r.decision(state,observation,1090,'boot')
            self.assertEqual(updated['failures'],0);self.assertFalse(act)

    def test_recovery_is_bounded_and_resets_across_reboots(self):
        state=dict(boot='boot',failures=20,last_attempt=1000,attempts=[1000])
        self.assertFalse(r.decision(state,'unresponsive',1100,'boot')[1])
        self.assertTrue(r.decision(state,'unresponsive',1400,'boot')[1])
        state['attempts']=[1000,1300,1600]
        self.assertFalse(r.decision(state,'unresponsive',2000,'boot')[1])
        self.assertFalse(r.decision(state,'unresponsive',2000,'newboot')[1])

    def test_empty_station_list_is_healthy_and_does_not_require_a_client_ping(self):
        def call(*args,**kw):
            out={('nmcli','radio','wifi'):'enabled',('nmcli','-g','802-11-wireless.mode','con','show','E-DEV-AP'):'ap',('nmcli','-g','GENERAL.STATE','device','show','wlan0'):'100 (connected)',('iw','dev','wlan0','info'):'type AP'}.get(args,'')
            return S.CompletedProcess(args,0,out,'')
        with patch.object(r,'call',side_effect=call):self.assertEqual(r.radio_state(),'healthy')

    def test_intentionally_disabled_wifi_is_not_restarted(self):
        with patch.object(r,'call',return_value=S.CompletedProcess([],0,'disabled','')):
            self.assertEqual(r.radio_state(),'disabled')

    def test_shared_driver_with_an_input_is_never_unloaded(self):
        with patch.object(r,'other_broadcom_inputs',return_value=True),patch.object(r,'call') as call:
            self.assertFalse(r.reconnect());call.assert_not_called()

    def test_failure_during_unload_does_not_touch_ethernet_or_vpn(self):
        with patch.object(r,'other_broadcom_inputs',return_value=False),patch.object(r,'call',return_value=S.CompletedProcess([],1,'','')) as call,patch.object(r,'activate',return_value=True) as activate:
            self.assertTrue(r.reconnect());activate.assert_called_once()
            commands=[c.args for c in call.call_args_list]
            self.assertEqual(commands[0],('nmcli','--wait','10','con','down','E-DEV-AP'))
            self.assertEqual(commands[1][:2],('modprobe','-r'))
            self.assertEqual(commands[1][-1],'brcmfmac')
            self.assertEqual(len(commands),2)
            self.assertTrue(set(commands[1][2:]).issubset({'brcmfmac','brcmfmac_cyw','brcmfmac_wcc','brcmfmac_bca'}))

    def test_state_is_private_and_uses_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'STATE',pathlib.Path(tmp)/'radio.json'):
            r.save(dict(failures=3))
            self.assertEqual(r.read(r.STATE,{}),dict(failures=3))
            self.assertEqual(r.STATE.stat().st_mode&0o777,0o600)
            self.assertFalse(r.STATE.with_suffix('.new').exists())
