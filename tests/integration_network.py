#!/usr/bin/env python3
"""Opt-in kernel routing test in a disposable Linux network namespace.
Run on Pi: sudo python3 tests/integration_network.py
Does not change the host's interfaces, routes, profiles or firewall.
"""
import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess

loader=importlib.machinery.SourceFileLoader('lan_kernel',str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-lan'))
lan=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(lan)
namespace='edev-route-test-'+str(os.getpid())


def call(*args,check=True):
    return subprocess.run(args,text=True,capture_output=True,stdin=subprocess.DEVNULL,check=check)


def ip(*args,check=True):
    return call('ip','netns','exec',namespace,'ip',*args,check=check)


if __name__=='__main__':
    if os.geteuid()!=0:
        raise SystemExit('Run with sudo on Linux.')
    call('ip','netns','add',namespace)
    try:
        for number in (0,1):
            dev='wan'+str(number);network='192.168.'+str(70+number)
            ip('link','add',dev,'type','dummy');ip('link','set',dev,'up')
            ip('addr','add',network+'.2/24','dev',dev)
            ip('route','add',network+'.0/24','dev',dev,'table',str(5000+number))
            ip('route','add','default','via',network+'.1','dev',dev,'table',str(5000+number))
            ip('rule','add','priority',str(10000+number),'oif',dev,'lookup',str(5000+number))
            ip('rule','add','priority',str(10100+number),'from',network+'.2/32','lookup',str(5000+number))
        # A regular main-table standby default must never silently take over.
        ip('route','add','default','via','192.168.71.1','dev','wan1','metric','600')
        lan.call=lambda *args,check=True,**kw:ip(*args[1:],check=check)
        lan.set_default(dict(ifname='wan0',gateway='192.168.70.1'))
        assert 'dev wan0' in ip('route','get','1.1.1.1').stdout
        assert 'dev wan1' in ip('route','get','1.1.1.1','oif','wan1').stdout
        assert 'dev wan1' in ip('route','get','1.1.1.1','from','192.168.71.2').stdout
        lan.set_default(dict(ifname='wan1',gateway='192.168.71.1'))
        assert 'dev wan1' in ip('route','get','1.1.1.1').stdout
        lan.set_default(None)
        assert ip('route','get','1.1.1.1',check=False).returncode!=0
        print('Kernel routes: primary, bound standby, source replies, switch and blocked fallback OK.')
    finally:
        call('ip','netns','delete',namespace)
