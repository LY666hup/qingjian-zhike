"""Local Winsock permission comparison. Uses 127.0.0.1 only, never the RK."""
import argparse, json, socket, threading, sys
from pathlib import Path
from datetime import datetime

parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
result={'at':datetime.now().astimezone().isoformat(),'python':sys.executable,
        'scope':'127.0.0.1 only; no RK/HTTP/board access','checks':{}}
def error(exc):return {'ok':False,'type':type(exc).__name__,'errno':exc.errno,'winerror':getattr(exc,'winerror',None),'message':str(exc)}
try:
    with socket.create_connection(('127.0.0.1',9),timeout=1):
        result['checks']['connect_loopback_9']={'ok':True}
except OSError as exc:result['checks']['connect_loopback_9']=error(exc)
try:
    with socket.socket() as server:
        server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(2)
        port=server.getsockname()[1]
        def echo():
            with server.accept()[0] as conn:
                conn.settimeout(2);conn.sendall(conn.recv(32))
        thread=threading.Thread(target=echo,daemon=True);thread.start()
        with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
            client.sendall(b'QJZC-LOCAL-CHECK')
            assert client.recv(32)==b'QJZC-LOCAL-CHECK'
        thread.join(3)
        result['checks']['loopback_echo']={'ok':True,'thread_exited':not thread.is_alive()}
except OSError as exc:result['checks']['loopback_echo']=error(exc)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(result,ensure_ascii=False,indent=2))
