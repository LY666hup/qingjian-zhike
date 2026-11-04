from pathlib import Path
import argparse
import sprint_app
from pose_display_bridge import display_class
from status_evidence_receiver import EvidenceStrictFrames

parser=argparse.ArgumentParser(description="Offline by default. A profile is loaded only; no video connection.")
parser.add_argument('--profile',type=Path)
parser.add_argument('--expected-sha')
args=parser.parse_args()
sprint_app.StrictFrames=EvidenceStrictFrames
if args.profile:
    if not args.expected_sha:parser.error('--expected-sha is required with --profile')
    import open_authorized_config_fe07ea72 as loader
    loader.PROFILE=args.profile.resolve()
    loader.EXPECTED_SHA=args.expected_sha.lower()
    loader.EVIDENCE=Path(__file__).resolve().parent/'local_profile_load.json'
    sprint_app.SprintMonitor=loader.ConfigMonitor
else:sprint_app.SprintMonitor=display_class()
raise SystemExit(sprint_app.main())
