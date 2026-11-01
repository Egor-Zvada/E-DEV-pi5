#!/usr/bin/env python3
"""Real packets in disposable namespaces: LAN survives a failed VPN, Internet stays closed.
Run as root on Linux. No host route, physical interface, or firewall is changed.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib as P
import subprocess as S
import tempfile
import time

loader=importlib.machinery.SourceFileLoader('vpn_lan_kernel',str(P.Path(__file__).resolve().parents[1]/'src/e-dev-vpn'))
v=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(v)


def run(*args,check=True,input=None):
    return S.run(args,text=True,capture_output=True,stdin=S.DEVNULL if input is None else None,input=input,check=check,timeout=8)


if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('Run with sudo on Linux.')
    router,client,server=['edev-'+name+'-'+str(os.getpid()) for name in ('rt','cl','sv')]
    made=[];process=None;vpn_process=None;persistent_client=None
    def inside(ns,*args,**kw):return run('ip','netns','exec',ns,*args,**kw)
    def ip(ns,*args,**kw):return inside(ns,'ip',*args,**kw)
    try:
        for ns in (router,client,server):run('ip','netns','add',ns);made.append(ns);ip(ns,'link','set','lo','up')
        ip(router,'link','add','wlan0','type','veth','peer','name','eth0','netns',client)
        ip(router,'link','add','eth0','type','veth','peer','name','eth0','netns',server)
        for ns,dev,address in ((router,'wlan0','10.42.0.1/24'),(client,'eth0','10.42.0.2/24'),(router,'eth0','192.168.100.1/24'),(server,'eth0','192.168.100.2/24')):
            ip(ns,'addr','add',address,'dev',dev);ip(ns,'link','set',dev,'up')
        ip(router,'link','add','awg0','type','dummy');ip(router,'link','set','awg0','up')
        ip(server,'addr','add','1.1.1.1/32','dev','lo')  # Only inside the isolated test server.
        for ns,gateway in ((client,'10.42.0.1'),(server,'192.168.100.1'),(router,'192.168.100.2')):
            ip(ns,'route','add','default','via',gateway)
        inside(router,'sysctl','-w','net.ipv4.ip_forward=1')
        v.call=lambda *args,**kw:inside(router,*args,**kw)
        v.local_routes=lambda:[('10.42.0.0/24','wlan0'),('192.168.100.0/24','eth0')]
        with tempfile.TemporaryDirectory() as folder:
            v.B=P.Path(folder);(v.B/'wifi.json').write_text(json.dumps(dict(address='10.42.0.1/24')))
            (v.B/'index.html').write_text('local-route-ok\n')
            v.routes('hybrid')
            inside(router,'nft','-f','-',input=v.rules('blocked'))
            process=S.Popen(['ip','netns','exec',server,'python3','-m','http.server','18087','--bind','0.0.0.0','--directory',folder],stdout=S.DEVNULL,stderr=S.DEVNULL)
            def get(address):return inside(client,'curl','--noproxy','*','-sS','--connect-timeout','1','--max-time','2','http://'+address+':18087/',check=False)
            for _ in range(10):
                response=get('192.168.100.2')
                if response.returncode==0:break
                time.sleep(.1)
            assert response.returncode==0 and response.stdout=='local-route-ok\n',response
            assert get('1.1.1.1').returncode!=0,'Missing VPN default must block Internet.'
            # Remove policy protection deliberately in this namespace to verify
            # the independent firewall still prevents a provider fallback.
            ip(router,'route','del','blackhole','default','table','1042')
            for mode in ('blocked','vpn','hybrid'):
                inside(router,'nft','-f','-',input='delete table inet e_dev_guard\n'+v.rules(mode))
                assert get('192.168.100.2').stdout=='local-route-ok\n',mode+' must keep LAN open.'
                assert get('1.1.1.1').returncode!=0,mode+' must block Internet without VPN.'
            vpn_server='edev-vs-'+str(os.getpid())
            run('ip','netns','add',vpn_server);made.append(vpn_server);ip(vpn_server,'link','set','lo','up')
            ip(router,'link','del','awg0')
            ip(router,'link','add','awg0','type','veth','peer','name','eth0','netns',vpn_server)
            for ns,dev,addr in ((router,'awg0','192.0.2.1/24'),(vpn_server,'eth0','192.0.2.2/24')):
                ip(ns,'addr','add',addr,'dev',dev);ip(ns,'link','set',dev,'up')
            ip(vpn_server,'addr','add','1.1.1.1/32','dev','lo');ip(vpn_server,'route','add','default','via','192.0.2.1')
            vpn_dir=v.B/'vpn-server';vpn_dir.mkdir();(vpn_dir/'index.html').write_text('vpn-route-ok\n')
            server_code="""from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def do_GET(self):
  body=b'vpn-route-ok\\n';self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
ThreadingHTTPServer(('0.0.0.0',18087),Handler).serve_forever()
"""
            vpn_process=S.Popen(['ip','netns','exec',vpn_server,'python3','-c',server_code],stdin=S.DEVNULL,stdout=S.DEVNULL,stderr=S.DEVNULL)
            # Upgrade an existing timeout-based set while retaining its members.
            inside(router,'nft','-f','-',input='add table inet e_dev_split\nadd set inet e_dev_split ru4 { type ipv4_addr; flags timeout; timeout 1h; }\nadd element inet e_dev_split ru4 { 203.0.113.17 }\nadd chain inet e_dev_split classify { type filter hook prerouting priority mangle; policy accept; }\nadd rule inet e_dev_split classify ip daddr @ru4 return\n')
            v.routes('hybrid');v.split('hybrid')
            assert 'timeout' not in v.dns_set('ru4').get('flags',[])
            assert '203.0.113.17' in v.dns_set_values(v.dns_set('ru4'))
            ip(router,'route','replace','default','via','192.0.2.2','dev','awg0','metric','10','table','1042')
            inside(router,'nft','-f','-',input='delete table inet e_dev_guard\n'+v.rules('hybrid'))
            inside(router,'nft','add','element','inet','e_dev_split','rusubnets','{','1.1.1.0/24','}')
            assert get('1.1.1.1').stdout=='local-route-ok\n','RU GeoIP must use the provider.'
            inside(router,'nft','add','element','inet','e_dev_split','vpn4','{','1.1.1.1','}')
            for _ in range(10):
                response=get('1.1.1.1')
                if response.stdout=='vpn-route-ok\n':break
                time.sleep(.1)
            assert response.stdout=='vpn-route-ok\n','VPN domains must override RU GeoIP.'
            for group in ('custom4','ru4'):
                inside(router,'nft','add','element','inet','e_dev_split',group,'{','1.1.1.1','}')
                assert get('1.1.1.1').stdout=='local-route-ok\n','Explicit direct rule must override VPN domains.'
                inside(router,'nft','flush','set','inet','e_dev_split',group)
            inside(router,'nft','add','element','inet','e_dev_split','vpn4','{','192.168.100.2','}')
            assert get('192.168.100.2').stdout=='local-route-ok\n','Local LAN must always stay local.'
            client_code="""import http.client,sys
connection=http.client.HTTPConnection('1.1.1.1',18087,timeout=3)
for line in sys.stdin:
 connection.request('GET','/');reply=connection.getresponse();print(reply.read().decode().strip(),flush=True)
"""
            persistent_client=S.Popen(['ip','netns','exec',client,'python3','-u','-c',client_code],stdin=S.PIPE,stdout=S.PIPE,stderr=S.PIPE,text=True)
            def established_request():
                persistent_client.stdin.write('request\n');persistent_client.stdin.flush()
                return persistent_client.stdout.readline().strip()
            assert established_request()=='vpn-route-ok'
            v.dns_rotation_state(0,'test-boot')
            original_call=v.call
            def concurrent_dns_reply(*args,**kwargs):
                if args[:2]==('nft','-f') and 'flush set inet e_dev_split vpn4_prev' in kwargs.get('input',''):
                    inside(router,'nft','add','element','inet','e_dev_split','vpn4','{','203.0.113.55','}')
                return original_call(*args,**kwargs)
            v.call=concurrent_dns_reply
            try:assert v.rotate_dns_sets(v.DNS_ROTATION_SECONDS,'test-boot')
            finally:v.call=original_call
            assert '203.0.113.55' in v.dns_set_values(v.dns_set('vpn4')),'Concurrent DNS reply was lost during rotation.'
            assert get('1.1.1.1').stdout=='vpn-route-ok\n','Previous generation must cover cached DNS clients.'
            assert v.rotate_dns_sets(v.DNS_ROTATION_SECONDS*2,'test-boot')
            assert '1.1.1.1' not in v.dns_set_values(v.dns_set('vpn4_prev')),'Expired generation was not collected.'
            assert established_request()=='vpn-route-ok','Aging DNS must not reroute an established VPN session.'
            assert get('1.1.1.1').stdout=='local-route-ok\n','A new flow must use current GeoIP after an old domain mapping expires.'
            inside(router,'nft','add','element','inet','e_dev_split','vpn4','{','1.1.1.1','}')
            ip(router,'link','del','awg0')
            assert get('192.168.100.2').stdout=='local-route-ok\n'
            assert get('1.1.1.1').returncode!=0,'Tunnel loss must not turn a VPN exception into a provider fallback.'
            print('Kernel packets: LAN survives VPN loss; no Internet leak; routing priorities, timeout-set migration, DNS generations and established-session stability verified.')
    finally:
        for child in (persistent_client,process,vpn_process):
            if child:
                child.terminate()
                try:child.wait(timeout=2)
                except S.TimeoutExpired:child.kill();child.wait()
        for ns in reversed(made):run('ip','netns','del',ns,check=False)
