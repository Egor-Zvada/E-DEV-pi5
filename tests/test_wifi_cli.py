import contextlib
import importlib.machinery
import importlib.util
import io
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('cli', str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev'))
c = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(c)


class WifiTests(unittest.TestCase):
    def test_route_check_keeps_local_lan_direct_even_with_vpn_domain_conflict(self):
        with patch.object(c,'root'),patch.object(c,'mode',return_value='hybrid'),patch.object(c.runpy,'run_path',return_value={'local_routes':lambda:[('192.168.100.0/24','eth0')]}),patch.object(c,'set_contains',return_value=True),contextlib.redirect_stdout(io.StringIO()) as output:
            c.route_check('192.168.100.52')
        self.assertIn('локальная сеть · eth0',output.getvalue())
        self.assertNotIn('→ VPN',output.getvalue())

    def test_route_check_uses_direct_dns_when_tunnel_is_not_required(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(c,'BASE',pathlib.Path(tmp)),patch.object(c,'root'),patch.object(c,'mode',return_value='direct'),patch.object(c.runpy,'run_path',return_value={'local_routes':lambda:[]}),patch.object(c,'out',return_value='203.0.113.9') as out,patch.object(c,'set_contains',return_value=False),contextlib.redirect_stdout(io.StringIO()) as output:
            c.route_check('example.test')
        self.assertIn('53',out.call_args.args[0])
        self.assertIn('провайдер (VPN OFF)',output.getvalue())

    def test_partial_legacy_system_settings_keep_default_user_and_explicit_values(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c,'BASE',pathlib.Path(tmp)):
            system=pathlib.Path(tmp)/'system.json'
            system.write_text('{"ssid":"My AP"}')
            self.assertEqual(c.local_user(),'user')
            self.assertEqual(c.system_config()['ssid'],'My AP')
            system.write_text('{"user":"developer","ssid":"My AP"}')
            self.assertEqual(c.local_user(),'developer')

    def test_settings_route_off_does_not_stop_tunnel(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'PROFILES', pathlib.Path(tmp)), patch.object(c, 'root'), patch.object(c, 'run', return_value=types.SimpleNamespace(returncode=0)) as run:
            with self.assertRaises(SystemExit): c.wvpn(['off'])
        run.assert_called_once_with(['/usr/local/sbin/e-dev-vpn','off'])

    def test_wifi_status_shows_hybrid_even_if_tunnel_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base/'vpn-enabled').write_text('off')
            with patch.object(c, 'BASE', base), patch.object(c, 'mode', return_value='hybrid'), patch.object(c, 'selected', return_value='main'), patch.object(c, 'vpn_config', return_value={'mode':'manual'}), patch.object(c, 'out', side_effect=['100 (connected)','E-DEV-AP']), patch.object(c, 'ips', return_value=[('wlan0','192.168.50.1')]), contextlib.redirect_stdout(io.StringIO()) as output:
                c.wifi_status()
            self.assertIn('HYBRID', output.getvalue())
            self.assertIn('ВЫКЛЮЧЕН', output.getvalue())
            self.assertIn('VPN-сервер: main', output.getvalue())
            self.assertIn('Wi-Fi AP: ВКЛЮЧЕНА', output.getvalue())
            self.assertIn('Адрес OUT: 192.168.50.1', output.getvalue())

    def test_links_use_current_physical_interface_addresses_only(self):
        with patch.object(c, 'ips', return_value=[('eth0','192.168.55.8'),('wlan0','172.16.7.1'),('wlan1','192.168.60.8'),('awg0','10.8.0.2'),('incusbr0','10.158.92.1')]), contextlib.redirect_stdout(io.StringIO()) as output:
            c.links()
        links = output.getvalue()
        self.assertIn('https://192.168.55.8:9443/', links)
        self.assertIn('https://172.16.7.1:8443/ui/', links)
        self.assertIn('smb://192.168.60.8/Share', links)
        for old in ('.local', '10.42.0.1', '10.8.0.2', '10.158.92.1'):
            self.assertNotIn(old, links)

    def test_short_status_names_the_mode_without_routing_explanation(self):
        for mode,expected in [('hybrid','HYBRID'),('vpn','VPN ON'),('direct','VPN OFF')]:
            with patch.object(c,'mode',return_value=mode),patch.object(c,'selected',return_value='main'),patch.object(c,'vpn_config',return_value={'mode':'manual'}),patch.object(c,'out',return_value=''),patch.object(c,'management_ips',return_value=[]),contextlib.redirect_stdout(io.StringIO()) as output:
                c.status_text()
            self.assertIn('Wi-Fi: '+expected+'\n',output.getvalue())
            self.assertNotIn('российские IP',output.getvalue())
