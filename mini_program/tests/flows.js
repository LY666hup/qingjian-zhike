const assert=require('node:assert/strict');
const api=require('../services/api'),session=require('../services/session'),config=require('../config');
const storage={};let navigation='';
global.wx={stopPullDownRefresh(){},showToast(){},switchTab(){},navigateTo(o){navigation=o.url;},getStorageSync(k){return storage[k];},setStorageSync(k,v){storage[k]=JSON.parse(JSON.stringify(v));}};
async function page(name,options={}){let p;global.Page=d=>p=d;const file='../pages/'+name+'/index';delete require.cache[require.resolve(file)];require(file);p.data=JSON.parse(JSON.stringify(p.data));p.setData=d=>Object.assign(p.data,d);if(p.onLoad)p.onLoad(options);await p.onShow();await new Promise(r=>setImmediate(r));return p;}
(async()=>{
 session.select('teacher');
 const all=await api.getClassroomHistory(),filtered=await api.getClassroomHistory({action:'jumping_jack'});assert.equal(all.length,8);assert.equal(filtered.length,3);
 const one=await api.getClassroomHistory({startDate:'2026-10-18',endDate:'2026-10-18'});assert.equal(one.length,1);
 assert.equal((await api.getClassroomHistory({startDate:'2099-01-01'})).length,0);
 await assert.rejects(api.getClassroomHistory({startDate:'2026-10-20',endDate:'2026-10-01'}),/开始日期/);
 for(const c of all){const reports=await api.getClassroomReport(c.id);assert.equal(c.student_count,reports.length);assert.equal(c.average_score,Math.round(reports.reduce((n,r)=>n+r.score,0)/reports.length));}
 const history=await page('history');await history.open({currentTarget:{dataset:{id:all[0].id}}});assert.equal(history.data.selectedReports.length,12);
 const first=history.data.selectedReports[0];history.report({currentTarget:{dataset:{id:first.report_id,student:first.student_id}}});assert(navigation.includes(first.report_id));
 const report=await page('student',{id:first.student_id,report:first.report_id});assert.equal(report.data.isReport,true);assert.equal(report.data.student.score,first.score);assert(!report.data.error);
 const advice=await api.getAdvice();const saved=await api.saveAdviceWorkflow(advice.advice_id,{state:'reviewed',note:'下一组先作示范'});assert.equal(saved.storage,'local');
 assert.equal((await api.getAdvice()).workflow.note,'下一组先作示范');
 assert.equal((await api.getAdvice('动作节奏')).workflow.state,'draft');
 await assert.rejects(api.saveAdviceWorkflow(advice.advice_id,{feedback:'helpful'}),/审核状态/);
 await assert.rejects(api.saveAdviceWorkflow(advice.advice_id,{state:'reviewed',note:'字'.repeat(201)}),/200/);
 const realSave=wx.setStorageSync;wx.setStorageSync=()=>{throw Error('quota');};await assert.rejects(api.saveAdviceWorkflow(advice.advice_id,{state:'needs_revision'}),/本机保存失败/);wx.setStorageSync=realSave;
 assert.equal((await api.getAdvice()).workflow.state,'reviewed');
 session.select('student');
 const personal=await api.getMyHistory('30',{action:'squat'});assert.equal(personal.points.length,5);assert(personal.points.every(p=>p.action==='squat'));
 const last=personal.points[personal.points.length-1];assert.equal((await api.getTrainingReport(last.report_id,true)).score,last.score);
 await assert.rejects(api.getTrainingReport(first.report_id.replace('20260001','20260002'),true),/自己的/);
 await assert.rejects(api.getClassroomReport(all[0].id),/授权教师/);
 const studentAdvice=await api.getAdvice();assert.equal(studentAdvice.workflow.state,'draft');
 await api.saveAdviceWorkflow(studentAdvice.advice_id,{feedback:'question',note:'想确认动作节奏'});
 assert.equal((await api.getAdvice()).workflow.feedback,'question');
 await assert.rejects(api.saveAdviceWorkflow(studentAdvice.advice_id,{state:'reviewed'}),/反馈选项/);
 const h=await page('history');const record=h.data.personalPoints[0];h.report({currentTarget:{dataset:{id:record.report_id}}});assert(navigation.includes('self=1'));
 const r=await page('student',{self:'1',report:record.report_id});assert.equal(r.data.student.student_id,'20260001');
 // An older network result must not overwrite the user's newer filter result.
 const {loadLatest,cancelLoad}=require('../utils/page');const p={data:{},setData(d){Object.assign(this.data,d);}};let old;
 const slow=loadLatest(p,()=>new Promise(resolve=>old=resolve));await Promise.resolve();await loadLatest(p,async()=>({value:'new'}));old({value:'old'});await slow;assert.equal(p.data.value,'new');
 let late;const inactive=loadLatest(p,()=>new Promise(resolve=>late=resolve));await Promise.resolve();cancelLoad(p);late({value:'inactive'});await inactive;assert.equal(p.data.value,'new');
 config.mode='live';config.baseURL='https://example.invalid';let call;
 wx.request=o=>{call=o;o.success({statusCode:200,data:{code:0,data:o.url.endsWith('/me')?{role:'student'}:{saved:true}}});};
 await api.getTrainingReport('a/b',true);assert(call.url.endsWith('/me/reports/a%2Fb'));
 await api.saveAdviceWorkflow('server-id',{feedback:'question'});assert(call.url.endsWith('/advice/server-id/feedback'));assert.equal(call.method,'POST');
 config.mode='mock';config.baseURL='';session.select('teacher');
 console.log('PASS: shared historical/report numbers, project/date filters, exact report navigation, role-restricted report access, local review/feedback persistence and storage failure, version-specific drafts, latest-request wins, and live self/report/feedback paths.');
})().catch(e=>{console.error(e);process.exitCode=1;});
