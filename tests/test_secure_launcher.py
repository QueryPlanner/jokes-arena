"""Hidden terminal input reaches only the generator process environment."""
import contextlib
import io
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import run_expanded_batch


class LauncherTests(unittest.TestCase):
    def test_noninteractive_input_never_prompts_or_launches(self):
        with patch('sys.stdin.isatty',return_value=False), patch.object(run_expanded_batch,'getpass') as prompt, patch.object(run_expanded_batch.subprocess,'run') as child, contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(run_expanded_batch.main(),1)
        prompt.getpass.assert_not_called()
        child.assert_not_called()
        self.assertIn('without echo',output.getvalue())

    def test_hidden_key_is_passed_in_environment_not_command_or_output(self):
        with patch('sys.stdin.isatty',return_value=True), patch('sys.argv',['launcher','--directory','/private/tmp/batch','--execute']), patch.object(run_expanded_batch.getpass,'getpass',return_value='test-secret'), patch.object(run_expanded_batch.subprocess,'run',return_value=SimpleNamespace(returncode=7)) as child, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_expanded_batch.main(),7)
        command=child.call_args.args[0]
        self.assertIn('arena.populate',command)
        self.assertIn('--execute',command)
        self.assertNotIn('test-secret',' '.join(command))
        self.assertEqual(child.call_args.kwargs['env']['OPENROUTER_API_KEY'],'test-secret')
        self.assertNotIn('test-secret',output.getvalue())

    def test_module_entrypoint_returns_noninteractive_error(self):
        with patch('sys.stdin.isatty',return_value=False), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            runpy.run_path(str(Path('scripts/run_expanded_batch.py').resolve()),run_name='__main__')
        self.assertEqual(error.exception.code,1)
