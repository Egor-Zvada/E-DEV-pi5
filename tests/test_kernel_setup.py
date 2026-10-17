import importlib.util
import pathlib as P
import tempfile
import unittest

path=P.Path(__file__).resolve().parents[1]/'scripts/configure_kernel.py'
spec=importlib.util.spec_from_file_location('kernel_setup',path)
k=importlib.util.module_from_spec(spec);spec.loader.exec_module(k)


class KernelSetupTests(unittest.TestCase):
    def test_preserves_root_boot_and_imager_options_and_is_idempotent(self):
        before='console=tty1 root=PARTUUID=123 rootwait ds=nocloud;i=abc\n'
        after=k.memory_command_line(before)
        self.assertEqual(after,before.rstrip()+' cgroup_enable=memory\n')
        self.assertEqual(k.memory_command_line(after),after)

    def test_last_memory_setting_wins_without_removing_other_controllers(self):
        before='root=x cgroup_enable=cpu,memory cgroup_disable=memory\n'
        after=k.memory_command_line(before)
        self.assertTrue(after.endswith('cgroup_enable=memory\n'))
        self.assertIn('cgroup_enable=cpu,memory',after)
        enabled='root=x cgroup_disable=memory cgroup_enable=memory,cpu\n'
        self.assertEqual(k.memory_command_line(enabled),enabled)

    def test_rejects_empty_or_multiline_command_line(self):
        for text in ('','\n','root=x\nconsole=tty1\n'):
            with self.assertRaises(ValueError):k.memory_command_line(text)

    def test_original_is_backed_up_before_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=P.Path(tmp);boot=root/'cmdline.txt';boot.write_text('root=x rootwait\n')
            self.assertTrue(k.configure(boot,root/'backups'))
            backup=next((root/'backups').glob('*/cmdline.txt'))
            self.assertEqual(backup.read_text(),'root=x rootwait\n')
            self.assertEqual(backup.stat().st_mode&0o777,0o600)
            self.assertFalse(k.configure(boot,root/'backups'))
