import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('bash'),'Bash is required')
class CompletionTests(unittest.TestCase):
    def complete(self,words,index,source=None,extra=''):
        # Use Bash itself: array quoting and sourcing cannot be checked in Python.
        command=' '.join("'"+word.replace("'","'\"'\"'")+"'" for word in words)
        script=f'''source "$1"
{extra}
COMP_WORDS=({command}); COMP_CWORD={index}
_e_dev_complete
printf '%s\\n' "${{COMPREPLY[@]}}"
'''
        result=subprocess.run(['bash','-c',script,'completion',str(source or ROOT/'assets/completion')],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout.splitlines()

    def test_settings_status_and_aliases(self):
        for alias in ('e-dev','E-DEV','E-dev','edev'):
            self.assertEqual(self.complete([alias,'set'],1),['settings'])
            self.assertEqual(self.complete([alias,'wifi','sta'],2),['status'])
        self.assertEqual(self.complete(['e-dev','status','--o'],2),['--once'])

    def test_only_current_public_commands_are_offered(self):
        words=self.complete(['e-dev',''],1)
        self.assertIn('settings',words); self.assertIn('manuals',words)
        for old in ('wvpn','setup','on','off','add','use'):
            self.assertNotIn(old,words)
        self.assertEqual(self.complete(['e-dev','vpn',''],2),['status'])

    def test_an_open_shell_reloads_the_updated_implementation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=pathlib.Path(tmp)/'completion';source.write_text((ROOT/'assets/completion').read_text())
            updated=source.read_text().replace('help settings manuals','help settings manuals future-command')
            # The loaded function is deliberately stale; only the file changes.
            extra='printf %s '+"'"+updated.replace("'","'\"'\"'")+"'"+' > "$1"'
            self.assertEqual(self.complete(['e-dev','future'],1,source,extra),['future-command'])

    def test_hidden_import_completes_a_file_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'server config.conf';path.touch()
            self.assertEqual(self.complete(['e-dev','vpn','add','test',str(path.parent/'server')],4),[str(path)])
