import importlib.machinery
import importlib.util
import pathlib
import unittest
from unittest.mock import patch
from types import SimpleNamespace

loader = importlib.machinery.SourceFileLoader('dashboard', str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-dashboard'))
d = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(d)


class Window:
    def __init__(self, height, width):
        self.size = height, width
        self.lines = []
        self.cells = {}
    def getmaxyx(self): return self.size
    def erase(self):
        self.lines.clear()
        self.cells.clear()
    def refresh(self): pass
    def addstr(self, y, x, text, style):
        assert 0 <= y < self.size[0]
        assert 0 <= x < self.size[1]
        assert x+len(text) < self.size[1]
        self.lines.append(text)
        for column, char in enumerate(text, x):
            self.cells[y, column] = char

    def row(self, y):
        return ''.join(self.cells.get((y, x), ' ') for x in range(self.size[1]))


def sample_data():
    return dict(cpu=25, cpu_parts=[20, 0, 5, 0],
                cores=[dict(name='cpu'+str(i), percent=25+i, parts=[20+i, 0, 5, 0]) for i in range(4)],
                mem_used=2**30, mem_total=4*2**30, mem_cache=2**30,
                swap_used=0, swap_total=2**30, disk_used=2**30,
                disk_total=256*2**30, temperature=43, uptime=500, cpu_history=[10, 25],
                ram_history=[25], net={n:(0, 0) for n in d.INTERFACES},
                net_history={n:[0] for n in d.INTERFACES},
                rx_history={n:[0] for n in d.INTERFACES}, tx_history={n:[0] for n in d.INTERFACES},
                mode='hybrid', selected='main', enabled=False, tunnel=False,
                policy={'mode':'default', 'primary':'main', 'backup':'backup'},
                system={'ssid':'E-DEV', 'hostname':'E-DEV'}, ap=True, clients=2,
                running=1, containers=3, services={},
                ips={'eth0':'192.168.55.8','wlan0':'172.16.7.1'})


class DashboardTests(unittest.TestCase):
    def test_cpu_counts_iowait_as_idle_and_ignores_counter_reset(self):
        self.assertEqual(d.cpu_percent([0]*8, [20, 0, 10, 60, 10, 0, 0, 0]), 30)
        self.assertEqual(d.cpu_percent([100]*8, [0]*8), 0)

    def test_network_reset_and_first_sample_do_not_create_fake_spike(self):
        self.assertEqual(d.rate(10000, 10, 2), 0)
        self.assertEqual(d.rate(10000, 10000, 2), 0)
        self.assertEqual(d.rate(100, 300, 2), 100)

    def test_dashboard_handles_resize_and_separates_ap_mode_from_tunnel(self):
        data = sample_data()
        for height, width in [(24, 80), (24, 110), (24, 127), (24, 128), (40, 140),
                              (24, 60), (18, 60), (18, 140), (10, 30), (1, 1)]:
            for page in ['main', 'links']:
                window = Window(height, width)
                d.render(window, data, page)
                if page == 'main' and width >= 60 and height >= 18:
                    self.assertIn('Трафик AP  HYBRID', window.lines)
                    self.assertIn('Wi-Fi AP   ВКЛ', window.lines)
                    self.assertIn('Туннель    ВЫКЛ', window.lines)
                if page == 'main' and height >= 24 and width >= 80:
                    for index, name in enumerate(d.INTERFACES):
                        self.assertIn(name, '\n'.join(window.row(row) for row in range(12, height-3)))
                    for index in range(4):
                        self.assertIn('CPU '+str(index), window.row(3+index))
                    self.assertIn('CPU all', window.row(7))
                    self.assertIn('25.0%', window.row(7))
                    self.assertIn('RAM', window.row(8))
                    self.assertIn('1.0/4.0G', window.row(8))
                    self.assertIn('Swp', window.row(9))
                    self.assertIn('CPU', window.row(10))
                    self.assertIn('RAM', window.row(11))
                    self.assertIn('43°C', window.row(height-3))
                    self.assertIn('0.00 Mb/s', window.row(13))
                    self.assertIn('шкала', window.row(12))
                    if width >= 128:
                        self.assertEqual(window.row(3)[2:42], d.LOGO_SMALL[0])
                    else:
                        for index, line in enumerate(d.LOGO_COMPACT):
                            self.assertEqual(window.row(3+index)[2:14], line.ljust(12))
                    d.render(window, data, animation_time=4.5)
                    ribbon = window.row(height-2)
                    d.render(window, data, animation_time=4.5, animated=False)
                    self.assertEqual(window.row(height-2), ribbon)
                if page == 'links' and height >= 18 and width >= 60:
                    rendered = '\n'.join(window.lines)
                    self.assertIn('https://192.168.55.8:9443/', rendered)
                    self.assertIn('smb://172.16.7.1/Share', rendered)
                    self.assertNotIn('.local', rendered)
                    self.assertNotIn('10.42.0.1', rendered)

    def test_marquee_wraps_and_stays_in_its_viewport(self):
        self.assertEqual(d.marquee('abc', 5, 0), 'abc  ')
        self.assertEqual(d.marquee('abc', 5, .3), 'bc   ')
        self.assertEqual(d.marquee('abc', 5, 3.3), 'abc  ')
        self.assertEqual(d.marquee('abc', 0, 1), '')
        self.assertEqual(d.marquee('abc', 100, 0)[:11], 'abc        ')
        self.assertEqual(len(d.marquee('abc', 100, 100)), 100)


    def test_cpu_segments_leave_iowait_empty(self):
        self.assertEqual(d.cpu_parts(None, [0]*8), [0]*4)
        self.assertEqual(d.cpu_parts([0]*8, [20, 5, 10, 50, 10, 2, 2, 1]), [20, 5, 10, 5])
        self.assertEqual(d.cpu_parts([100]*8, [0]*8), [0]*4)

    def test_memory_cache_does_not_double_count_used_memory(self):
        memory = dict(MemTotal=1000, MemAvailable=400, Cached=500, SReclaimable=100, Buffers=50, Shmem=20)
        self.assertEqual(d.memory_values(memory), (600, 1000, 400))
        memory['Cached'] = 100
        self.assertEqual(d.memory_values(memory), (600, 1000, 230))

    def test_network_scale_covers_peaks_without_claiming_link_capacity(self):
        self.assertEqual(d.network_ceiling([]), 125000)
        self.assertEqual(d.network_ceiling([125001]), 250000)
        self.assertEqual(d.network_ceiling([900000]), 1250000)
        self.assertEqual(d.mbps(125000), '1.00 Mb/s')

    def test_zones_follow_permanent_mac_and_exclude_bridge_counters(self):
        lan = dict(inputs={'usb':dict(ifname='eth1', mac='a')},
                   outputs={'wlan0':dict(ifname='wlan0', mac='b')}, backups=['usb'])
        devices = [dict(name='enx123', mac='a', up=True),
                   dict(name='edevbr0', mac='b', physical=False, up=True),
                   dict(name='wlan0', mac='b', physical=True, up=True)]
        zones = d.resolve_zones(lan, dict(active='usb'), devices)
        self.assertEqual(zones['IN'][0]['name'], 'enx123')
        self.assertTrue(zones['IN'][0]['active'])
        self.assertEqual(zones['OUT'][0]['name'], 'wlan0')
        self.assertFalse(zones['VPN'][0]['present'])
        zones = d.resolve_zones(lan, {}, [])
        self.assertFalse(zones['OUT'][0]['present'])

    def test_more_interfaces_are_reachable_without_overwriting_footer(self):
        data = sample_data()
        data['zones'] = {'IN':[dict(name='eth'+str(i), present=True, up=True, active=i==0) for i in range(8)],
                         'OUT':[dict(name='wlan0', present=True, up=True)],
                         'VPN':[dict(name='awg0', present=True, up=True)]}
        window = Window(24, 80)
        scroll, maximum = d.render(window, data)
        self.assertGreater(maximum, 0)
        self.assertEqual(scroll, 0)
        scroll, _ = d.render(window, data, network_scroll=999)
        self.assertEqual(scroll, maximum)
        self.assertIn('awg0', window.row(20))
        self.assertIn('q выход', window.row(23))
        d.render(window, data, network_scroll=-99)
        self.assertIn('IN', window.row(12))

    def test_sampler_uses_physical_out_ports_and_first_sample_is_zero(self):
        sampler = d.Sampler()
        sampler.details = dict(devices=[dict(name=n, mac=n, physical=n!='awg0', up=True) for n in d.INTERFACES])
        values = {'/proc/stat':'cpu 100 0 10 900 0 0 0 0\ncpu0 100 0 10 900 0 0 0 0',
                  '/proc/meminfo':'MemTotal: 4096 kB\nMemAvailable: 2048 kB\nSwapTotal: 1024 kB\nSwapFree: 512 kB',
                  '/proc/uptime':'100 0', '/run/e-dev-lan-state.json':'{"active":"eth0"}'}
        for n in (*d.INTERFACES, 'edevbr0'):
            for field in ('rx_bytes', 'tx_bytes'):
                values['/sys/class/net/'+n+'/statistics/'+field] = '1000'
        lan = dict(inputs={'eth0':dict(ifname='eth0', mac='eth0')}, outputs={'wlan0':dict(ifname='wlan0', mac='wlan0')})
        stat = SimpleNamespace(f_blocks=100, f_frsize=1024, f_bfree=50)
        with patch.object(d, 'text', side_effect=lambda path, fallback='':values.get(str(path), fallback)), \
             patch.object(d, 'config', side_effect=lambda name, fallback:lan if name=='lan.json' else fallback), \
             patch.object(d.time, 'monotonic', side_effect=[100, 102]), \
             patch.object(d.os, 'statvfs', return_value=stat), \
             patch.object(d.P.Path, 'exists', return_value=True), \
             patch.object(sampler, 'refresh_details'):
            first = sampler.sample()
            self.assertEqual(first['net']['eth0'], [0, 0])
            self.assertEqual(first['cores'][0]['percent'], 0)
            values['/sys/class/net/wlan0/statistics/rx_bytes'] = '1200'
            values['/sys/class/net/wlan0/statistics/tx_bytes'] = '1400'
            values['/sys/class/net/edevbr0/statistics/rx_bytes'] = '9999999'
            second = sampler.sample()
            self.assertEqual(second['zone_net']['OUT'], (100, 200))
            self.assertNotIn('edevbr0', second['net'])
            self.assertEqual(second['swap_used'], 512*1024)


    def test_tall_plot_has_a_shared_scale_and_zero_baseline(self):
        self.assertEqual(d.plot_row([0, .5, 1], 3, 1, 0, 2), '  █')
        self.assertEqual(d.plot_row([0, .5, 1], 3, 1, 1, 2), '·██')
        self.assertEqual(d.plot_row([], 3, 1, 0, 2), '')
