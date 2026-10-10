"""Read a key privately and pass it to the generator environment."""
import getpass
import os
import subprocess
import sys


def main():
    if not sys.stdin.isatty():
        print('Run this command in your terminal so the key can be entered without echo.', file=sys.stderr)
        return 1
    environment = os.environ.copy()
    environment['OPENROUTER_API_KEY'] = getpass.getpass('OpenRouter API key: ')
    return subprocess.run([sys.executable, '-m', 'arena.populate', *sys.argv[1:]], env=environment).returncode


if __name__ == '__main__':
    sys.exit(main())
