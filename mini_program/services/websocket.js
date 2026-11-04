const config=require('../config');
const engine=require('../mock/engine');
// 统一完整快照事件。返回清理函数；页面隐藏必须调用。
function subscribeClassroom(onData,onState){
 if(config.mode==='mock'){
   onState('demo');
   const timer=setInterval(()=>{const s=engine.snapshot();onData({type:'classroom.snapshot',version:1,source:'mock',timestamp:new Date().toISOString(),data:{classroom:s.classroom,students:s.students}});},config.refreshMs);
   return ()=>clearInterval(timer);
 }
 if(config.mode==='test'){
  let closed=false,timer;const http=require('./http');
  const poll=async()=>{try{const s=await http.request('/test/snapshot');if(!closed){onState('test_connected');onData({data:s});}}catch(e){if(!closed)onState('offline');}finally{if(!closed)timer=setTimeout(poll,500);}};poll();
  return ()=>{closed=true;clearTimeout(timer);};
 }
 if(!config.socketURL){onState('error');return ()=>{};}
 let closed=false;
 const socket=wx.connectSocket({url:config.socketURL,complete:()=>{}});
 socket.onOpen(()=>{onState('online');socket.send({data:JSON.stringify({type:'subscribe',class_id:'class_001'})});});
 socket.onMessage(r=>{if(closed)return;try{const e=JSON.parse(r.data);if(e.type==='classroom.snapshot'&&e.version===1&&e.data&&e.data.classroom&&Array.isArray(e.data.students))onData(e);}catch(_){onState('invalid');}});
 socket.onError(()=>onState('error'));socket.onClose(()=>{if(!closed)onState('offline');});
 return ()=>{closed=true;socket.close();};
}
module.exports={subscribeClassroom};
