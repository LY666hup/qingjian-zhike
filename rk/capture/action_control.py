"""Exercise selection only. Never touches capture, processing mode or FPGA."""
from control import atomic,read,IDENTITY
import uuid,time
ACTIONS=('PUSHUP','SQUAT','CURL','LATERAL_RAISE','FRONT_RAISE','OVERHEAD_PRESS','BENT_OVER_ROW','OVERHEAD_TRICEPS','PLANK')

def desired(config):
    from pathlib import Path
    r=read(Path(config['run_dir'])/'ai/action_request.json')
    current=read(Path(config['run_dir'])/'ai/request.json')
    if r and current and r.get('binding_id')==current.get('binding_id'):return r
    approval=read(Path(config['run_dir'])/'capture_approval.json',{})
    return dict(action_token=approval.get('pose_action',config.get('pose_action','SQUAT')),action_revision='initial',binding_id=None)

def select_action(config,body):
    from pathlib import Path
    if time.monotonic()*1000>=config['expires_monotonic_ms']:raise ValueError('Session expired')
    root=Path(config['run_dir'])
    if read(root/'run.json',{}).get('state')=='ENDED':raise ValueError('Session ended')
    request=read(root/'ai/request.json')
    if not request or body.get('binding_id')!=request['binding_id']or body.get('identity')!=request['identity']:raise ValueError('Action binding differs')
    if body.get('action_token')not in ACTIONS:raise ValueError('Only original nine actions')
    revision=body.get('action_revision')
    if not isinstance(revision,str)or not revision.isalnum()or len(revision)!=32:raise ValueError('Action revision')
    old=desired(config)
    if old.get('action_revision')==revision:
        if old.get('action_token')!=body['action_token']:raise ValueError('Revision conflict')
        return old
    if body.get('previous_revision')!=old['action_revision']:raise ValueError('Action changed; stale request')
    value={k:body[k]for k in('action_token','action_revision','binding_id','identity')}
    atomic(root/'ai/action_request.json',value);return value

def matches_action(config,panel):
    r=desired(config)
    return panel.get('action_revision')==r['action_revision'] and panel.get('action_token')==r['action_token']
