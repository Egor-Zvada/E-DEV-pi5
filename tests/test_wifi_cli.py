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
            with patch.object(c, 'BASE', base), patch.object(c, 'mode', return_value='hybrid'), patch.object(c, 'selected', return_value='main'), patch.object(c, 'vpn_config', return_value={'mode':'manual'}), patch.object(c, 'out', side_effect=['100 (connected)','E-DEV-AP']), contextlib.redirect_stdout(io.StringIO()) as output:
                c.wifi_status()
            self.assertIn('HYBRID', output.getvalue())
            self.assertIn('ВЫКЛЮЧЕН', output.getvalue())
            self.assertIn('VPN-сервер: main', output.getvalue())
            self.assertIn('Wi-Fi AP: ВКЛЮЧЕНА', output.getvalue())
