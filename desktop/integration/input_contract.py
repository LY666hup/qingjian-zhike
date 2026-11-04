"""Four independent source declarations; no inference and no network calls."""
from urllib.parse import urlsplit,parse_qs
from datetime import datetime,timezone

SLOTS={'original':('A','original'),'gray':('A','gray'),'gaussian':('A','gaussian'),'histogram':('A','histogram'),'B':('B',None)}
def origin(url):
 p=urlsplit(url)
 if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.fragment:raise ValueError('Invalid credential-free HTTP URL')
 return p.scheme,p.hostname,p.port or (443 if p.scheme=='https' else 80)
def validate_profiles(data,now=None):
 if data.get('schema')!='qjzc.four_inputs.draft1':raise ValueError('Unsupported profile schema')
 profiles=data.get('inputs',{});result={};common=None
 for slot,p in profiles.items():
  if slot not in SLOTS:raise ValueError('Unknown slot')
  camera,mode=SLOTS[slot]
  if p.get('camera_id')!=camera or mode is not None and p.get('processing_mode')!=mode:raise ValueError('Slot identity mismatch')
  if p.get('processing_mode') not in ('original','gray','gaussian','histogram'):raise ValueError('Invalid mode')
  if not p.get('session_id') or not p.get('boot_id') or p.get('processor')!='fpga':raise ValueError('Missing window identity')
  sha=p.get('fpga_bit_sha256','')
  if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Invalid SHA')
  o=origin(p['video_url'])
  if origin(p['status_url'])!=o or common is not None and o!=common:raise ValueError('All four inputs must have the declared same origin')
  common=o
  if any(v!=p['session_id'] for v in parse_qs(urlsplit(p['video_url']).query).get('session',[])):raise ValueError('URL session mismatch')
  expiry=datetime.fromisoformat(p['authorized_until'])
  if expiry.tzinfo is None or expiry.timestamp()<=(now if now is not None else datetime.now(timezone.utc).timestamp()):raise ValueError('Authorization deadline expired or not timezone-aware')
  if any(p['video_url']==old['video_url'] for old in result.values()):raise ValueError('Do not duplicate a stream URL into another slot')
  result[slot]=dict(p)
 return result
def validate_status(profile,s):
 for k in ('camera_id','processing_mode','session_id','boot_id','fpga_bit_sha256','processor'):
  if s.get(k)!=profile[k]:raise ValueError('Status identity mismatch: '+k)
 if s.get('state') not in ('CAPTURING','ENDED','ARMED_NO_CAPTURE'):raise ValueError('Unknown state')
 return s
