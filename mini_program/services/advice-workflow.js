// Mock workflow storage is local to this Mini Program, never a backend approval.
const config=require('../config');
let memory={};
const storageKey='qingjian.demo.advice.v1';
function read(){if(typeof wx!=='undefined'&&wx.getStorageSync){const data=wx.getStorageSync(storageKey);return data&&data.version===1&&data.items?JSON.parse(JSON.stringify(data.items)):{};}return JSON.parse(JSON.stringify(memory));}
function key(viewer,id){return (viewer.role==='teacher'?'teacher:class_001':'student:'+viewer.student_id)+':'+id;}
function fingerprint(data){let h=2166136261;for(const c of JSON.stringify(data)){h^=c.charCodeAt(0);h=Math.imul(h,16777619);}return 'draft_'+(h>>>0).toString(16);}
function status(viewer,id){return read()[key(viewer,id)]||{state:'draft',note:'',feedback:'',savedAt:''};}
function save(viewer,id,patch){
 if(config.mode!=='mock')throw Error('真实审核和反馈须通过后端提交');
 if(!/^draft_[a-f0-9]+$/.test(id))throw Error('建议版本无效，请重新加载');
 if(viewer.role==='teacher'){
  if(!['reviewed','needs_revision'].includes(patch.state))throw Error('审核状态无效');
 }else if(viewer.role==='student'){
  if(!['helpful','question','unsuitable'].includes(patch.feedback))throw Error('反馈选项无效');
 }else throw Error('身份无效');
 const note=String(patch.note||'').trim();if(note.length>200)throw Error('备注最多200字');
 const items=read(),record={...status(viewer,id),...(viewer.role==='teacher'?{state:patch.state}:{feedback:patch.feedback}),note,savedAt:new Date().toISOString()};
 items[key(viewer,id)]=record;
 const keys=Object.keys(items);while(keys.length>100)delete items[keys.shift()];
 let scope='session';
 if(typeof wx!=='undefined'&&wx.setStorageSync){try{wx.setStorageSync(storageKey,{version:1,items});scope='local';}catch(_){throw Error('本机保存失败，请检查存储空间后重试');}}
 else memory=items;
 return {...record,storage:scope};
}
module.exports={fingerprint,status,save};
