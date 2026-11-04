// MOCK / DEMO：事件与优先队列均由模拟服务生成，页面不进行算法判定。
const seed = require('./data');
const clone = x => JSON.parse(JSON.stringify(x));
let state, tick;
function reset(){state=clone(seed);tick=0;}
reset();
function snapshot(){return clone(state);}
function advance(){
  if(state.classroom.stage !== 'running') return snapshot();
  tick++;
  state.students.forEach((s,i)=>{if(tick%3===i%3 && s.reps<s.target_reps)s.reps++;s.phase=tick%2?'UP':'DOWN';});
  if(tick===3){const s=state.students[0];s.status='attention';s.errors=[{code:'KNEE_IN',name:'膝内扣',count:2}];state.classroom.attention_queue.unshift({student_id:s.student_id,name:s.name,reason:'膝内扣连续出现 ×2',status:'attention'});}
  const c=state.classroom;c.finished_count=state.students.filter(s=>s.reps===s.target_reps).length;c.active_count=c.student_count-c.finished_count;c.attention_count=c.attention_queue.length;
  if(c.finished_count===c.student_count)c.stage='finished';
  return snapshot();
}
function control(command){if(command==='reset')reset();else if(command==='start')state.classroom.stage='running';else if(command==='pause')state.classroom.stage='paused';return snapshot();}
module.exports={snapshot,advance,control};
