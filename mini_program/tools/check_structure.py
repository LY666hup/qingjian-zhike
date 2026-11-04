from pathlib import Path
import json
import subprocess
import re
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
for p in root.rglob('*.json'):
    json.loads(p.read_text(encoding='utf-8'))
for p in root.rglob('*.wxml'):
    source = p.read_text(encoding='utf-8')
    for expression in re.findall(r'\{\{(.*?)\}\}', source, re.S):
        if re.search(r'&(?:amp|lt|gt|quot|apos);', expression):
            raise ValueError(f'{p}: HTML/XML entities must not be used as WXML expression operators')
    ET.fromstring('<root xmlns:wx="urn:wx">' + source + '</root>')
for p in root.rglob('*.js'):
    subprocess.run(['node', '--check', str(p)], check=True, capture_output=True)
print('PASS: JSON parsing, WXML XML tag structure and JavaScript syntax. Not a WeChat compiler check.')
