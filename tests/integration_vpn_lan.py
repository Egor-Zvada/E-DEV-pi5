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
    made=[];process=None;vpn_process=None
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
            vpn_process=S.Popen(['ip','netns','exec',vpn_server,'python3','-m','http.server','18087','--bind','0.0.0.0','--directory',str(vpn_dir)],stdout=S.DEVNULL,stderr=S.DEVNULL)
            v.routes('hybrid');v.split('hybrid')
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
            ip(router,'link','del','awg0')
            assert get('192.168.100.2').stdout=='local-route-ok\n'
            assert get('1.1.1.1').returncode!=0,'Tunnel loss must not turn a VPN exception into a provider fallback.'
            print('Kernel packets: LAN survives VPN loss; no Internet leak; explicit rules > VPN domains > GeoIP verified.')
    finally:
        for child in (process,vpn_process):
            if child:
                child.terminate()
                try:child.wait(timeout=2)
                except S.TimeoutExpired:child.kill();child.wait()
        for ns in reversed(made):run('ip','netns','del',ns,check=False)
