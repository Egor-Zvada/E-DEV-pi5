import importlib.machinery
import importlib.util
import pathlib
import json
import types
import unittest
import tempfile
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('routing', str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-vpn'))
r = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(r)


class RoutingTests(unittest.TestCase):
    def test_lan_follows_physical_uplink_and_excludes_tunnel_routes(self):
        rows = [{'dev':'eth0', 'dst':'192.168.55.0/24', 'scope':'link', 'protocol':'kernel'},
                {'dev':'wlan1', 'dst':'172.16.5.0/24', 'scope':'link', 'protocol':'kernel'},
                {'dev':'awg0', 'dst':'10.9.0.0/24', 'scope':'link', 'protocol':'kernel'},
                {'dev':'eth0', 'dst':'default', 'gateway':'192.168.55.1', 'protocol':'dhcp'}]
        with patch.object(r, 'call', return_value=types.SimpleNamespace(stdout=json.dumps(rows))):
            routes = r.local_routes()
            guard = r.rules('vpn')
        self.assertIn(('192.168.55.0/24','eth0'), routes)
        self.assertIn(('172.16.5.0/24','wlan1'), routes)
        self.assertNotIn(('10.9.0.0/24','awg0'), routes)
        self.assertIn('192.168.55.0/24', guard)
        self.assertNotIn('192.168.100.0/24', guard)

    def test_direct_ap_still_protects_vpn_containers_and_hybrid_marks_only_ap(self):
        with patch.object(r, 'local_routes', return_value=[('10.42.0.0/24','wlan0')]):
            direct = r.rules('direct')
            hybrid = r.rules('hybrid')
            blocked = r.rules('blocked')
        self.assertIn('ip saddr 10.43.0.0/24 jump protect', direct)
        self.assertNotIn('ip saddr { 10.43.0.0/24, 10.42.0.0/24 } jump protect', direct)
        self.assertIn('ip saddr 10.42.0.0/24 meta mark 0x42 accept', hybrid)
        self.assertNotIn('meta mark 0x42 accept', blocked)
        self.assertNotIn('oifname "awg0" accept', blocked)
        self.assertIn('counter drop', blocked)

    def test_configured_ap_subnet_updates_guard_split_dns_and_policy_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base/'wifi.json').write_text('{"address":"172.16.7.1/24"}')
            with patch.object(r, 'B', base), patch.object(r, 'local_routes', return_value=[('172.16.7.0/24','wlan0')]), patch.object(r, 'call') as call:
                call.return_value = types.SimpleNamespace(stdout='[]', returncode=1)
                guard = r.rules('hybrid')
                r.split('hybrid')
                r.routes('hybrid')
                batches = '\n'.join(c.kwargs.get('input','') for c in call.call_args_list)
                commands = [c.args for c in call.call_args_list]
            self.assertNotIn('10.42.', guard+batches)
            self.assertIn('172.16.7.1:1053', guard)
            self.assertIn('iifname "wlan0" ip saddr != 172.16.7.0/24 counter drop', guard)
            self.assertIn('ip saddr 172.16.7.0/24 ip daddr @rusubnets', batches)
            self.assertIn(('ip','rule','add','priority','10420','from','172.16.7.0/24','lookup','1042'), commands)
