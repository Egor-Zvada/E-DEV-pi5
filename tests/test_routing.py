import importlib.machinery
import importlib.util
import pathlib
import subprocess
import json
import types
import unittest
import tempfile
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('routing', str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-vpn'))
r = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(r)


class RoutingTests(unittest.TestCase):
    def test_hybrid_explicit_direct_then_vpn_domains_then_geoip(self):
        with patch.object(r,'call',return_value=types.SimpleNamespace(returncode=1,stdout='[]')) as call,patch.object(r,'custom_load'),patch.object(r,'geo_load'):
            r.split('hybrid')
        batch=call.call_args.kwargs['input']
        self.assertLess(batch.index('@custom4'),batch.index('@ru4'))
        self.assertLess(batch.index('@ru4'),batch.index('@vpn4'))
        self.assertLess(batch.index('@vpn4'),batch.index('@rusubnets'))
        self.assertIn('@vpn4 meta mark set 0 ct mark set 0x43 counter return',batch)
        self.assertIn('@vpn4_prev',batch)
        self.assertIn('ct state established,related ct mark 0x43 meta mark set 0 return',batch)
        self.assertIn('meta mark set 0 ct mark set 0 return',batch)

    def test_dns_rotation_keeps_previous_generation_and_is_never_early(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'B',pathlib.Path(folder)),patch.object(r,'call') as call:
            r.dns_rotation_state(100,'test-boot')
            self.assertFalse(r.rotate_dns_sets(200,'test-boot'));call.assert_not_called()
            def values(name):return {'elem':['203.0.113.9']} if name=='vpn4' else {'elem':[]}
            with patch.object(r,'dns_set',side_effect=values):
                self.assertTrue(r.rotate_dns_sets(100+r.DNS_ROTATION_SECONDS,'test-boot'))
            batch=call.call_args.kwargs['input']
            self.assertIn('add element inet e_dev_split vpn4_prev { 203.0.113.9 }',batch)
            self.assertIn('delete element inet e_dev_split vpn4 { 203.0.113.9 }',batch)
            self.assertNotIn('flush set inet e_dev_split vpn4\n',batch)
            self.assertNotIn('systemctl',str(call.call_args_list))
            call.reset_mock()
            self.assertFalse(r.rotate_dns_sets(101+r.DNS_ROTATION_SECONDS,'test-boot'));call.assert_not_called()
            self.assertFalse(r.rotate_dns_sets(200+r.DNS_ROTATION_SECONDS,'new-boot'));call.assert_not_called()

    def test_bad_dns_rotation_data_never_flushes_current_sets(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'B',pathlib.Path(folder)),patch.object(r,'call') as call:
            r.dns_rotation_state(0,'test-boot')
            with patch.object(r,'dns_set',return_value={'elem':['not-an-address']}):
                with self.assertRaises(ValueError):r.rotate_dns_sets(r.DNS_ROTATION_SECONDS,'test-boot')
            call.assert_not_called()

    def test_failed_kernel_rotation_cannot_retry_before_cache_grace_expires(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(r,'B',pathlib.Path(folder)),patch.object(r,'dns_set',return_value={'elem':[]}),patch.object(r,'call',side_effect=subprocess.CalledProcessError(1,['nft'])) as call:
            r.dns_rotation_state(0,'test-boot')
            with self.assertRaises(subprocess.CalledProcessError):r.rotate_dns_sets(r.DNS_ROTATION_SECONDS,'test-boot')
            call.reset_mock()
            self.assertFalse(r.rotate_dns_sets(r.DNS_ROTATION_SECONDS+1,'test-boot'));call.assert_not_called()

    def test_tunnel_failure_keeps_home_lan_open_without_an_internet_exit(self):
        with patch.object(r,'local_routes',return_value=[('10.42.0.0/24','wlan0'),('192.168.100.0/24','eth0')]):
            blocked=r.rules('blocked')
        self.assertIn('192.168.100.0/24',blocked)
        self.assertIn('ip daddr { 10.42.0.0/24, 192.168.100.0/24 } accept',blocked)
        self.assertNotIn('oifname "awg0" accept',blocked)
        self.assertIn('counter drop',blocked)

    def test_vpn_route_refresh_preserves_unchanged_lan_route(self):
        previous=[dict(dst='192.168.100.0/24',dev='eth0'),dict(dst='10.42.0.0/24',dev='wlan0')]
        def call(*args,**kw):
            return types.SimpleNamespace(stdout=json.dumps(previous) if args[:4]==('ip','-j','-4','route') else '[]',returncode=1 if args[:3]==('ip','rule','del') else 0)
        with patch.object(r,'local_routes',return_value=[('192.168.100.0/24','eth0'),('10.42.0.0/24','wlan0')]),patch.object(r,'call',side_effect=call) as commands:
            r.routes('hybrid')
        changed=[c.args for c in commands.call_args_list if c.args[:3] in (('ip','route','del'),('ip','route','replace'))]
        self.assertFalse(any('192.168.100.0/24' in c or '10.42.0.0/24' in c for c in changed))

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
