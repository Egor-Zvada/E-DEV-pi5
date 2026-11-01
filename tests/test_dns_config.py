import importlib.machinery
import importlib.util
import pathlib as P
import subprocess as S
import tempfile
import unittest
from unittest.mock import patch

loader=importlib.machinery.SourceFileLoader('dns_config',str(P.Path(__file__).resolve().parents[1]/'src/e-dev-dns-config'))
d=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(d)


class DNSConfigTests(unittest.TestCase):
    def test_cache_and_client_ttl_expire_before_domain_routing_sets(self):
        text=(P.Path(__file__).resolve().parents[1]/'config/e-dev-dns-resilience.conf').read_text()
        values=dict(line.split('=',1) for line in text.splitlines() if line and not line.startswith('#'))
        stale=int(values['use-stale-cache'])
        self.assertLess(int(values['max-cache-ttl'])+stale,3600)
        self.assertLess(int(values['max-ttl'])+stale,3600)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=P.Path(self.tmp.name)
        (self.base/'hybrid.json').write_text('{"dns":["1.1.1.1","8.8.8.8"]}')
        (self.base/'wifi.json').write_text('{"address":"172.16.7.1/24"}')
        (self.base/'direct-domains').write_text('ozon.ru\nru\n')
        (self.base/'vpn-domains').write_text('googlevideo.com\nyoutube.com\n')

    def test_client_dns_stays_bound_to_vpn_and_keeps_both_domain_sets(self):
        vpn,shared=d.render(self.base)
        self.assertIn('listen-address=172.16.7.1',vpn)
        self.assertIn('server=1.1.1.1@awg0',vpn)
        self.assertIn('nftset=/ozon.ru/ru/4#inet#e_dev_split#ru4',vpn)
        self.assertIn('nftset=/googlevideo.com/youtube.com/4#inet#e_dev_split#vpn4',vpn)
        self.assertNotIn('@awg0',shared)
        self.assertIn('no-resolv',shared)

    def test_both_files_are_validated_before_replacing_either(self):
        target=self.base/'dns.conf';shared=self.base/'shared.conf'
        target.write_text('old-vpn');shared.write_text('old-direct')
        with patch.object(d.S,'run',side_effect=[S.CompletedProcess([],0,'',''),S.CompletedProcess([],1,'','invalid')]):
            with self.assertRaises(ValueError):d.configure(self.base,shared)
        self.assertEqual(target.read_text(),'old-vpn');self.assertEqual(shared.read_text(),'old-direct')
        self.assertFalse(list(self.base.glob('*.new')))

    def test_bad_domain_or_server_never_writes_active_files(self):
        (self.base/'vpn-domains').write_text('youtube.com/4#inet#injection')
        with self.assertRaises(ValueError):d.render(self.base)
        (self.base/'vpn-domains').write_text('youtube.com')
        (self.base/'hybrid.json').write_text('{"dns":["127.0.0.1"]}')
        with self.assertRaises(ValueError):d.render(self.base)

    def test_temporary_files_are_private_and_successful_files_match(self):
        def validate(args,**kw):
            path=P.Path(args[-1].split('=',1)[1]);self.assertEqual(path.stat().st_mode&0o777,0o600)
            return S.CompletedProcess(args,0,'','')
        with patch.object(d.S,'run',side_effect=validate):d.configure(self.base,self.base/'shared.conf')
        self.assertEqual((self.base/'dns.conf').read_text(),d.render(self.base)[0])
