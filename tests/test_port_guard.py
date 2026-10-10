"""Exercise the shell guard against Docker reservations and host sockets."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class PortGuardTests(unittest.TestCase):
    def test_protocol_address_and_running_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, script in {
                'docker': '#!/bin/bash\nprintf "%s" "$DOCKER_RESULT"\n',
                'ss': '#!/bin/bash\nif [[ $* == *-ltn* ]]; then printf "%s" "$TCP_RESULT"; else printf "%s" "$UDP_RESULT"; fi\n',
            }.items():
                path = root / name
                path.write_text(script)
                path.chmod(0o700)
            socket = 'LISTEN 0 4096 127.0.0.1:8183 0.0.0.0:*'
            owned = 'arena|running|jokes-arena|127.0.0.1:8183->8080/tcp'
            cases = [('', '', '', 0), (owned, socket, '', 0),
                     ('arena|exited|jokes-arena|', socket, '', 1),
                     ('other|running|other|127.0.0.1:8183->8080/tcp', '', '', 1),
                     (owned, socket.replace('127.0.0.1', '0.0.0.0'), '', 1),
                     (owned, socket, 'UNCONN 0 0 127.0.0.1:8183 0.0.0.0:*', 1),
                     (owned.replace('127.0.0.1', '0.0.0.0'), socket, '', 1),
                     ('', socket, '', 1)]
            for docker, tcp, udp, expected in cases:
                with self.subTest(docker=docker, tcp=tcp, udp=udp):
                    environment = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}',
                                   'DOCKER_RESULT': docker, 'TCP_RESULT': tcp, 'UDP_RESULT': udp}
                    result = subprocess.run(['bash', 'deployment/check-host-port.sh', '8183', 'jokes-arena'],
                                            env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
