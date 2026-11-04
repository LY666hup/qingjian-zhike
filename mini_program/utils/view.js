const labels={normal:'正常',attention:'注意',severe:'重点关注'};
function student(s){return Object.assign({},s,{score:s.score===null?'—':s.score,statusLabel:labels[s.status]||'未知',progress:Math.min(100,Math.max(0,Math.round(s.reps/s.target_reps*100))),initial:s.name.slice(-1),errorText:s.errors.map(e=>e.name+' ×'+e.count).join(' · ')||'暂无错误事件'});}
function stage(c){return Object.assign({},c,{stageLabel:({ready:'待开始',running:'训练进行中',paused:'已暂停',finished:'本轮已完成'})[c.stage]||'未知状态',completionPercent:c.student_count>0?Math.min(100,Math.max(0,Math.round(c.finished_count/c.student_count*100))):0});}
function metrics(s){return [{label:'动作幅度 ROM',value:s.rom},{label:'速度控制',value:s.speed},{label:'稳定性',value:s.stability},{label:'左右对称性',value:s.symmetry},{label:'完成度',value:s.completion}];}
module.exports={student,stage,metrics};
