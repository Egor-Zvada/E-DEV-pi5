import contextlib
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import os
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]


def load(name):
    loader=importlib.machinery.SourceFileLoader(name.replace('-','_')+'_test',str(ROOT/'src'/name))
    module=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
    loader.exec_module(module)
    return module


ui=load('e-dev-settings')
cli=load('e-dev')
actions=load('e-dev-actions')
tui=load('e-dev-tui')


class Menu:
    def __init__(self, choices=()):
        self.choices=iter(choices)
        self.screens=[]
    def menu(self,title,prompt,choices):
        self.screens.append((title,prompt,choices))
        return next(self.choices,None)
    def message(self,*args): pass
    def yes(self,*args): return True
    def logo(self): return 'LOGO'


class SettingsTests(unittest.TestCase):
    def test_old_public_mutations_fail_before_sudo_or_any_operation(self):
        for command in [('wifi','vpn','off'),('wifi','settings'),('lan','setup'),('vpn','on'),
                        ('vpn','settings'),('vpn','manual','main'),('vpn','remove','main'),
                        ('passwd','wifi'),('help','vpn')]:
            with self.subTest(command=command),patch.object(cli.sys,'argv',['e-dev',*command]), \
                 patch.object(cli,'root') as root,patch.object(cli,'run') as run,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit: cli.main()
                self.assertEqual(exit.exception.code,1)
                root.assert_not_called();run.assert_not_called()

    def test_new_settings_and_manuals_dispatch_to_one_ui(self):
        for args,expected in [(['settings'],['/usr/local/sbin/e-dev-settings']),
                              (['manuals'],['/usr/local/sbin/e-dev-settings','manuals'])]:
            with patch.object(cli.sys,'argv',['e-dev',*args]),patch.object(cli,'root') as root, \
                 patch.object(cli,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
                with self.assertRaises(SystemExit): cli.main()
                run.assert_called_once_with(expected)
                self.assertEqual(root.called,args==['settings'])

    def test_six_root_sections_and_out_port_count(self):
        plan={'outputs':{'wlan0':{},'usb':{}},'failover':True}
        with patch.object(ui.lan,'config',return_value=plan),patch.object(ui.lan,'read',return_value={}), \
             patch.object(ui.lan,'inventory',return_value=[]),patch.object(ui.lanui,'name',return_value='eth1'), \
             patch.object(ui,'cfg',side_effect=lambda name,default:default),patch.object(ui,'text',return_value='hybrid'):
            choices=ui.root_choices()
        self.assertEqual([key for key,label in choices],['lan','out','ap','vpn','status','access'])
        self.assertIn('1',choices[1][1]);self.assertIn('HYBRID',choices[2][1])

    def test_cancel_manual_and_backup_choice_do_not_change_anything(self):
        for mode in ('manual','default'):
            menu=Menu([mode])
            with patch.object(ui,'choose_profile',side_effect=[None] if mode=='manual' else ['primary',None]), \
                 patch.object(ui,'action') as action:
                ui.vpn_selection(menu)
            action.assert_not_called()

    def test_default_selection_passes_both_roles_and_does_not_enable_tunnel(self):
        with patch.object(ui,'choose_profile',side_effect=['primary','backup']),patch.object(ui,'action') as action:
            ui.vpn_selection(Menu(['default']))
        self.assertEqual(action.call_args.args[2:],('vpn','default','primary','backup'))

    def test_private_password_and_path_are_not_arguments(self):
        menu=Menu();menu.password=lambda *args:'test-passphrase'
        with patch.object(ui,'action') as action:
            ui.password(menu,'files')
        self.assertEqual(action.call_args.args[2:],('password','files'))
        self.assertEqual(action.call_args.kwargs['payload'],{'password':'test-passphrase'})
        self.assertNotIn('test-passphrase',str(action.call_args.args))

    def test_out_has_no_separate_dhcp_or_password_and_access_has_no_ap_password(self):
        menu=Menu()
        with patch.object(ui,'output',return_value=json.dumps(dict(ssh=True,ui=True,files=True,incus=True))):
            ui.access_menu(menu)
        tags=[tag for tag,label in menu.screens[0][2]]
        self.assertNotIn('wifi',tags)
        self.assertIn('password-all',tags)

    def test_import_profile_named_add_has_no_collision_with_import_button(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(ui,'B',pathlib.Path(temp)), \
             patch.object(ui,'cfg',return_value={}),patch.object(ui,'text',return_value='add'):
            profiles=pathlib.Path(temp)/'profiles';profiles.mkdir();(profiles/'add.conf').write_text('placeholder')
            menu=Menu(['profile:add',None,None])
            ui.vpn_profiles(menu)
        self.assertEqual([tag for tag,label in menu.screens[0][2]],['add','profile:add'])
        self.assertEqual(menu.screens[1][0],'VPN · add')

    def test_tab_completes_real_paths_with_spaces_and_common_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)
            (root/'vpn folder').mkdir();(root/'main one.conf').touch();(root/'main two.conf').touch()
            self.assertEqual(tui.complete_path(str(root/'vp')),str(root/'vpn folder')+'/')
            self.assertEqual(tui.complete_path(str(root/'main ')),str(root/'main '))
            self.assertEqual(tui.complete_path(str(root/'main o')),str(root/'main one.conf'))
            self.assertEqual(tui.complete_path(str(root/'missing')),str(root/'missing'))

    def test_incognito_access_keeps_local_api_for_lxconsole_and_never_stops_incus(self):
        with patch.object(actions,'call',return_value='') as call,contextlib.redirect_stdout(io.StringIO()):
            actions.access('incus',False)
        call.assert_called_once_with('incus','config','set','core.https_address=127.0.0.1:8443')

    def test_protected_lxconsole_certificate_cannot_be_revoked(self):
        for name in ('lxconsole','E-DEV LXConsole'):
            with self.subTest(name=name),patch.object(actions.os,'geteuid',return_value=0),patch.object(actions,'load'), \
                 patch.object(actions,'call',return_value=json.dumps([dict(name=name,fingerprint='abc')])) as call:
                with self.assertRaises(ValueError): actions.main(['cert-remove','abc'])
            self.assertEqual(call.call_count,1)

    def test_each_manual_chapter_exists_and_help_is_short(self):
        for name,label in ui.CHAPTERS:
            self.assertTrue((ROOT/'docs/wiki'/(name+'.txt')).exists())
        index=(ROOT/'docs/wiki/index.txt').read_text()
        self.assertIn('e-dev settings',index);self.assertIn('e-dev manuals',index)
        for old in ('wifi settings','lan setup','vpn on','help vpn','vpn add'):
            self.assertNotIn(old,index)

    def test_logo_keeps_one_shared_margin_and_all_root_sections_fit(self):
        for columns,rows,filename in [(80,24,'logo-settings-small.txt'),(80,32,'logo-settings.txt'),(120,40,'logo-small.txt')]:
            size=os.terminal_size((columns,rows))
            with patch.object(tui.shutil,'get_terminal_size',return_value=size), \
                 patch.object(tui.S,'run',return_value=types.SimpleNamespace(returncode=0,stdout='ap\n')) as run:
                dialog=tui.Dialog();prompt=dialog.logo()
                original=(ROOT/'assets'/filename).read_text().splitlines()
                # Preserve the original distances between frame, glyph and underline.
                margin=len(prompt.splitlines()[0])-len(original[0])
                self.assertEqual([line[margin:] for line in prompt.splitlines()],original)
                dialog.menu('E-DEV 1.0 · Settings',prompt,[(str(i),str(i)) for i in range(6)])
                args=run.call_args.args[0];menu=args.index('--menu')
                self.assertGreaterEqual(int(args[menu+4]),6)
                self.assertLessEqual(int(args[menu+2]),rows-2)
                self.assertIn('--ascii-lines',args);self.assertIn('--no-nl-expand',args)

    def test_returning_to_a_menu_preserves_the_selected_section(self):
        with patch.object(tui.shutil,'get_terminal_size',return_value=os.terminal_size((80,24))), \
             patch.object(tui.S,'run',return_value=types.SimpleNamespace(returncode=0,stdout='vpn\n')) as run:
            dialog=tui.Dialog();items=[('ap','AP'),('vpn','VPN')]
            dialog.menu('Settings','Choose',items);dialog.menu('Settings','Choose',items)
            args=run.call_args.args[0]
            self.assertEqual(args[args.index('--default-item')+1],'vpn')

    def test_certificate_back_returns_to_incus_service_before_access_root(self):
        menu=Menu(['certs',None])
        with patch.object(ui,'output',return_value=json.dumps(dict(incus=True))), \
             patch.object(ui,'certificates') as certificates:
            ui.access_service(menu,'incus')
        certificates.assert_called_once_with(menu)
        self.assertEqual([screen[0] for screen in menu.screens],['Incus UI · сертификат']*2)
