"""Retention for this new live run only; never touches previous evidence folders."""
from pathlib import Path
from collections import deque
class JPEGRetention:
 def __init__(self,root,count=32,byte_limit=48*1024**2):self.root=Path(root).resolve();self.count=count;self.limit=byte_limit;self.queues={};self.used={};self.pins={}
 def keep(self,key,folder,pin=False):
  folder=Path(folder).resolve()
  if not folder.is_relative_to(self.root)or folder==self.root:raise ValueError('retention outside owned JPEG directory')
  files=[folder/'frame.jpg',folder/'record.json'];cost=sum(p.stat().st_size for p in files)
  q=self.queues.setdefault(key,deque());self.pins.setdefault(key,set());self.used[key]=self.used.get(key,0)+cost
  if pin:self.pins[key].add(folder)
  q.append((folder,cost))
  while len(q)>self.count+len(self.pins[key])or self.used[key]>self.limit:
   candidates=[x for x in q if x[0]not in self.pins[key]]
   if not candidates:raise ValueError('pinned JPEG budget exceeded')
   old,size=candidates[0]
   # Latest was already atomically published before retirement. Keep the new frame.
   if old==folder:raise ValueError('one JPEG exceeds retention budget')
   for p in (old/'frame.jpg',old/'record.json'):
    if not p.resolve().is_relative_to(self.root):raise ValueError('unsafe retention target')
    p.unlink()
   old.rmdir();q.remove((old,size));self.used[key]-=size
