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
    def test_wifi_vpn_off_routes_ap_without_stopping_tunnel(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'PROFILES', pathlib.Path(tmp)), patch.object(c, 'root'), patch.object(c, 'run', return_value=types.SimpleNamespace(returncode=0)) as run:
            with self.assertRaises(SystemExit): c.wvpn(['vpn','off'])
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
