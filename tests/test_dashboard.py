import importlib.machinery
import importlib.util
import pathlib
import unittest

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


class DashboardTests(unittest.TestCase):
    def test_cpu_counts_iowait_as_idle_and_ignores_counter_reset(self):
        self.assertEqual(d.cpu_percent([0]*8, [20, 0, 10, 60, 10, 0, 0, 0]), 30)
        self.assertEqual(d.cpu_percent([100]*8, [0]*8), 0)

    def test_network_reset_and_first_sample_do_not_create_fake_spike(self):
        self.assertEqual(d.rate(10000, 10, 2), 0)
        self.assertEqual(d.rate(10000, 10000, 2), 0)
        self.assertEqual(d.rate(100, 300, 2), 100)

    def test_dashboard_handles_resize_and_separates_ap_mode_from_tunnel(self):
        data = dict(cpu=25, mem_used=2**30, mem_total=4*2**30, disk_used=2**30,
                    disk_total=256*2**30, temperature=43, uptime=500, cpu_history=[10, 25],
                    ram_history=[25], net={n:(0, 0) for n in d.INTERFACES},
                    net_history={n:[0] for n in d.INTERFACES}, mode='hybrid', selected='main',
                    enabled=False, tunnel=False, policy={'mode':'default', 'primary':'main', 'backup':'backup'},
                    system={'ssid':'E-DEV', 'hostname':'E-DEV'}, ap=True, clients=2,
                    running=1, containers=3, services={}, ips={'eth0':'192.168.55.8','wlan0':'172.16.7.1'})
        for height, width in [(24, 80), (24, 110), (24, 127), (24, 128), (40, 140),
                              (18, 60), (18, 140), (10, 30), (1, 1)]:
            for page in ['main', 'links']:
                window = Window(height, width)
                d.render(window, data, page)
                if page == 'main' and width >= 60 and height >= 18:
                    self.assertIn('Трафик AP  HYBRID', window.lines)
                    self.assertIn('Wi-Fi AP   ВКЛ', window.lines)
                    self.assertIn('Туннель    ВЫКЛ', window.lines)
                if page == 'main' and height >= 24 and width >= 80:
                    for index, name in enumerate(d.INTERFACES):
                        self.assertIn(name, window.row(14+index*2))
                    self.assertIn('RAM', window.row(7))
                    self.assertIn('4.0', window.row(7))
                    self.assertIn('↑ 0.0 B/s', window.row(14))
                    if width >= 128:
                        self.assertEqual(window.row(3)[2:42], d.LOGO_SMALL[0])
                    else:
                        for index, line in enumerate(d.LOGO_COMPACT):
                            self.assertEqual(window.row(4+index)[2:14], line.ljust(12))
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
