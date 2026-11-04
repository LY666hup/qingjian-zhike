"""Offline GUI by default, with local network preflight before loading a profile."""
from pathlib import Path
import argparse, json, subprocess, sys

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--profile',type=Path)
parser.add_argument('--expected-sha')
args=parser.parse_args()
if args.profile and not args.expected_sha:
    parser.error('--expected-sha is required with --profile')
report=ROOT/'startup_loopback_result.json'
probe=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'loopback_probe.py'),'--output',str(report)])
if probe.returncode or not json.loads(report.read_text(encoding='utf8'))['checks']['loopback_echo'].get('ok'):
    print('STARTUP BLOCKED: local networking failed; no board request was made. Start this launcher from normal Windows Explorer/PowerShell.',flush=True)
    raise SystemExit(2)
print('LOCAL NETWORK PREFLIGHT PASS; no RK connection tested or started.',flush=True)
runtime=ROOT.parent/'integration'
if not runtime.is_dir():
    runtime=ROOT.parent/'integration'
if not runtime.is_dir():
    raise SystemExit('Five-source runtime directory missing; retain the handoff directory structure.')
sys.path.insert(0,str(runtime))
import sprint_app
from pose_display_bridge import display_class
from status_evidence_receiver import EvidenceStrictFrames
sprint_app.StrictFrames=EvidenceStrictFrames
if args.profile:
    import profile_loader as loader
    loader.PROFILE=args.profile.resolve()
    loader.EXPECTED_SHA=args.expected_sha.lower()
    loader.EVIDENCE=ROOT/'NEW_PROFILE_LOADED.json'
    sprint_app.SprintMonitor=loader.ConfigMonitor
else:
    sprint_app.SprintMonitor=display_class()
raise SystemExit(sprint_app.main())
