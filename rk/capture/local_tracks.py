"""Conservative local box tracking, not student recognition or durable identity."""
from dataclasses import dataclass
import math

def iou(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    inter=x*y;den=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/den if den>0 else 0

@dataclass
class Track:
    key:str
    box:list
    hits:int
    timestamp:int
    eligible:bool=False

class LocalTracks:
    def __init__(self,max_people=2):
        self.limit=max_people;self.next_id=1;self.tracks={};self.last_timestamp=None;self.reason='waiting'

    def retire_all(self,reason):
        self.tracks={};self.reason=reason

    def update(self,boxes,timestamp):
        if type(timestamp)is not int or self.last_timestamp is not None and timestamp<=self.last_timestamp:
            self.retire_all('time_regression_or_duplicate');return []
        gap=None if self.last_timestamp is None else timestamp-self.last_timestamp;self.last_timestamp=timestamp
        for b in boxes:
            if len(b)!=4 or any(type(v)not in(int,float)or not math.isfinite(v)for v in b)or not(b[0]<b[2]and b[1]<b[3]):raise ValueError('Invalid box')
        if len(boxes)>self.limit:
            self.retire_all('capacity_exceeded');return []
        if gap is not None and gap>1200:self.retire_all('analysis_gap')
        # Overlap/crossing is not sufficient evidence for a person-specific count.
        if any(iou(a,b)>.15 for i,a in enumerate(boxes)for b in boxes[i+1:]):
            self.retire_all('ambiguous_overlap');return []
        old=list(self.tracks.values());scores=[[iou(t.box,b)for b in boxes]for t in old]
        candidates=[]
        for i,t in enumerate(old):
            ranked=sorted(((v,j)for j,v in enumerate(scores[i])if v>=.25),reverse=True)
            if len(ranked)>1 and ranked[0][0]-ranked[1][0]<.20:
                self.retire_all('ambiguous_assignment');return []
            if ranked:candidates.append((ranked[0][0],i,ranked[0][1]))
        if len({j for _,_,j in candidates})!=len(candidates):
            self.retire_all('ambiguous_assignment');return []
        matched={j:old[i]for _,i,j in candidates};current={}
        for j,b in enumerate(boxes):
            previous=matched.get(j)
            if previous:
                t=Track(previous.key,list(b),previous.hits+1,timestamp,previous.hits+1>=3)
            else:
                t=Track('T'+str(self.next_id),list(b),1,timestamp,False);self.next_id+=1
            current[t.key]=t
        self.tracks=current;self.reason='ok'if current else'no_person'
        return list(current.values())

def crop_roi(box,width,height,pad=.20):
    x1,y1,x2,y2=box;dx=(x2-x1)*pad;dy=(y2-y1)*pad
    return [max(0,int(math.floor(x1-dx))),max(0,int(math.floor(y1-dy))),min(width,int(math.ceil(x2+dx))),min(height,int(math.ceil(y2+dy)))]

def project_landmarks(points,roi,width,height):
    from types import SimpleNamespace
    x1,y1,x2,y2=roi;cw=x2-x1;ch=y2-y1
    # Preserve off-image coordinates; do not clamp the provider's pose geometry.
    return [SimpleNamespace(x=(x1+p.x*cw)/width,y=(y1+p.y*ch)/height,z=p.z*cw/width,
                            visibility=p.visibility,presence=p.presence)for p in points]

def pose_belongs_to_box(points,box,width,height):
    """Ownership gate only, not a modification to any original action threshold."""
    anchors=[points[i]for i in(11,12,23,24)if points[i].visibility>=.5]
    if len(anchors)<2:return False
    x=sum(p.x for p in anchors)/len(anchors)*width;y=sum(p.y for p in anchors)/len(anchors)*height
    return box[0]<=x<=box[2]and box[1]<=y<=box[3]
