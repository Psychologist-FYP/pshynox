#!/usr/bin/env python3
"""Run the two-person Wi-Fi demo with a local CA and an HTTPS frontend.

No system trust store or firewall is changed. Trust data/lan/ca.pem manually on
participating devices. Keys stay under ignored data/lan, never in public/.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def find_lan_ip():
    try:
        result = subprocess.run(['ip', '-j', 'route', 'get', '1.1.1.1'], capture_output=True, text=True, check=True)
        # Reading the routing table does not send traffic to the destination.
        candidate = json.loads(result.stdout)[0].get('prefsrc')
        if candidate and ipaddress.ip_address(candidate).is_private:
            return candidate
    except (OSError, ValueError, KeyError, IndexError, subprocess.CalledProcessError):
        pass
    return None


def certificates(folder, address):
    folder.mkdir(parents=True, exist_ok=True)
    ca, ca_key = folder / 'ca.pem', folder / 'ca-key.pem'
    cert, key = folder / 'meeting.pem', folder / 'meeting-key.pem'
    manifest = folder / 'address.txt'
    def openssl(*args):
        result = subprocess.run(['openssl', *map(str, args)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('Certificate generation failed: ' + result.stderr)
    if not ca.exists() or not ca_key.exists():
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '365',
                '-keyout', ca_key, '-out', ca, '-subj', '/CN=Pshynox Wi-Fi Demo CA',
                '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        ca_key.chmod(0o600)
    valid = cert.exists() and key.exists() and manifest.exists() and manifest.read_text() == address
    if valid:
        valid = subprocess.run(['openssl', 'x509', '-checkend', '86400', '-noout', '-in', str(cert)], capture_output=True).returncode == 0
    if not valid:
        csr, ext = folder / 'meeting.csr', folder / 'meeting.ext'
        ext.write_text(f'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{address}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
        openssl('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', csr, '-subj', '/CN=Pshynox Wi-Fi Meeting')
        openssl('x509', '-req', '-in', csr, '-CA', ca, '-CAkey', ca_key, '-CAcreateserial', '-out', cert,
                '-days', '90', '-sha256', '-extfile', ext)
        key.chmod(0o600)
        manifest.write_text(address)
    return cert, key, ca


def free_port(host, port):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
        except OSError:
            raise RuntimeError(f'Port {port} is already in use. Stop the old server or choose different --api-port / --web-port values.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default=None, help='Wi-Fi IPv4 address of this laptop; auto-detected if possible')
    parser.add_argument('--api-port', type=int, default=8000)
    parser.add_argument('--web-port', type=int, default=5173)
    parser.add_argument('--cert-only', action='store_true', help='Prepare certificates without starting servers')
    args = parser.parse_args()
    address = args.ip or find_lan_ip()
    try:
        parsed = ipaddress.ip_address(address or '')
        if parsed.version != 4 or not parsed.is_private or parsed.is_loopback or parsed.is_unspecified:
            raise ValueError()
    except ValueError:
        parser.error('Pass --ip with the laptop’s Wi-Fi IPv4 address, for example --ip 192.168.1.20.')
    if not shutil.which('openssl'):
        parser.error('Install OpenSSL before running the LAN demo.')
    cert, key, ca = certificates(ROOT / 'data' / 'lan', address)
    print(f'\nTrust this public certificate on BOTH devices: {ca}\nNever share ca-key.pem or meeting-key.pem.\n', flush=True)
    if args.cert_only:
        return
    if not shutil.which('npm') or not (ROOT / 'apps/web/node_modules').is_dir():
        parser.error('Run npm ci in apps/web first.')
    free_port('127.0.0.1', args.api_port)
    free_port('0.0.0.0', args.web_port)
    env = os.environ.copy()
    env.update(MEETING_PUBLIC_ORIGIN=f'https://{address}:{args.web_port}',
               VITE_HTTPS_CERT=str(cert), VITE_HTTPS_KEY=str(key),
               VITE_API_TARGET=f'http://127.0.0.1:{args.api_port}')
    api_args = [sys.executable, '-m', 'uvicorn', 'services.api.main:app', '--host', '127.0.0.1', '--port', str(args.api_port)]
    if (ROOT / '.env').exists():
        api_args.extend(['--env-file', str(ROOT / '.env')])
    print(f'Doctor: https://localhost:{args.web_port}\nPatient invitations: https://{address}:{args.web_port}/meeting/...\nKeep this terminal open. Ctrl+C stops both servers.\n', flush=True)
    children = []
    try:
        children.append(subprocess.Popen(api_args, cwd=ROOT, env=env, start_new_session=True))
        children.append(subprocess.Popen(['npm', 'run', 'dev', '--', '--host', '0.0.0.0', '--port', str(args.web_port), '--strictPort'], cwd=ROOT / 'apps/web', env=env, start_new_session=True))
        import time
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        raise RuntimeError('A server stopped. Check the output above.')
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
