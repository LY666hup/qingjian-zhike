// Clearly labelled synthetic history, shared by reports, filters and trend charts.
const seed=require('../mock/data');
const dates=['2026-09-30','2026-10-02','2026-10-05','2026-10-08','2026-10-12','2026-10-15','2026-10-18','2026-10-20'];
const values=[72,74,76,78,81,83,86,88];
const sessions=dates.map((date,i)=>({id:'lesson_'+String(i).padStart(3,'0'),date,class_name:seed.classroom.class_name,action:i===1||i===3||i===5?'jumping_jack':'squat',action_name:i===1||i===3||i===5?'开合跳':'深蹲'}));
const reports=sessions.flatMap((lesson,i)=>seed.students.map((student,j)=>{
 const rhythm=lesson.action==='jumping_jack';const events=Math.max(0,6-i);
 const errors=events?[{code:rhythm?'RHYTHM':'KNEE_IN',name:rhythm?'节奏不稳':'膝内扣',count:events}]:[];
 return {...student,report_id:'report_'+i+'_'+student.student_id,lesson_id:lesson.id,date:lesson.date,action:lesson.action,action_name:lesson.action_name,class_name:lesson.class_name,score:values[i]-j%4,reps:15,target_reps:15,phase:'FINISHED',status:errors.length?'attention':'normal',errors,rom:78+i,speed:76+i,stability:80+i,symmetry:77+i,completion:100,source:'MOCK / DEMO',stage:'finished'};
}));
function filter(items,options={}){
 const {action,startDate,endDate}=options;
 if(startDate&&endDate&&startDate>endDate)throw Error('开始日期不能晚于结束日期');
 return items.filter(r=>(!action||action==='all'||r.action===action)&&(!startDate||r.date>=startDate)&&(!endDate||r.date<=endDate));
}
function history(id,period='7',options={}){
 const selected=filter(reports.filter(r=>r.student_id===id),options);
 const window=period==='30'?selected.filter(r=>r.date>='2026-09-22'&&r.date<='2026-10-21'):selected.slice(-7);
 return {student_id:id,period,as_of:'2026-10-21',source:'MOCK / DEMO',points:window.map((r,i)=>({report_id:r.report_id,label:r.date.slice(5),date:r.date,action:r.action,action_name:r.action_name,reps:r.reps,target_reps:r.target_reps,score:r.score,rom:r.rom,speed:r.speed,stability:r.stability,symmetry:r.symmetry,completion:r.completion,knee_in:r.errors.filter(e=>e.code==='KNEE_IN').reduce((s,e)=>s+e.count,0),errors:r.errors})),strength:window.length?'已记录的动作完成度':'暂无训练数据',focus:window.some(r=>r.errors.length)?'结合记录中的动作事件复核':'继续积累同项目记录'};
}
function classrooms(options={}){return filter(sessions,options).map(s=>{const members=reports.filter(r=>r.lesson_id===s.id);return {...s,student_count:members.length,average_score:Math.round(members.reduce((n,r)=>n+r.score,0)/members.length)};}).reverse();}
function report(id){const value=reports.find(r=>r.report_id===id);if(!value)throw Error('未找到训练报告');return value;}
function classReports(id){if(!sessions.some(s=>s.id===id))throw Error('未找到历史课堂');return reports.filter(r=>r.lesson_id===id);}
module.exports={history,classrooms,report,classReports};
