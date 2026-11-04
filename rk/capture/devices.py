"""Read-only current RK device discovery. Never initializes RKNN."""
from pathlib import Path
import subprocess

def npu_nodes():
 result=[]
 for node in Path('/dev/dri').glob('renderD*'):
  dev=Path('/sys/class/drm')/node.name/'device';s=str((dev/'driver').resolve()).lower();p=dev/'of_node/compatible'
  if p.exists():s+=p.read_bytes().decode(errors='replace').lower()
  if 'npu'in s:result.append(node)
 if Path('/dev/rknpu').exists():result.append(Path('/dev/rknpu'))
 return result

def owners(require_idle=False):
 nodes=npu_nodes()
 if not nodes:raise RuntimeError('Cannot identify current NPU device; coordinate with Ly')
 out={}
 for node in [Path('/dev/video22')]+nodes:
  if not node.exists():raise RuntimeError('Device missing: '+str(node))
  p=subprocess.run(['fuser','-v',str(node)],capture_output=True,text=True,timeout=3)
  out[str(node)]=dict(rc=p.returncode,out=p.stdout,err=p.stderr)
  if require_idle and(p.returncode!=1 or p.stdout or p.stderr):raise RuntimeError('Device occupied or query failed: '+str(node))
 return out
