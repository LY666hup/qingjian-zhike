const config=require('../../config');
const api = require('../../services/api');
const view = require('../../utils/view');
const stream = require('../../services/websocket');
Page({
 data: {connectionBuild:'M3-1104',connecting:false,connectionError:'',setup:null,setupIds:[],setupActions:['深蹲','开合跳','俯卧撑'],setupActionIndex:0,setupTarget:'15',setupError:'',setupSaving:false,isTest:config.mode==='test',editing:null,editValue:'',editReason:'',saving:false,editError:'',filter:'all',query:'',students:[],visible:[],loading:true,error:'',streamState:'',filters:[{id:'all',name:'全部'},{id:'normal',name:'正常'},{id:'attention',name:'注意'},{id:'severe',name:'重点关注'}]},
 onShow(){this.active=true;return this.refresh();},
 onHide(){this.active=false;this.requestVersion++;this.stop();},
 onUnload(){this.active=false;this.requestVersion++;this.stop();},
 stop(){if(this.unsubscribe)this.unsubscribe();this.unsubscribe=null;},
 onPullDownRefresh(){return this.refresh();},
 async refresh(){
  this.stop();const version=this.requestVersion=(this.requestVersion||0)+1;
  this.setData({loading:true,error:'',classroom:null,students:[],visible:[],restricted:false});
  try{
   const viewer=await api.getViewer();
   if(viewer.role!=='teacher'){this.setData({restricted:true});return;}
   const [c,s]=await Promise.all([api.getClassroomStatus(),api.getStudentList()]);
   if(!this.active||version!==this.requestVersion)return;
   this.render(c,s);
   this.unsubscribe=stream.subscribeClassroom(e=>this.render(e.data.classroom,e.data.students),state=>this.setData({streamState:state}));
  }catch(e){if(this.active&&version===this.requestVersion)this.setData({error:e.message||'加载失败'});}
  finally{if(version===this.requestVersion){this.setData({loading:false});wx.stopPullDownRefresh();}}
 },
 render(c,s){this.setData({classroom:view.stage(c),students:s.map(view.student)});this.applyFilter();},
 applyFilter(){const {students,filter,query}=this.data;this.setData({visible:students.filter(s=>(filter==='all'||s.status===filter)&&(!query||s.name.includes(query)||s.student_id.includes(query)))});},
 filter(e){this.setData({filter:e.currentTarget.dataset.id});this.applyFilter();},
 search(e){this.setData({query:e.detail.value.trim()});this.applyFilter();},
 open(e){wx.navigateTo({url:'/pages/student/index?id='+e.currentTarget.dataset.id});},
 editCount(e){const s=this.data.students.find(s=>s.student_id===e.currentTarget.dataset.id);if(!s)return;this.setData({editing:{...s,round:this.data.classroom.round},editValue:String(s.reps),editReason:'',editError:''});},
 editValue(e){this.setData({editValue:e.detail.value});},editReason(e){this.setData({editReason:e.detail.value});},cancelEdit(){if(!this.data.saving)this.setData({editing:null});},
 async saveCount(){if(this.data.saving)return;const s=this.data.editing;if(!s)return;if(!/^\d+$/.test(this.data.editValue)||!this.data.editReason.trim()){this.setData({editError:'请输入整数次数和修正原因'});return;}this.setData({saving:true,editError:''});try{const snapshot=await api.correctCount(s.student_id,Number(this.data.editValue),this.data.editReason,s.count_revision,s.round);this.render(snapshot.classroom,snapshot.students);this.setData({editing:null});}catch(e){this.setData({editError:e.message});}finally{this.setData({saving:false});}},
 async connectConsole(){if(this.data.connecting)return;this.stop();this.setData({connecting:true,connectionError:'',classroom:null,students:[],visible:[]});const local=require('../../services/test-connection');config.mode='test';config.baseURL=local.baseURL;config.testToken=local.token;this.setData({isTest:true});try{const response=await require('../../services/http').request('/test/snapshot');if(response.source!=='TEST_MANUAL')throw Error('服务来源不匹配');this.setData({connectionBuild:local.build});await this.refresh();}catch(e){this.setData({connectionError:e.message,streamState:'offline'});}finally{this.setData({connecting:false});}},
 async newRound(){if(!this.data.isTest){this.setData({connectionError:'请先连接计数控制台'});return;}try{const s=await api.getSessionSetup();if(!['ready','finished'].includes(s.classroom.stage))throw Error('本轮尚未结束，请先结束归档，再新建一轮');s.roster=s.roster.map(x=>({...x,selected:s.students.some(y=>y.student_id===x.student_id)}));this.setData({setup:s,setupIds:s.students.map(x=>x.student_id),setupActionIndex:['squat','jumping_jack','pushup'].indexOf(s.classroom.action),setupTarget:String(s.students[0]?s.students[0].target_reps:15),setupError:''});}catch(e){this.setData({connectionError:e.message});}},
 chooseStudents(e){this.setData({setupIds:e.detail.value});},chooseAction(e){this.setData({setupActionIndex:Number(e.detail.value)});},chooseTarget(e){this.setData({setupTarget:e.detail.value});},cancelSetup(){if(!this.data.setupSaving)this.setData({setup:null});},
 async saveSetup(){if(this.data.setupSaving||!this.data.setup)return;const value=this.data.setupTarget;if(!/^\d+$/.test(value)||!this.data.setupIds.length){this.setData({setupError:'请选择学生并填写正整数目标'});return;}this.setData({setupSaving:true,setupError:''});try{const s=await api.prepareSession(this.data.setupIds,['squat','jumping_jack','pushup'][this.data.setupActionIndex],Number(value),this.data.setup.classroom.round);this.setData({query:'',filter:'all',setup:null,connectionError:''});this.render(s.classroom,s.students);}catch(e){this.setData({setupError:e.message});}finally{this.setData({setupSaving:false});}},
 closeDebug(){if(typeof wx.setEnableDebug!=='function'){wx.showToast({title:'当前微信不支持，请在右上角开发调试中关闭',icon:'none'});return;}wx.setEnableDebug({enableDebug:false,success(){wx.showModal({title:'已请求关闭微信调试',content:'请完全退出小程序后重新进入。当前局域网HTTP联调关闭调试后可能无法连接后台；需要继续联调时，可在微信菜单重新打开调试。',showCancel:false});},fail(e){wx.showModal({title:'关闭未成功',content:e.errMsg||'请在微信右上角菜单的开发调试中关闭',showCancel:false});}});},
 personal(){wx.switchTab({url:'/pages/mine/index'});},
 async control(e){try{const s=await api.controlSession(e.currentTarget.dataset.command);this.render(s.classroom,s.students);}catch(e){wx.showToast({title:e.message,icon:'none'});}}
});
