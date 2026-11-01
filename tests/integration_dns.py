#!/usr/bin/env python3
"""Check stale-cache and retry behaviour with an isolated DNS server on localhost high ports.
Uses no system resolver configuration and never contacts the public Internet.
"""
import os
import socket
import struct
import subprocess as S
import threading
import time


def free_port():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


if __name__=='__main__':
    upstream=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);upstream.bind(('127.0.0.1',0));upstream.settimeout(.1)
    port=free_port();stop=threading.Event();state=dict(drop=False,skip=1,queries=0)
    address=socket.inet_aton('203.0.113.9')
    def answer():
        while not stop.is_set():
            try:data,peer=upstream.recvfrom(4096)
            except socket.timeout:continue
            state['queries']+=1
            if state['drop']:continue
            if state['skip']:
                state['skip']-=1;continue
            offset=12
            while data[offset]:offset+=data[offset]+1
            end=offset+5
            response=data[:2]+struct.pack('!5H',0x8180,1,1,0,0)+data[12:end]
            response+=b'\xc0\x0c'+struct.pack('!HHIH',1,1,1,4)+address
            upstream.sendto(response,peer)
    thread=threading.Thread(target=answer);thread.start()
    command=['dnsmasq','--no-daemon','--conf-file=/dev/null','--no-resolv','--no-hosts','--bind-interfaces','--listen-address=127.0.0.1','--port='+str(port),'--server=127.0.0.1#'+str(upstream.getsockname()[1]),'--cache-size=64','--use-stale-cache=5','--fast-dns-retry=500,3000','--edns-packet-max=1232']
    if os.geteuid()==0:command+=['--user=root']
    process=S.Popen(command,stdout=S.DEVNULL,stderr=S.DEVNULL)
    def query(timeout=.75):
        packet=struct.pack('!6H',1234,0x0100,1,0,0,0)+b'\x05probe\x07example\x04test\x00'+struct.pack('!HH',1,1)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
            client.settimeout(timeout);started=time.monotonic();client.sendto(packet,('127.0.0.1',port))
            return client.recvfrom(4096)[0],time.monotonic()-started
    try:
        # Wait for the listener without populating the cache or retrying DNS
        # from the client: the first lost packet must be retried by dnsmasq.
        for _ in range(40):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):pass
                break
            except OSError:time.sleep(.05)
        else:raise AssertionError('DNS test server did not start.')
        # The retry window uses whole seconds in dnsmasq 2.91. A one-second
        # window may expire before even the first retry at a second boundary.
        reply,elapsed=query(3)
        assert reply.endswith(address) and state['queries']>=2,'Lost upstream packet was not retried.'
        assert .25<elapsed<3,('Unexpected upstream retry delay.',elapsed)
        # dnsmasq timestamps TTLs in whole seconds and considers equality valid.
        state['drop']=True;time.sleep(2.2)
        baseline=state['queries'];reply,elapsed=query()
        assert reply.endswith(address) and elapsed<.75,(reply,elapsed)
        deadline=time.monotonic()+2.5
        while state['queries']<baseline+1 and time.monotonic()<deadline:time.sleep(.05)
        assert state['queries']>=baseline+1,('No background cache refresh observed.',baseline,state['queries'])
        time.sleep(5)
        try:reply,_=query();assert not reply.endswith(address),'Cache served past the configured excess lifetime.'
        except socket.timeout:pass
        print('DNS packets: expired cache survives a short upstream outage, replies promptly, retries, and expires at its bound.')
    finally:
        process.terminate()
        try:process.wait(timeout=2)
        except S.TimeoutExpired:process.kill();process.wait()
        stop.set();thread.join(timeout=1);upstream.close()
