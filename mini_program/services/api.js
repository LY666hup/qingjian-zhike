const config=require('../config');
const http=require('./http');
const engine=require('../mock/engine');
const copy=x=>JSON.parse(JSON.stringify(x));
function teacherOnly(){if(config.mode==='mock'&&require('./session').get().role!=='teacher')throw Error('班级数据仅对授权教师开放');}
function checkStudent(id){if(config.mode==='mock'&&require('./session').get().role==='student'&&id!==require('./session').get().student_id)throw Error('只能查看自己的训练记录');}
function get(path,read){return config.mode==='mock'?Promise.resolve().then(()=>copy(read(engine.snapshot()))):http.request(path);}
function getClassroomStatus(){return get('/classrooms/current',s=>{teacherOnly();return s.classroom;});}
function getStudentList(){return get('/classrooms/current/students',s=>{teacherOnly();return s.students;});}
function getStudentDetail(id){return get('/students/'+encodeURIComponent(id),s=>{checkStudent(id);const v=s.students.find(x=>x.student_id===id);if(!v)throw new Error('未找到学生档案');return v;});}
function query(options){return Object.keys(options).filter(k=>options[k]!==undefined&&options[k]!=='').map(k=>encodeURIComponent(k)+'='+encodeURIComponent(options[k])).join('&');}
function getStudentHistory(id,period='7',filters={}){return get('/students/'+encodeURIComponent(id)+'/history?'+query({period,...filters}),s=>{checkStudent(id);if(!s.students.some(x=>x.student_id===id))throw Error('未找到学生档案');return require('./demo-records').history(id,period,filters);});}
function getDeviceStatus(){return get('/devices/status',s=>s.device);}
function getClassroomHistory(filters={}){const suffix=query(filters);return get('/classrooms/history'+(suffix?'?'+suffix:''),()=>{teacherOnly();return require('./demo-records').classrooms(filters);});}
function getTrainingReport(id,self=false,studentId=''){return get((self?'/me':'/students/'+encodeURIComponent(studentId))+'/reports/'+encodeURIComponent(id),()=>{const report=require('./demo-records').report(id);checkStudent(report.student_id);if(!self&&studentId!==report.student_id)throw Error('报告与学生身份不匹配');if(self&&require('./session').get().student_id!==report.student_id)throw Error('只能查看自己的训练记录');return report;});}
function getClassroomReport(id){return get('/classrooms/'+encodeURIComponent(id)+'/reports',()=>{teacherOnly();return require('./demo-records').classReports(id);});}
function controlDemo(command){if(config.mode==='mock'&&require('./session').get().role!=='teacher')return Promise.reject(new Error('演示课堂仅由教师控制'));if(config.mode!=='mock')return Promise.reject(new Error('真实服务模式不提供演示控制'));return Promise.resolve(engine.control(command));}

function getViewer(){return config.mode==='mock'?Promise.resolve({...require('./session').get(),name:require('./session').get().role==='teacher'?'演示教师':'示例学生'}):http.request('/me');}
function getMyProfile(){return config.mode==='mock'?getStudentDetail(require('./session').get().student_id):http.request('/me/profile');}
function getMyClassroom(){return get('/me/classroom',s=>{const c=s.classroom;return {class_id:c.class_id,class_name:c.class_name,action:c.action,action_name:c.action_name,lesson:c.lesson,date:c.date,time:c.time,stage:c.stage};});}
function getMyHistory(period='7',filters={}){return config.mode==='mock'?getStudentHistory(require('./session').get().student_id,period,filters):http.request('/me/history?'+query({period,...filters}));}
function getClassStatistics(){return get('/classrooms/current/statistics',s=>{teacherOnly();return require('./insights').classSummary(s.students);});}
async function buildAdvice(goal='动作稳定性'){
 const viewer=await getViewer();
 if(config.mode!=='mock')return http.request(viewer.role==='teacher'?'/classrooms/current/advice':'/me/advice','POST',{goal});
 if(viewer.role==='teacher'){
  const stats=await getClassStatistics();const first=stats.issues[0];
  return {source:'本地规则草稿 · 非大模型生成',scope:'当前样例课堂',evidence:[stats.total?stats.total+'名学生，平均动作质量'+stats.average+'分':'当前没有可供统计的课堂记录',...stats.issues.map(x=>x.name+'：'+x.people+'人、'+x.count+'次事件')],title:!stats.total?'先完成一次课堂记录':first?'优先关注：'+first.name:'保持观察与分组指导',steps:!stats.total?['积累结构化课堂记录后再形成教学草稿。']:first?['本轮关注“'+goal+'”，结合现场动作复核“'+first.name+'”事件，确认是否需要个别指导。','在下一组前安排动作示范，再观察同一问题是否重复出现。','对比下一次同项目课堂记录后，再决定是否调整练习安排。']:['查看学生完成情况。','围绕“'+goal+'”结合实际表现安排下一次训练。'],goal,review:'规则草稿；本机审核状态见页面末尾，尚未调用AI服务。'};
 }
 const h=await getMyHistory();const s=require('./insights').personalSummary(h);
 return {source:'本地规则草稿 · 非大模型生成',scope:'最近'+s.count+'次样例训练',evidence:[s.count?'平均动作质量'+s.average+'分':'暂无训练记录',s.count?'其中'+s.kneeSessions+'次训练记录有膝内扣事件，合计'+s.kneeEvents+'次':'暂无动作事件统计',s.change===null?'记录不足以比较首末评分':'首末评分变化'+(s.change>=0?'+':'')+s.change+'分'],title:s.count?'下一次重点：'+goal:'先完成一次训练记录',steps:s.count?['训练前请教师确认动作要点，按课堂安排练习。','练习时关注“'+goal+'”，如遇不适及时停止并告知教师。','结束后查看同类动作事件是否减少，再与教师讨论后续重点。']:['训练记录积累后再生成个性化草稿。'],goal,review:'由样例统计形成的规则草稿，待教师审核；尚未调用AI服务。'};
}
async function getAdvice(goal='动作稳定性'){
 const advice=await buildAdvice(goal);
 if(config.mode!=='mock')return advice;
 const viewer=await getViewer(),workflow=require('./advice-workflow');
 const advice_id=workflow.fingerprint({role:viewer.role,student_id:viewer.role==='student'?viewer.student_id:null,goal,evidence:advice.evidence,steps:advice.steps});
 return {...advice,advice_id,workflow:workflow.status(viewer,advice_id)};
}
async function saveAdviceWorkflow(id,patch){const viewer=await getViewer();return config.mode==='mock'?require('./advice-workflow').save(viewer,id,patch):http.request('/advice/'+encodeURIComponent(id)+'/'+(viewer.role==='teacher'?'review':'feedback'),'POST',patch);}
function getTimetable(month){return get('/me/timetable?month='+encodeURIComponent(month),()=>({source:'mock',lessons:require('../utils/calendar').demoLessons(month)}));}
function controlSession(command){if(config.mode!=='test')return controlDemo(command);return http.request('/test/snapshot').then(s=>http.request('/test/command','POST',{command,round:s.classroom.round,command_id:'mini_'+Date.now()+'_'+Math.random().toString(16).slice(2)}));}
function correctCount(id,value,reason,revision,round){if(config.mode!=='test')return Promise.reject(Error('计数修正尚未连接后端'));return http.request('/test/command','POST',{command:'correct',id,value,reason,expected_revision:revision,round,command_id:'fix_'+Date.now()+'_'+Math.random().toString(16).slice(2)});}
function getSessionSetup(){return http.request('/test/snapshot');}
function prepareSession(ids,action,target,round){return http.request('/test/command','POST',{command:'prepare',ids,action,target,round,command_id:'round_'+Date.now()+'_'+Math.random().toString(16).slice(2)});}
module.exports={getSessionSetup,prepareSession,controlSession,correctCount,getTimetable,getViewer,getMyProfile,getMyClassroom,getMyHistory,getClassStatistics,getAdvice,saveAdviceWorkflow,getTrainingReport,getClassroomReport,getClassroomStatus,getStudentList,getStudentDetail,getStudentHistory,getDeviceStatus,getClassroomHistory,controlDemo};
