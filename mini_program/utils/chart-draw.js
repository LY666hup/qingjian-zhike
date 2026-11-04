// Shared pure Canvas2D drawing for the native component and local visual checks.
const colors=['#2b9b70','#97cbb1','#e1b46b','#8ea9c4','#bdabc9'];
const finite=v=>typeof v==='number'&&Number.isFinite(v);
function empty(ctx,w,h,label='暂无可用数据'){ctx.fillStyle='#90a297';ctx.font='13px sans-serif';ctx.textAlign='center';ctx.fillText(label,w/2,h/2);}
function polygon(ctx,points){ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();}
function radar(ctx,w,h,model){
 const rows=model.rows||[];if(rows.length<3||rows.some(r=>!finite(r.value)))return empty(ctx,w,h,'暂无完整维度数据');
 const cx=w/2,cy=h/2+4,r=Math.min(w*.30,h*.30),n=rows.length;
 const point=(i,scale=1)=>({x:cx+Math.cos(-Math.PI/2+2*Math.PI*i/n)*r*scale,y:cy+Math.sin(-Math.PI/2+2*Math.PI*i/n)*r*scale});
 for(let level=1;level<=4;level++){polygon(ctx,rows.map((_,i)=>point(i,level/4)));ctx.strokeStyle='#dfeae3';ctx.lineWidth=1;ctx.stroke();}
 rows.forEach((row,i)=>{const p=point(i),a=point(i,1.35);ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(p.x,p.y);ctx.strokeStyle='#e6eee8';ctx.stroke();ctx.fillStyle='#6b8474';ctx.font='11px sans-serif';ctx.textAlign='center';ctx.fillText(row.label,a.x,a.y);ctx.fillStyle='#287b54';ctx.font='bold 12px sans-serif';ctx.fillText(String(Math.round(row.value)),a.x,a.y+15);});
 polygon(ctx,rows.map((row,i)=>point(i,Math.max(0,Math.min(100,row.value))/100)));ctx.fillStyle='rgba(43,155,112,.20)';ctx.fill();ctx.strokeStyle='#2b9b70';ctx.lineWidth=2;ctx.stroke();
 rows.forEach((row,i)=>{const p=point(i,Math.max(0,Math.min(100,row.value))/100);ctx.beginPath();ctx.arc(p.x,p.y,3,0,Math.PI*2);ctx.fillStyle='#2b9b70';ctx.fill();});
}
function donut(ctx,w,h,model){
 const segments=(model.segments||[]).filter(s=>finite(s.value)&&s.value>0),total=segments.reduce((n,s)=>n+s.value,0);
 if(!total)return empty(ctx,w,h);
 const cx=w/2,cy=h/2,r=Math.min(w,h)*.34,line=Math.max(14,r*.23);let start=-Math.PI/2;
 segments.forEach((s,i)=>{const end=start+Math.PI*2*s.value/total;ctx.beginPath();ctx.arc(cx,cy,r,start,end);ctx.strokeStyle=s.color||colors[i%colors.length];ctx.lineWidth=line;ctx.stroke();start=end;});
 ctx.textAlign='center';ctx.fillStyle='#1b4934';ctx.font='bold '+Math.min(29,r*.55)+'px sans-serif';ctx.fillText(String(model.center||total),cx,cy+3);ctx.fillStyle='#83978a';ctx.font='11px sans-serif';ctx.fillText(model.caption||'训练记录',cx,cy+24);
}
function combo(ctx,w,h,model){
 const points=model.points||[];if(!points.length)return empty(ctx,w,h);
 const box={left:32,right:32,top:25,bottom:34},cw=w-box.left-box.right,ch=h-box.top-box.bottom;
 const max=Math.max(1,...points.filter(p=>finite(p.reps)).map(p=>p.reps)),step=cw/points.length;
 ctx.font='10px sans-serif';ctx.textBaseline='middle';
 for(let i=0;i<=4;i++){const y=box.top+ch*i/4;ctx.beginPath();ctx.moveTo(box.left,y);ctx.lineTo(w-box.right,y);ctx.strokeStyle='#e6eee8';ctx.lineWidth=1;ctx.stroke();ctx.fillStyle='#90a397';ctx.textAlign='right';ctx.fillText(String(Math.round(max*(4-i)/4)),box.left-7,y);ctx.textAlign='left';ctx.fillText(String(100-i*25),w-box.right+7,y);}
 ctx.fillStyle='#72917e';ctx.textAlign='left';ctx.fillText('次数',2,9);ctx.textAlign='right';ctx.fillText('评分',w-2,9);
 points.forEach((p,i)=>{const x=box.left+step*(i+.5);if(finite(p.reps)){const bar=Math.max(0,p.reps)/max*ch;ctx.fillStyle='#b0ddc5';ctx.fillRect(x-step*.24,box.top+ch-bar,step*.48,bar);}ctx.fillStyle='#869c8e';ctx.textAlign='center';ctx.font='9px sans-serif';ctx.fillText(p.label||'',x,h-15);});
 ctx.strokeStyle='#246b4c';ctx.lineWidth=2.5;ctx.beginPath();let started=false;
 points.forEach((p,i)=>{if(!finite(p.score)){started=false;return;}const x=box.left+step*(i+.5),y=box.top+ch*(1-Math.max(0,Math.min(100,p.score))/100);if(started)ctx.lineTo(x,y);else ctx.moveTo(x,y);started=true;});ctx.stroke();
 points.forEach((p,i)=>{if(!finite(p.score))return;const x=box.left+step*(i+.5),y=box.top+ch*(1-Math.max(0,Math.min(100,p.score))/100);ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fillStyle='#246b4c';ctx.fill();});ctx.textBaseline='alphabetic';
}
function draw(ctx,width,height,model={}){ctx.clearRect(0,0,width,height);ctx.save();try{const fn={radar,donut,combo}[model.type];if(fn)fn(ctx,width,height,model);else empty(ctx,width,height);}finally{ctx.restore();}}
module.exports={draw};
