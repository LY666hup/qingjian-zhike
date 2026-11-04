// Deterministic summaries. No external LLM call; no inferred diagnoses.
function classSummary(students){
 const buckets=[{label:'85–100分',count:0},{label:'70–84分',count:0},{label:'低于70分',count:0}];const issues={};
 for(const s of students){buckets[s.score>=85?0:s.score>=70?1:2].count++;const seen=new Set();for(const e of s.errors||[]){const v=issues[e.code]||(issues[e.code]={code:e.code,name:e.name,people:0,count:0});if(!seen.has(e.code)){v.people++;seen.add(e.code);}v.count+=e.count;}}
 return {total:students.length,average:students.length?Math.round(students.reduce((n,s)=>n+s.score,0)/students.length):null,finished:students.filter(s=>s.reps>=s.target_reps).length,buckets:buckets.map(b=>({...b,percent:students.length?Math.round(b.count/students.length*100):0})),issues:Object.values(issues).sort((a,b)=>b.people-a.people||b.count-a.count)};
}
function personalSummary(history){const recent=history.points.slice(-5);const count=recent.length;return {count,average:count?Math.round(recent.reduce((n,p)=>n+p.score,0)/count):null,kneeSessions:recent.filter(p=>p.knee_in>0).length,kneeEvents:recent.reduce((n,p)=>n+(p.knee_in||0),0),change:count>1?recent[count-1].score-recent[0].score:null};}
module.exports={classSummary,personalSummary};
