"""Return only an RSA-OAEP encrypted one-use invitation to the local operator."""
import base64
import json
from pathlib import Path
import sys
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from agent_brain.native_sync.hub import Hub


def issue(hub, public_pem):
    key = serialization.load_pem_public_key(public_pem)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError('an RSA reception key of at least 3072 bits is required')
    ticket = hub.invite('linkcrux', 'liuyang', ttl=86400)
    value = json.dumps({'hub': 'https://aihub0508.com', 'tenant': 'linkcrux',
                        'member_name': 'liuyang', 'invite': ticket,
                        'expires_at': int(time.time()) + 86400}).encode()
    return key.encrypt(value, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                          algorithm=hashes.SHA256(), label=None))


if __name__ == '__main__':
    public = base64.b64decode(sys.argv[1], validate=True)
    encrypted = issue(Hub(Path('/var/lib/amh-native/hub.sqlite')), public)
    print(base64.b64encode(encrypted).decode())
