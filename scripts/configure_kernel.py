#!/usr/bin/env python3
"""Enable the Raspberry Pi memory controller, preserving the boot command line."""
import os
import pathlib as P
import time


def memory_command_line(text):
    words=text.split()
    if not words or len(text.strip().splitlines())!=1:
        raise ValueError('cmdline.txt должен содержать одну непустую строку.')
    enabled=disabled=-1
    for index,word in enumerate(words):
        key,_,value=word.partition('=')
        if 'memory' not in value.split(','):continue
        if key=='cgroup_enable':enabled=index
        if key=='cgroup_disable':disabled=index
    if enabled>disabled:return text
    # Pi firmware prepends cgroup_disable=memory. The last setting wins.
    return text.rstrip()+' cgroup_enable=memory\n'


def configure(path=P.Path('/boot/firmware/cmdline.txt'), backups=P.Path('/opt/e-dev/backups')):
    previous=path.read_text();updated=memory_command_line(previous)
    if previous==updated:return False
    folder=backups/('kernel-memory-'+str(time.time_ns()));folder.mkdir(parents=True,mode=0o700)
    backup=folder/'cmdline.txt';backup.write_text(previous);backup.chmod(0o600)
    temporary=path.with_name(path.name+'.e-dev-new');temporary.write_text(updated);temporary.replace(path)
    return True


def main():
    if os.geteuid()!=0:raise ValueError('Требуется sudo.')
    model=P.Path('/proc/device-tree/model')
    if not model.exists() or 'Raspberry Pi 5' not in model.read_text():
        raise ValueError('Эта настройка рассчитана на Raspberry Pi 5.')
    changed=configure()
    active='memory' in P.Path('/sys/fs/cgroup/cgroup.controllers').read_text().split()
    print('Memory cgroup: '+('активен.' if active else 'подготовлен. Требуется sudo reboot для применения лимитов памяти.'))
    return changed


if __name__=='__main__':main()
