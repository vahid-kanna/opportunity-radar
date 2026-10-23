"""Build an explicit text-only deployment ZIP, never a working-directory dump.
Usage: python package_release.py /absolute/path/opportunity-radar.zip
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import quality

FILES = ('main.py', 'server.py', 'db.py', 'storage.py', 'quality.py', 'scanner.py',
         'ats.py', 'feedclient.py', 'radar_profile.py', 'review.py',
         'templates/index.html', 'static/radar.js', 'static/radar.css',
         'UPGRADE.md', 'boards.example.json')


def build_release(destination, root=None):
    root = Path(root or Path(__file__).parent)
    destination = Path(destination).resolve()
    if destination.suffix.lower() != '.zip':
        raise ValueError('Destination must end in .zip')
    payloads = {name: (root / name).read_bytes() for name in FILES}
    seeds = json.loads((root / 'seed_data.json').read_text(encoding='utf-8'))
    seeds = [record for record in seeds if quality.is_public_record(record)]
    payloads['seed_data.json'] = json.dumps(seeds, ensure_ascii=False, indent=2).encode('utf-8')
    # Deliberately independent of unrelated uncommitted dependency edits.
    payloads['requirements.txt'] = b'Flask==3.1.0\nrequests==2.32.3\n'
    for content in payloads.values():
        content.decode('utf-8')
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())}
    payloads['SHA256SUMS.json'] = json.dumps(manifest, indent=2).encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Corrupt release archive')
        for name, digest in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError(f'Archive content mismatch: {name}')
    return dict(path=str(destination), files=len(payloads), public_seeds=len(seeds), sha256=hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    args = parser.parse_args()
    print(json.dumps(build_release(args.destination), indent=2))
