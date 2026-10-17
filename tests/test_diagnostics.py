import importlib.machinery
import importlib.util
import io
import contextlib
import json
import os
import pathlib as P
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = P.Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('diagnostics_test', str(ROOT/'src/e-dev-diagnostics'))
d = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(d)
loader = importlib.machinery.SourceFileLoader('diagnostics_cli_test', str(ROOT/'src/e-dev'))
cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(cli)
CLIENT = '4c:31:2d:91:3e:6a'


def sample(stamp=100):
    return dict(time=stamp, maintenance=False, temperature_c=60, throttled=0,
                ap_forwarding=True, stations=[], services={})


def event(phase, stamp=100, interface='edevbr0'):
    return dict(time=stamp, kind='dhcp', phase=phase, interface=interface, mac=CLIENT)


class EventTests(unittest.TestCase):
    def test_dhcp_extracts_metadata_without_recording_message_body(self):
        row = dict(MESSAGE='DHCPACK(edevbr0) 10.42.0.43 '+CLIENT+' arbitrary-hostname',
                   __REALTIME_TIMESTAMP='123000000')
        parsed = d.journal_event(row)
        self.assertEqual(parsed, dict(event('ACK', 123),address='10.42.0.43'))
        self.assertNotIn('hostname', json.dumps(parsed))
        self.assertIsNone(d.journal_event(dict(MESSAGE='unrelated private information')))
        self.assertIsNone(d.journal_event(dict(MESSAGE=[80, 81])))

    def test_wireless_events_and_authentication_are_recorded(self):
        self.assertEqual(d.journal_event(dict(MESSAGE='wlan0: AP-STA-DISCONNECTED '+CLIENT))['mac'],CLIENT)
        self.assertEqual(d.wireless_event('wlan0: del station '+CLIENT)['kind'],'wireless-event')
        self.assertIsNone(d.wireless_event('scan started'))

    def test_service_errors_redact_quoted_and_unquoted_secrets_and_controls(self):
        text = '''failed password="secret words" psk=abc PrivateKey: key token='another token'\x1b[31m'''
        parsed = d.journal_event(dict(MESSAGE=text, _SYSTEMD_UNIT='e-dev-dns.service'))
        for secret in ('secret words', 'abc', ' key', 'another token', '\x1b'):
            self.assertNotIn(secret, parsed['message'])
        self.assertIn('[hidden]', parsed['message'])

    def test_wifi_counter_parsing_does_not_invent_missing_signal(self):
        row = d.stations('Station '+CLIENT+' (on wlan0)\n\ttx bytes:\t100\n\ttx bitrate:\t1.0 MBit/s\n')[0]
        self.assertEqual(row['tx_bytes'], 100)
        self.assertEqual(row['tx_mbps'], 1)
        self.assertNotIn('signal_dbm', row)
        self.assertEqual(d.stations(''), [])

    def test_configuration_never_reads_profile_contents(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(d, 'B', P.Path(tmp)):
            (d.B/'profiles').mkdir();(d.B/'profiles/main.conf').write_text('PrivateKey = must-not-appear')
            (d.B/'selected-profile').write_text('main')
            (d.B/'wifi.json').write_text(json.dumps(dict(address='172.16.7.1/24', interface='edevbr0', psk='also-secret')))
            result = d.config()
        self.assertEqual(result['ap'], '172.16.7.1')
        self.assertNotIn('secret', str(result));self.assertNotIn('must-not-appear', str(result))

    def test_health_and_benchmark_locks_do_not_hide_real_outages(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(d,'LOCK_ROOT',P.Path(tmp)):
            with open(P.Path(tmp)/'e-dev-policy.lock','w') as policy:
                d.fcntl.flock(policy,d.fcntl.LOCK_EX)
                self.assertFalse(d.maintenance())
                with open(P.Path(tmp)/'e-dev-wifi.lock','w') as wifi:
                    d.fcntl.flock(wifi,d.fcntl.LOCK_EX)
                    self.assertTrue(d.maintenance())


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.detector = d.Detector();self.detector.grace_until = 0

    def test_stalled_exchange_is_saved_as_an_observation_with_cooldown(self):
        for phase, stamp in (('DISCOVER',100),('OFFER',101),('DISCOVER',102),('OFFER',103)):
            self.detector.dhcp(event(phase,stamp),'edevbr0')
        self.assertEqual(self.detector.check(sample(129)), [])
        issues = self.detector.check(sample(131))
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]['kind'],'dhcp:'+CLIENT)
        self.assertEqual(self.detector.check(sample(150)), [])

    def test_ack_closes_exchange_and_incus_dhcp_is_ignored(self):
        for phase in ('DISCOVER','OFFER','DISCOVER','ACK'):
            self.detector.dhcp(event(phase),'edevbr0')
        self.assertEqual(self.detector.check(sample(200)), [])
        self.detector.dhcp(event('DISCOVER',interface='incusbr0'),'edevbr0')
        self.assertEqual(self.detector.pending,{})

    def test_replayed_journal_line_is_not_a_second_discover(self):
        for _ in range(2):
            self.detector.dhcp(event('DISCOVER'),'edevbr0')
            self.detector.dhcp(event('OFFER',101),'edevbr0')
        self.assertEqual(self.detector.check(sample(140)), [])

    def test_planned_changes_clear_failures_and_allow_a_reconnect_grace(self):
        bad=sample();bad['ap_forwarding']=False
        self.detector.check(bad);self.detector.check(bad)
        changed=dict(bad,maintenance=True,time=110)
        self.assertEqual(self.detector.check(changed), [])
        self.assertEqual(self.detector.check(dict(bad,time=140)), [])
        self.assertEqual(self.detector.check(dict(bad,time=156)), [])

    def test_probe_threshold_counts_actual_tests_not_cached_samples(self):
        bad={'vpn':dict(ok=False)}
        self.assertEqual(self.detector.check(sample(100),bad), [])
        for stamp in range(110,160,10):
            self.assertEqual(self.detector.check(sample(stamp)), [])
        self.assertEqual(self.detector.check(sample(160),bad), [])
        self.assertEqual(self.detector.check(sample(220),bad)[0]['kind'],'probe:vpn')

    def test_sleeping_or_departed_client_is_not_an_incident(self):
        for stamp in range(100,500,10):
            self.assertEqual(self.detector.check(sample(stamp)), [])

    def test_short_probe_failure_recovers_and_expected_disabled_probe_is_skipped(self):
        self.detector.check(sample(100),{'vpn':dict(ok=False)})
        self.detector.check(sample(160),{'vpn':dict(ok=True)})
        self.assertEqual(self.detector.check(sample(220),{'vpn':dict(ok=False)}), [])
        self.assertEqual(self.detector.check(sample(280),{'vpn':dict(ok=False,skipped=True)}), [])

    def test_heat_and_historical_hardware_bits_are_retained(self):
        hot=sample();hot.update(temperature_c=81,throttled=0x10000)
        kinds={row['kind'] for row in self.detector.check(hot)}
        self.assertEqual(kinds,{'hardware-flags','temperature'})
        self.assertEqual(self.detector.check(dict(hot,time=110)), [])

    def test_noisy_dhcp_cache_stays_bounded(self):
        for n in range(600):
            row=event('DISCOVER',n);row['mac']=f'00:00:00:00:{n//256:02x}:{n%256:02x}'
            self.detector.dhcp(row,'edevbr0')
        self.assertEqual(len(self.detector.pending),512)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=P.Path(self.tmp.name)/'diagnostics';self.store=d.Store(self.path)

    def test_rotation_quota_permissions_and_latest_record(self):
        with patch.object(d,'ROTATE_BYTES',200),patch.object(d,'MAX_BYTES',800):
            for n in range(50):self.store.append('samples',dict(index=n,value='x'*60))
            self.assertLessEqual(sum(p.stat().st_size for p in self.store.managed()),800)
            self.assertEqual(self.store.tail('samples',1)[0]['index'],49)
        self.assertEqual(self.path.stat().st_mode&0o777,0o700)
        self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in self.store.managed()))

    def test_partial_power_loss_line_does_not_swallow_next_sample(self):
        path=self.path/'samples.jsonl';path.write_text('{incomplete')
        self.store.append('samples',dict(index=5))
        self.assertEqual(self.store.tail('samples',2),[dict(index=5)])

    def test_snapshot_retention_and_unrelated_files_are_preserved(self):
        unrelated=self.path/'keep-this.txt';unrelated.write_text('owner data')
        with patch.object(d,'command',return_value=(0,'[]')):
            for n in range(15):self.store.snapshot(dict(time=100,kind='manual',observation=str(n)),sample())
        snapshots=[p for p in self.store.managed() if p.name.startswith('incident-')]
        self.assertEqual(len(snapshots),12);self.assertTrue(unrelated.exists())
        for path in snapshots:os.utime(path,(time.time()-8*86400,)*2)
        self.store.prune();self.assertEqual(len(self.store.managed()),0)
        self.assertTrue(unrelated.exists())

    def test_symlink_log_is_rejected_without_touching_target(self):
        target=P.Path(self.tmp.name)/'target';target.write_text('unchanged')
        (self.path/'events.jsonl').symlink_to(target)
        with self.assertRaises(ValueError):self.store.append('events',dict(test=True))
        self.assertEqual(target.read_text(),'unchanged')

    def test_oversized_snapshot_is_rejected(self):
        with patch.object(d,'MAX_BYTES',10):
            with self.assertRaises(ValueError):self.store.prune(11)

    def test_cli_allows_only_public_diagnostics_operations_before_sudo(self):
        for sub in ('status','last','events','snapshot'):
            with patch.object(cli.sys,'argv',['e-dev','diagnostics',sub]),patch.object(cli,'root'), \
                 patch.object(cli,'run') as run:
                run.return_value.returncode=0
                with self.assertRaises(SystemExit):cli.main()
                self.assertEqual(run.call_args.args[0],['/usr/local/sbin/e-dev-diagnostics',sub])
        with patch.object(cli.sys,'argv',['e-dev','diagnostics','daemon']),patch.object(cli,'root') as root, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):cli.main()
            root.assert_not_called()


class ProbeTests(unittest.TestCase):
    def test_network_tests_are_bound_to_the_requested_interface(self):
        with patch.object(d,'command',side_effect=[(7,''),(0,'200')]) as run:
            self.assertTrue(d.http_probe('awg0')['ok'])
            for call in run.call_args_list:
                args=call.args;self.assertEqual(args[args.index('--interface')+1],'awg0')
        self.assertEqual(d.http_probe(None),dict(ok=False,reason='no-interface'))

    def test_dns_requires_both_success_status_and_an_address(self):
        with patch.object(d,'command',return_value=(0,'status: NOERROR\nexample.com. 60 IN A 1.2.3.4')):
            self.assertTrue(d.dns_probe('10.42.0.1',1053)['ok'])
        with patch.object(d,'command',return_value=(0,'status: SERVFAIL')):
            self.assertFalse(d.dns_probe('10.42.0.1',1053)['ok'])


if __name__=='__main__':unittest.main()
