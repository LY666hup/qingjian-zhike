#!/usr/bin/env python3
"""Step 1 - check the environment before installing anything.

This step never installs.  It answers: is this interpreter, this libc and these local wheels a consistent set?
If not it prints what to send back instead of guessing versions.

Output lines: `CHECK OK: ...`, `CHECK FAIL: <code> :: <detail>`, then a JSON block on stdout.
Exit codes: 0 all good, 1 environment mismatch (fix before installing), 2 cannot even read the wheels dir.
"""
from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import sys
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WHEELS = os.path.join(ROOT, 'wheels')
WANT_PY = (3, 11)
WANT_PLATFORM = 'aarch64'


def libc():
    try:
        ver = platform.libc_ver()
        return {'libc': ver[0] or 'unknown', 'libc_version': ver[1] or 'unknown'}
    except Exception:  # noqa: BLE001
        return {'libc': 'unknown', 'libc_version': 'unknown'}


def glibc_tuple(version):
    m = re.match(r'(\d+)\.(\d+)', version or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def wheel_tag(name):
    """(python_tag, abi_tag, platform_tag) from a wheel filename."""
    base = name[:-4] if name.endswith('.whl') else name
    parts = base.split('-')
    return parts[-3], parts[-2], parts[-1]


def main():
    problems, notes = [], []
    py = sys.version_info[:2]
    plat = sysconfig.get_platform()
    machine = platform.machine()
    lc = libc()
    print('CHECK interpreter: %s (%s)' % (platform.python_version(), sys.executable))
    print('CHECK platform: %s machine=%s %s %s' % (plat, machine, lc['libc'], lc['libc_version']))

    if py != WANT_PY:
        problems.append(('python_version', 'need %d.%d, got %d.%d' % (WANT_PY + py)))
    else:
        print('CHECK OK: python version %d.%d' % py)

    if machine.lower() not in ('aarch64', 'arm64'):
        problems.append(('machine', 'need aarch64, got %s' % machine))
    else:
        print('CHECK OK: machine aarch64')

    if not os.path.isdir(WHEELS):
        print('CHECK FAIL: wheel_dir :: %s missing' % WHEELS)
        return 2
    wheels = sorted(glob.glob(os.path.join(WHEELS, '*.whl')))
    if not wheels:
        print('CHECK FAIL: wheel_dir :: no .whl in %s' % WHEELS)
        return 2

    glibc_v = glibc_tuple(lc['libc_version'])
    if lc['libc'] != 'glibc' or not glibc_v:
        problems.append(('libc_unknown',
                         'cannot read the libc version here (%s %s); run this on the board, not on a dev box'
                         % (lc['libc'], lc['libc_version'])))
    musl_on_glibc, too_new, wrong_abi = [], [], []
    for w in wheels:
        name = os.path.basename(w)
        pyt, abi, plattag = wheel_tag(name)
        if 'musllinux' in plattag:
            musl_on_glibc.append(name)
        for m in re.finditer(r'manylinux_(\d+)_(\d+)', plattag):
            need = (int(m.group(1)), int(m.group(2)))
            if glibc_v and need > glibc_v:
                too_new.append((name, 'manylinux_%d_%d' % need))
        if abi.startswith('cp') and not pyt.startswith('cp%d%d' % py) and 'abi3' not in abi:
            wrong_abi.append((name, pyt, abi))
    if musl_on_glibc:
        problems.append(('musllinux_wheel_on_glibc',
                         'these wheels need musl but the board uses %s: %s' % (lc['libc'], musl_on_glibc)))
    else:
        print('CHECK OK: no musllinux wheel in the set')
    if too_new:
        problems.append(('glibc_too_old', '%s' % too_new[:4]))
    else:
        print('CHECK OK: every manylinux tag is older than the board glibc')
    if wrong_abi:
        problems.append(('abi_mismatch', '%s' % wrong_abi[:4]))
    else:
        print('CHECK OK: cp%d%d / abi3 wheels only' % py)

    # a venv must be creatable and must not be the system python
    free = shutil.disk_usage(ROOT).free / 1e9
    print('CHECK disk: %.1f GB free under %s' % (free, ROOT))
    if free < 1.0:
        problems.append(('disk_space', 'need >=1 GB, got %.2f GB' % free))
    else:
        print('CHECK OK: enough space for a venv')

    model = os.path.join(ROOT, 'models', 'pose_landmarker_full.task')
    if os.path.isfile(model):
        print('CHECK OK: model present (%d bytes)' % os.path.getsize(model))
    else:
        notes.append('model_missing: put pose_landmarker_full.task in %s (sha256 4eaa5eb7...c4ad)'
                     % os.path.dirname(model))

    summary = {'python': platform.python_version(), 'platform': plat, 'machine': machine,
               'libc': lc['libc'], 'libc_version': lc['libc_version'],
               'wheels': [os.path.basename(w) for w in wheels],
               'wheel_count': len(wheels), 'notes': notes,
               'problems': [{'code': c, 'detail': d} for c, d in problems],
               'verdict': 'OK' if not problems else 'MISMATCH'}
    os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
    with open(os.path.join(ROOT, 'logs', 'step1_env.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    for c, d in problems:
        print('CHECK FAIL: %s :: %s' % (c, d))
    if problems:
        print('')
        print('SEND THIS BACK (do not install yet):')
        for line in ('python -VV', 'ldd --version | head -1', 'uname -m',
                     'python3 -c "import sysconfig;print(sysconfig.get_platform())"'):
            print('  ' + line)
        print(json.dumps(summary, ensure_ascii=False))
        return 1
    print('CHECK OK: environment matches the wheel set')
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())