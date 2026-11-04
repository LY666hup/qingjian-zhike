"""Action ACK is a value, not a heartbeat. Never fsync the same value repeatedly."""
import copy
from control import atomic
class ChangedACK:
    def __init__(self,path):self.path=path;self.previous=None;self.initial=True
    def update(self,value):
        if not self.initial and value==self.previous:return False
        atomic(self.path,value);self.previous=copy.deepcopy(value);self.initial=False;return True
