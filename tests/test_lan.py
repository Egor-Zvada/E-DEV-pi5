import copy
import contextlib
import importlib.machinery
import importlib.util
import json
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('lan',str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-lan'))
l = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
loader.exec_module(l)


def plan():
    def entry(key,name,mac,table,kind='ethernet'):
        return dict(id=key,ifname=name,mac=mac,type=kind,profile='E-DEV-IN-'+key,table=table,
                    method='dhcp',address='',gateway='',dns=['1.1.1.1'],ssid='example-network' if kind=='wifi' else '')
    return dict(version=1,inputs={'eth0':entry('eth0','eth0','02:00:00:00:00:01',5000),
                 'usb-a':entry('usb-a','wlan1','02:00:00:00:00:02',5001,'wifi'),
                 'usb-b':entry('usb-b','eth1','02:00:00:00:00:03',5002)},
                outputs={'wlan0':dict(id='wlan0',ifname='wlan0',mac='02:00:00:00:00:04',
                                     type='wifi',profile='E-DEV-AP')},primary='eth0',backups=['usb-a'],failover=True)


class LanTests(unittest.TestCase):
    def test_builtin_interfaces_and_zones_are_immutable(self):
        p=plan()
        self.assertEqual(l.validate(p),p)
        for key,zone in [('eth0','inputs'),('wlan0','outputs')]:
            changed=copy.deepcopy(p)
            del changed[zone][key]
            with self.assertRaises(ValueError): l.validate(changed)
        changed=copy.deepcopy(p)
        changed['outputs']['usb-b']=dict(changed['inputs']['usb-b'])
        with self.assertRaises(ValueError): l.validate(changed)

    def test_input_tables_and_backup_roles_are_unique(self):
        for change in ('table','primary','backups'):
            p=plan()
            if change=='table': p['inputs']['usb-a']['table']=5000
            elif change=='primary': p['primary']='wlan0'
            else: p['backups']=['eth0']
            with self.assertRaises(ValueError): l.validate(p)

    def test_static_input_rejects_output_overlap_and_wrong_gateway(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(l,'B',pathlib.Path(tmp)):
            p=plan()
            entry=p['inputs']['usb-b']
            entry.update(method='static',address='192.168.55.10/24',gateway='192.168.55.1')
            self.assertEqual(l.validate(p),p)
            entry['gateway']='192.168.66.1'
            with self.assertRaises(ValueError): l.validate(p)
            entry.update(address='10.42.0.10/24',gateway='10.42.0.1')
            with self.assertRaises(ValueError): l.validate(p)

    def test_mac_identity_survives_interface_renaming(self):
        entry=plan()['inputs']['usb-a']
        self.assertEqual(l.resolve(entry,[{'mac':entry['mac'],'ifname':'wlx000000000002'}]),'wlx000000000002')
        self.assertIsNone(l.resolve(entry,[{'mac':'02:00:00:00:00:99','ifname':'wlan1'}]))

    def test_wifi_credentials_are_not_saved_to_public_plan(self):
        p=plan()
        p['inputs']['usb-a']['psk']='example-only-password'
        clean=l.public(l.validate(p))
        self.assertNotIn('example-only-password',json.dumps(clean))
        self.assertIn('psk',p['inputs']['usb-a'])

    def test_disabled_failover_never_selects_healthy_backup(self):
        p=plan();p['failover']=False
        links={key:{'ifname':entry['ifname']} for key,entry in p['inputs'].items()}
        active,_,_=l.choose(p,{'active':'eth0','failures':{'eth0':99}},links,{'eth0':False,'usb-a':True})
        self.assertEqual(active,'eth0')
        links['eth0']=None
        self.assertIsNone(l.choose(p,{},links,{'usb-a':True})[0])

    def test_failover_waits_three_failed_probes_and_ignores_unselected_inputs(self):
        p=plan();links={key:{'ifname':entry['ifname']} for key,entry in p['inputs'].items()}
        previous={'active':'eth0'}
        for count in range(1,4):
            active,failures,successes=l.choose(p,previous,links,{'eth0':False,'usb-a':True,'usb-b':True})
            self.assertEqual(active,'eth0' if count<3 else 'usb-a')
            previous=dict(active=active,failures=failures,successes=successes)
        self.assertNotIn('usb-b',failures)

    def test_failover_returns_only_after_three_primary_successes(self):
        p=plan();links={key:{'ifname':entry['ifname']} for key,entry in p['inputs'].items()}
        previous={'active':'usb-a'}
        for count in range(1,4):
            active,failures,successes=l.choose(p,previous,links,{'eth0':True,'usb-a':True})
            self.assertEqual(active,'usb-a' if count<3 else 'eth0')
            previous=dict(active=active,failures=failures,successes=successes)

    def test_missing_active_device_fails_over_without_waiting_and_total_loss_closes_exit(self):
        p=plan();links={'eth0':None,'usb-a':{'ifname':'wlan1'},'usb-b':{'ifname':'eth1'}}
        self.assertEqual(l.choose(p,{'active':'eth0'},links,{'usb-a':True,'usb-b':True})[0],'usb-a')
        self.assertIsNone(l.choose(p,{'active':'eth0'},links,{'usb-a':False,'usb-b':True})[0])

    def test_probe_is_bound_to_interface_and_does_not_need_dns(self):
        with patch.object(l,'call',return_value=types.SimpleNamespace(returncode=0,stdout='ip=192.0.2.1\n')) as call:
            self.assertTrue(l.probe({'ifname':'wlan1'}))
        args=call.call_args.args
        self.assertEqual(args[:3],('curl','--interface','wlan1'))
        self.assertIn('https://1.1.1.1/cdn-cgi/trace',args)
        self.assertIn('--noproxy',args)

    def test_main_route_is_atomic_and_blackhole_blocks_other_defaults(self):
        with patch.object(l,'call') as call:
            l.set_default(dict(ifname='wlan1',gateway='192.168.55.1'))
            commands=[c.args for c in call.call_args_list]
            self.assertTrue(any('blackhole' in args and '41' in args for args in commands))
            self.assertTrue(any('wlan1' in args and '40' in args for args in commands))
            self.assertFalse(any(args[:3]==('ip','route','del') for args in commands))
            call.reset_mock()
            l.set_default(None)
            self.assertTrue(any(c.args[:3]==('ip','route','del') for c in call.call_args_list))

    def test_psk_persistence_uses_private_keyfile_not_command_arguments(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(l,'KEYS',pathlib.Path(tmp)):
            path=pathlib.Path(tmp)/'test.nmconnection'
            path.write_text('[connection]\nuuid=test-uuid\n[wifi-security]\nkey-mgmt=wpa-psk\n')
            with patch.object(l,'output',return_value='test-uuid'),patch.object(l,'call') as call:
                l.set_password('test-profile',' example pass\\word ')
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertIn('psk = \\sexample\\spass\\\\word\\s',path.read_text())
            self.assertNotIn('example pass',str(call.call_args_list))

    def test_standby_input_gets_separate_table_and_no_secret_in_nmcli_argv(self):
        entry=plan()['inputs']['usb-a'];entry['psk']='example-only-password'
        with patch.object(l,'profile_exists',return_value=True),patch.object(l,'call') as call,patch.object(l,'set_password') as secret:
            l.configure_input(entry,[])
        args=call.call_args.args
        self.assertIn('5001',args)
        self.assertNotIn('example-only-password',str(call.call_args_list))
        secret.assert_called_once_with(entry['profile'],'example-only-password')

    def test_bridge_guard_uses_output_bridge_not_radio_interface(self):
        loader=importlib.machinery.SourceFileLoader('bridge_routing',str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-vpn'))
        r=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(r)
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'B',pathlib.Path(tmp)),patch.object(r,'local_routes',return_value=[('10.42.0.0/24','edevbr0')]):
            (pathlib.Path(tmp)/'wifi.json').write_text('{"interface":"edevbr0","address":"10.42.0.1/24"}')
            rules=r.rules('hybrid')
        self.assertIn('iifname "edevbr0" ip saddr != 10.42.0.0/24 counter drop',rules)
        self.assertIn('"edevbr0", "vpnbr0"',rules)

    def test_bridge_conversion_lets_nm_remove_port_ip_settings(self):
        p=plan()
        with patch.object(l,'profile_exists',return_value=True),patch.object(l,'call',return_value=types.SimpleNamespace(stdout='example-only-password\n')) as call,patch.object(l,'save'),patch.object(l,'set_password') as secret:
            l.configure_out(p,[],dict(address='10.42.0.1/24',dhcp_range='',lease=0))
        port=next(c.args for c in call.call_args_list if c.args[:4]==('nmcli','connection','modify','E-DEV-AP') and 'connection.port-type' in c.args)
        self.assertIn('connection.port-type',port)
        self.assertNotIn('ipv4.method',port)
        secret.assert_called_once_with('E-DEV-AP','example-only-password')
        self.assertNotIn('example-only-password',str(call.call_args_list))

    def test_out_readiness_rejects_detached_ap_even_with_bridge_ip(self):
        links=[dict(ifname='edevbr0',ifindex=8),dict(ifname='wlan0',ifindex=3)]
        with patch.object(l,'read_addresses',return_value={'edevbr0':['10.42.0.1/24']}),patch.object(l,'output',return_value=json.dumps(links)):
            with self.assertRaises(ValueError): l.verify_output('edevbr0','10.42.0.1/24')
            links[1]['master']=8
            with patch.object(l,'output',return_value=json.dumps(links)):
                l.verify_output('edevbr0','10.42.0.1/24')

    def test_restore_requires_original_active_connection_not_only_successful_command(self):
        with patch.object(l,'output',return_value='other-uuid'):
            with self.assertRaises(ValueError):
                l.verify_active(['previous-uuid:eth0'],[dict(ifname='eth0')])

    @contextlib.contextmanager
    def transaction(self, rollback_ok=True):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);base=root/'config';base.mkdir();keys=root/'keys';keys.mkdir()
            original='[connection]\nuuid=previous-uuid\n';(keys/'wired.nmconnection').write_text(original)
            samba=root/'smb.conf';samba.write_text('interfaces = lo eth0 wlan0\n')
            (base/'dns.conf').write_text('listen-address=10.42.0.1\n')
            request=root/'request.json';request.write_text(json.dumps(plan()))
            events=[]
            wifi={'current':lambda:dict(address='10.42.0.1/24',dhcp_range='',lease=0),'dns_address':lambda a:None}
            def fail_out(*args):
                (keys/'wired.nmconnection').write_text('damaged')
                (base/'wifi.json').write_text('{"interface":"edevbr0","address":"10.42.0.1/24"}')
                raise ValueError('simulated activation failure')
            def output(*args):
                if '--active' in args: return 'previous-uuid:eth0\nprevious-ap:wlan0'
                if args[:3]==('nmcli','-t','-f'): return 'E-DEV-OUT'
                if args[:3]==('ip','-j','rule'): return '[]'
                return ''
            with patch.object(l,'B',base),patch.object(l,'KEYS',keys),patch.object(l,'SAMBA',samba), \
                 patch.object(l,'REQUEST',request),patch.object(l,'STATE',root/'state'),patch.object(l,'RESULT',root/'result'), \
                 patch.object(l,'BACKUPS',root/'backups'),patch.object(l,'inventory',return_value=[dict(ifname='eth0'),dict(ifname='wlan0')]), \
                 patch.object(l,'lock',side_effect=lambda n:contextlib.nullcontext()),patch.multiple(l,configure_input=lambda *args:None,input_snapshot=lambda plan:[{'profile':'generated-eth','properties':{'ipv4.route-table':'0'}}]), \
                 patch.object(l,'configure_out',side_effect=fail_out),patch.object(l,'output',side_effect=output), \
                 patch.object(l,'call',return_value=types.SimpleNamespace(returncode=0,stdout='')), \
                 patch.object(l.runpy,'run_path',return_value=wifi),patch.object(l,'runtime_refresh'), \
                 patch.object(l,'verify_active',side_effect=None if rollback_ok else ValueError('old connection missing')), \
                 patch.object(l,'wait_output'),patch.object(l,'block_outputs',side_effect=lambda:events.append('block')), \
                 patch.object(l,'unblock_outputs',side_effect=lambda:events.append('unblock')):
                yield root,base,keys,request,events,original

    def test_failed_bridge_migration_restores_keyfiles_config_and_only_then_unblocks(self):
        with self.transaction() as (root,base,keys,request,events,original):
            with self.assertRaises(ValueError): l.apply()
            self.assertEqual((keys/'wired.nmconnection').read_text(),original)
            self.assertFalse((base/'wifi.json').exists())
            self.assertFalse((base/'lan.json').exists())
            self.assertFalse(request.exists())
            self.assertEqual(events,['block','unblock'])
            result=json.loads((root/'result').read_text())
            self.assertFalse(result['success']);self.assertTrue(result['recovered'])
            self.assertEqual(pathlib.Path(result['backup']).stat().st_mode&0o777,0o700)

    def test_failed_restore_does_not_unblock_outputs(self):
        with self.transaction(rollback_ok=False) as (root,base,keys,request,events,original):
            with self.assertRaises(ValueError): l.apply()
            self.assertEqual(events,['block'])
            self.assertFalse(json.loads((root/'result').read_text())['recovered'])
            self.assertFalse(request.exists())

    def test_generated_input_snapshot_includes_original_route_table_without_keyfile(self):
        entry=plan()['inputs']['eth0']
        values=['eth0','yes','0','-1','','auto','','','','no','-1','0','no','-1','0','no','']
        with patch.object(l,'profile_exists',return_value=True),patch.object(l,'call',return_value=types.SimpleNamespace(stdout='\n'.join(values)+'\n')):
            saved=l.input_snapshot({'inputs':{'eth0':entry}})
        self.assertEqual(saved[0]['properties']['ipv4.route-table'],'0')
        self.assertEqual(saved[0]['properties']['802-3-ethernet.mac-address'],'')

    def test_waits_for_shared_bridge_ip_after_radio_activation_returns(self):
        with patch.object(l,'verify_output',side_effect=[ValueError('still activating'),None]) as verify,patch.object(l.time,'sleep'):
            l.wait_output('edevbr0','10.42.0.1/24')
        self.assertEqual(verify.call_count,2)
