import hashlib,time
FIELDS=('session_id','boot_id','camera_id','processing_mode','fpga_bit_sha256','processor')
def verify_pair(record,jpeg,panel,yolo):
    identity={k:record[k]for k in FIELDS}
    if hashlib.sha256(jpeg).hexdigest()!=record['jpeg_sha256']:raise ValueError('JPEG SHA differs')
    multi=panel.get('schema')=='qjzc.pose.multi_action.v1'
    if not multi and panel.get('schema')!='qjzc.pose.action_panel.v1':raise ValueError('Panel schema')
    for k in FIELDS:
        if panel.get(k)!=identity[k] or panel.get('identity',{}).get(k)!=identity[k] or yolo.get('identity',{}).get(k)!=identity[k]:raise ValueError('Identity '+k)
    for k in ('input_record_id','jpeg_sha256'):
        if panel.get(k)!=record[k] or yolo.get(k)!=record[k]:raise ValueError('Frame '+k)
    if (panel.get('preview_size') if multi else [panel.get('image',{}).get('width'),panel.get('image',{}).get('height')])!=[record['width'],record['height']]:raise ValueError('Panel dimensions')
    timing=panel.get('timing',{})
    if timing.get('timestamp_ms')!=record['timestamp_ms'] or timing.get('time_source')!=record['time_source']:raise ValueError('Time source')
    if (panel.get('model_sha256') if multi else panel.get('inference',{}).get('model_sha256'))!='4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad':raise ValueError('Pose model')
    return True

def live_bundle(config,bundle,request,now=None):
    now=time.monotonic()*1000 if now is None else now
    if not request or not bundle or now-config['expires_monotonic_ms']>=0:return False
    y=bundle.get('yolo',{});p=bundle.get('panel',{})
    return (0<=now-bundle.get('completed_ms',-1e12)<=1500 and
            y.get('binding_id')==request.get('binding_id') and y.get('identity')==request.get('identity') and
            all(p.get(k)==request['identity'].get(k)for k in FIELDS) and
            p.get('input_record_id')==y.get('input_record_id') and p.get('jpeg_sha256')==y.get('jpeg_sha256') and
            (p.get('schema')!='qjzc.pose.multi_action.v1' or (p.get('binding_id')==request.get('binding_id') and p.get('preview_size')==y.get('preview_size') and all(p.get('timing',{}).get(k)==y.get(k) for k in ('timestamp_ms','time_source')))))
