"""An explicit Windows-issued window; RK wall clock is never consulted."""
from datetime import datetime,timezone
def explicit_window_time(host_utc,host_until):
 try:
  a=datetime.fromisoformat(host_utc.replace('Z','+00:00'));b=datetime.fromisoformat(host_until.replace('Z','+00:00'))
  if a.tzinfo is None or b.tzinfo is None:raise ValueError('timezone missing')
  seconds=(b-a).total_seconds()
  if not 0<seconds<=3600:raise ValueError('window must be1..3600seconds')
  return seconds,b.astimezone(timezone.utc).isoformat()
 except (ValueError,AttributeError,TypeError) as e:raise ValueError('Need fresh Windows --host-utc and --host-until; never use RK wall clock or extend an old window')from e
