import importlib.machinery
import importlib.util
import pathlib
import subprocess
import unittest
from unittest.mock import Mock, patch

loader=importlib.machinery.SourceFileLoader('incus_ready',str(pathlib.Path(__file__).resolve().parents[1]/'src/e-dev-incus-ready'))
r=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(r)


class ReadyTests(unittest.TestCase):
    def test_dead_daemon_does_not_start_another_waiter(self):
        with patch.object(r,'identity',return_value=None),patch.object(r.S,'Popen') as start:
            self.assertEqual(r.wait_ready(123),1)
            start.assert_not_called()

    def test_daemon_death_or_pid_reuse_ends_waiter(self):
        for identity in (None,'new-start-time'):
            child=Mock();child.poll.return_value=None
            with patch.object(r,'identity',side_effect=['original',identity]),patch.object(r.S,'Popen',return_value=child):
                self.assertEqual(r.wait_ready(123),1)
            child.terminate.assert_called_once();child.wait.assert_called_once_with(timeout=2)

    def test_success_and_timeout_keep_incus_result(self):
        for code in (0,1):
            child=Mock(returncode=code);child.poll.return_value=code
            with patch.object(r,'identity',return_value='original'),patch.object(r.S,'Popen',return_value=child):
                self.assertEqual(r.wait_ready(123),code)
            child.terminate.assert_not_called()
