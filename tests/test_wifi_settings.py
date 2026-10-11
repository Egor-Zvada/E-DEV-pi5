import contextlib
import importlib.machinery
import importlib.util
import json
import io
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('wifi_settings', str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-wifi'))
w = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(w)


class WifiSettingsTests(unittest.TestCase):
    def candidate(self):
        return dict(address='192.168.50.1/24', dhcp_range='192.168.50.100,192.168.50.200', lease=3600)

    def test_early_validation_failure_cleans_request_without_changing_ap(self):
        with self.applying() as (base,request,old_dns,events),patch.object(w,'validate',side_effect=ValueError('invalid')):
            with self.assertRaises(ValueError):w.apply()
            self.assertFalse(request.exists());self.assertEqual(events,[])

    def test_ui_waits_until_systemd_finishes_after_request_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(w,'REQUEST',pathlib.Path(tmp)/'request'), \
             patch.object(w,'call',return_value='activating') as call:
            self.assertTrue(w.applying())
            call.return_value='failed';self.assertFalse(w.applying())

    def test_interruption_rolls_back_before_unblocking(self):
        with self.applying() as (base,request,old_dns,events), \
             patch.object(w,'radio_snapshot',return_value={}), \
             patch.object(w,'radio_modify',side_effect=KeyboardInterrupt), \
             patch.object(w,'radio_restore') as restore,patch.object(w,'radio_activate'):
            request.write_text(json.dumps(dict(self.candidate(),radio=dict(ssid='new',band='bg',channel=11))))
            with self.assertRaises(KeyboardInterrupt):w.apply()
            restore.assert_called_once();self.assertFalse(request.exists())
            self.assertLess(events.index(('verify','10.42.0.1/24')),events.index(('unblock',)))

    def test_radio_validation_and_secret_file_permissions(self):
        data=dict(ssid='My AP',band='bg',channel=11,psk='test-passphrase')
        candidate=dict(self.candidate(),radio=data)
        self.assertEqual(w.validate(candidate,[]),candidate)
        for change in (dict(ssid=''),dict(ssid='a'*33),dict(channel=36),dict(psk='short')):
            with self.assertRaises(ValueError): w.validate_radio(dict(data,**change))
        with tempfile.TemporaryDirectory() as temp:
            path=pathlib.Path(temp)/'request.json'
            w.save(path,candidate,0o600)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_auto_channel_uses_empty_nmcli_value_on_apply_and_restore(self):
        events=[]
        def call(*args,**kw):
            events.append(args)
            if 'wifi.channel' in args:
                self.assertEqual(args[args.index('wifi.channel')+1],'')
            if '802-11-wireless.channel' in args:
                self.assertEqual(args[args.index('802-11-wireless.channel')+1],'')
            return 'test-passphrase' if '--show-secrets' in args else ''
        with patch.object(w,'call',side_effect=call),patch.object(w,'radio_profiles',return_value=['AP']), \
             patch.object(w.runpy,'run_path',return_value={'set_password':lambda *args:None}):
            w.radio_modify(dict(ssid='AP',band='bg',channel=0))
            w.radio_restore({'AP':dict(properties={'802-11-wireless.band':'bg','802-11-wireless.channel':'0'},psk='test-passphrase')})
        self.assertNotIn('test-passphrase',str(events))

    def test_failed_radio_change_restores_radio_password_and_system_settings(self):
        with self.applying(fail_activations=1) as (base,request,old_dns,events):
            system=base/'system.json';system.write_text('{"ssid":"old"}')
            candidate=dict(self.candidate(),radio=dict(ssid='new',band='bg',channel=11))
            w.REQUEST.write_text(json.dumps(candidate))
            old_radio={'profile':{'psk':'test-passphrase','properties':{}}}
            with patch.object(w,'radio_snapshot',return_value=old_radio),patch.object(w,'radio_modify'), \
                 patch.object(w,'radio_restore') as restore,patch.object(w,'radio_activate'):
                with self.assertRaises(subprocess.CalledProcessError): w.apply()
            restore.assert_called_once_with(old_radio)
            self.assertEqual(json.loads(system.read_text())['ssid'],'old')
            self.assertIn(('unblock',),events)

    def test_subnet_rejects_lan_container_and_public_networks(self):
        data = self.candidate()
        self.assertEqual(w.validate(data, [{'dev':'wlan0','dst':'192.168.50.0/24'}]), data)
        for address in ('10.43.0.1/24', '10.158.92.1/24', '192.0.2.1/24', '192.168.50.0/24', '10.0.0.1/8'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                w.validate(dict(data, address=address, dhcp_range=''), [])
        with self.assertRaises(ValueError):
            w.validate(data, [{'dev':'eth0','dst':'192.168.50.0/24'}])

    def test_dhcp_rejects_gateway_broadcast_other_subnet_and_bad_lease(self):
        data = self.candidate()
        for pool in ('192.168.50.1,192.168.50.30', '192.168.50.100,192.168.50.255',
                     '192.168.60.100,192.168.60.200', '192.168.50.200,192.168.50.100'):
            with self.subTest(pool=pool), self.assertRaises(ValueError):
                w.validate(dict(data, dhcp_range=pool), [])
        with self.assertRaises(ValueError):
            w.validate(dict(data, lease=60), [])

    @contextlib.contextmanager
    def applying(self, fail_activations=0, fail_dns=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            base = root/'config'
            base.mkdir()
            old_dns = 'port=1053\nlisten-address=10.42.0.1\nserver=1.1.1.1@awg0\n'
            (base/'dns.conf').write_text(old_dns)
            request = root/'request.json'
            request.write_text(json.dumps(self.candidate()))
            events = []
            failures = fail_activations
            dns_failure = fail_dns

            def call(*args, **kwargs):
                nonlocal failures, dns_failure
                events.append(args)
                if args[:2] == ('nmcli','--wait') and failures:
                    failures -= 1
                    raise subprocess.CalledProcessError(1, args)
                if args == ('systemctl','restart','e-dev-dns') and dns_failure:
                    dns_failure = False
                    raise subprocess.CalledProcessError(1, args)
                return ''

            def guard(mode):
                cfg = json.loads((base/'wifi.json').read_text()) if (base/'wifi.json').exists() else {}
                events.append(('guard',cfg.get('address','10.42.0.1/24')))

            runtime = {'readmode':lambda:'hybrid','routes':lambda mode:events.append(('routes',mode)),'guard':guard}
            old = dict(address='10.42.0.1/24', dhcp_range='', lease=0)
            with patch.object(w,'B',base), patch.object(w,'BACKUPS',root/'backups'), patch.object(w,'REQUEST',request), \
                 patch.object(w,'lock',side_effect=lambda name:contextlib.nullcontext()), \
                 patch.object(w,'current',return_value=old), patch.object(w,'kernel_routes',return_value=[]), \
                 patch.object(w,'call',side_effect=call), patch.object(w.runpy,'run_path',return_value=runtime), \
                 patch.object(w,'block_ap',side_effect=lambda:events.append(('block',))), \
                 patch.object(w,'unblock_ap',side_effect=lambda:events.append(('unblock',))), \
                 patch.object(w,'verify_ap',side_effect=lambda address:events.append(('verify',address))), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                yield base, request, old_dns, events

    def test_apply_updates_dns_and_guard_before_unblocking(self):
        with self.applying() as (base, request, old_dns, events):
            w.apply()
            self.assertEqual(json.loads((base/'wifi.json').read_text()), self.candidate())
            self.assertIn('listen-address=192.168.50.1', (base/'dns.conf').read_text())
            self.assertNotIn('10.42.0.1', (base/'dns.conf').read_text())
            self.assertEqual(events[0], ('block',))
            self.assertIn(('guard','192.168.50.1/24'), events)
            self.assertEqual(events[-2:], [('systemctl','restart','e-dev-dns'),('unblock',)])
            self.assertFalse(request.exists())

    def test_failed_activation_rolls_back_legacy_settings_and_dns(self):
        with self.applying(fail_activations=1) as (base, request, old_dns, events):
            with self.assertRaises(subprocess.CalledProcessError):
                w.apply()
            self.assertFalse((base/'wifi.json').exists())
            self.assertEqual((base/'dns.conf').read_text(), old_dns)
            self.assertIn(('guard','10.42.0.1/24'), events)
            self.assertEqual(events[-1], ('unblock',))
            self.assertFalse(request.exists())

    def test_failed_rollback_keeps_ap_blocked(self):
        with self.applying(fail_activations=2) as (base, request, old_dns, events):
            with self.assertRaises(subprocess.CalledProcessError):
                w.apply()
            self.assertIn(('block',), events)
            self.assertNotIn(('unblock',), events)
            self.assertFalse(request.exists())

    def test_failed_dns_restart_restores_routes_and_old_guard(self):
        with self.applying(fail_dns=True) as (base, request, old_dns, events):
            with self.assertRaises(subprocess.CalledProcessError):
                w.apply()
            self.assertEqual([e for e in events if e[0] == 'guard'],
                             [('guard','192.168.50.1/24'),('guard','10.42.0.1/24')])
            self.assertEqual((base/'dns.conf').read_text(), old_dns)
            self.assertEqual(events[-2:], [('systemctl','restart','e-dev-dns'),('unblock',)])

    def test_bridged_out_settings_use_bridge_and_ignore_own_connected_route(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(w,'B',pathlib.Path(tmp)):
            (pathlib.Path(tmp)/'wifi.json').write_text('{"interface":"edevbr0"}')
            data=self.candidate()
            self.assertEqual(w.validate(data,[{'dev':'edevbr0','dst':'192.168.50.0/24'}]),data)
            with patch.object(w,'call') as call:
                w.modify(data)
            self.assertEqual(call.call_args.args[:4],('nmcli','connection','modify','E-DEV-OUT'))
