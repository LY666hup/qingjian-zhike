const pad=n=>String(n).padStart(2,'0');
const iso=d=>d.toISOString().slice(0,10);
function today(){const d=new Date();return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate());}
function monthDate(month){if(!/^\d{4}-\d{2}$/.test(month))throw Error('月份格式无效');const d=new Date(month+'-01T00:00:00Z');if(!Number.isFinite(d.getTime())||iso(d).slice(0,7)!==month)throw Error('月份无效');return d;}
function shiftMonth(month,delta){const d=monthDate(month);d.setUTCMonth(d.getUTCMonth()+delta);return iso(d).slice(0,7);}
function grid(month,selected,lessons=[],todayDate=today()){
 const first=monthDate(month),start=new Date(first);start.setUTCDate(1-(first.getUTCDay()+6)%7);const byDate={};for(const lesson of lessons)(byDate[lesson.date]||(byDate[lesson.date]=[])).push(lesson);
 return Array.from({length:42},(_,i)=>{const d=new Date(start);d.setUTCDate(start.getUTCDate()+i);const date=iso(d),rows=byDate[date]||[];return {date,day:d.getUTCDate(),inMonth:date.slice(0,7)===month,isToday:date===todayDate,selected:date===selected,count:rows.length,label:rows[0]?rows[0].shortName||rows[0].title.slice(0,2):''};});
}
function demoLessons(month){const d=monthDate(month),rows=[];while(iso(d).slice(0,7)===month){const day=d.getUTCDay();if([1,3,5].includes(day)){const date=iso(d),action=day===3?'pushup':day===5?'plank':'squat',name={squat:'深蹲',pushup:'俯卧撑',plank:'平板支撑'}[action];rows.push({id:'demo_'+date,date,time:'14:00–14:45',title:name+'基础与动作控制',shortName:name.slice(0,2),action,class_name:'高一（3）班',location:'示例教学场地',source:'演示安排 · 非学校发布',focus:'动作示范、分组练习与教师反馈',tutorial:action});}d.setUTCDate(d.getUTCDate()+1);}return rows;}
module.exports={today,grid,shiftMonth,demoLessons};
