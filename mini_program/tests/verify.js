const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const api = require('../services/api');
const engine = require('../mock/engine');
const config = require('../config');
config.mode='mock';config.baseURL='';
let timer, cleared = 0, created = 0;
global.setInterval = fn => { timer = fn; created++; return 123; };
global.clearInterval = id => { assert.equal(id, 123); cleared++; };
global.wx = { stopPullDownRefresh(){}, showToast(){}, navigateTo(){}, switchTab(){} };

async function main(){
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json')));
  for(const page of app.pages){
    for(const ext of ['js','json','wxml','wxss']) assert(fs.existsSync(path.join(root,page+'.'+ext)),page+'.'+ext);
    JSON.parse(fs.readFileSync(path.join(root,page+'.json')));
  }
  let c = await api.getClassroomStatus(), s = await api.getStudentList();
  assert.equal(c.student_count,s.length);
  assert.equal(c.attention_count,c.attention_queue.length);
  assert.equal(c.average_score,Math.round(s.reduce((sum,x)=>sum+x.score,0)/s.length));
  s[0].reps=999; assert.notEqual((await api.getStudentList())[0].reps,999,'data is cloned');
  await assert.rejects(api.getStudentDetail('unknown'));
  assert.equal((await api.getStudentHistory('20260001')).points.length,7);
  assert.equal((await api.getStudentHistory('20260001','30')).points.length,8);
  let page;
  global.Page = definition => { page=definition; };
  require('../pages/classroom/index');
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = update => Object.assign(page.data,update);
  await page.onShow();
  // onShow returns void; let refresh and subscription finish.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(page.data.visible.length,12);
  page.filter({currentTarget:{dataset:{id:'severe'}}}); assert.equal(page.data.visible.length,1);
  page.search({detail:{value:'不存在'}}); assert.equal(page.data.visible.length,0);
  await api.controlDemo('start');
  const beforeTicks=engine.snapshot();
  timer();timer();timer();
  assert.deepEqual(engine.snapshot(),beforeTicks,'offline subscription must not generate exercise counts');
  engine.advance();engine.advance();engine.advance();timer();
  assert.equal(page.data.classroom.attention_count,4);
  assert.equal(page.data.classroom.attention_queue[0].student_id,'20260001');
  await api.controlDemo('pause'); const paused=engine.snapshot();timer();assert.deepEqual(engine.snapshot(),paused);
  page.onHide();assert.equal(cleared,1);
  page.active=true;
  const before=created;
  await Promise.all([page.refresh(),page.refresh()]);
  assert.equal(created-before,1,'overlapping refreshes create only one subscription');
  page.onHide();
  await api.controlDemo('start');for(let i=0;i<50;i++)engine.advance();
  c=await api.getClassroomStatus();assert.equal(c.finished_count,12);assert.equal(c.active_count,0);assert.equal(c.stage,'finished');
  await api.controlDemo('reset');assert.equal((await api.getClassroomStatus()).attention_count,3);
  // Real mode must reject missing configuration, never silently serve demo data.
  config.mode='live';await assert.rejects(api.getClassroomStatus(),/尚未配置/);
  config.baseURL='https://example.invalid';
  wx.request=o=>o.success({statusCode:200,data:{code:0,data:{class_id:'real'}}});
  assert.equal((await api.getClassroomStatus()).class_id,'real');
  wx.request=o=>o.success({statusCode:500,data:{}});await assert.rejects(api.getClassroomStatus());
  config.mode='mock';config.baseURL='';
  console.log('PASS: page registration, data isolation, roster counts, not-found, history ranges, filtering, demo events, pause/reset/completion, stream cleanup, HTTP success/failure and no fallback.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
