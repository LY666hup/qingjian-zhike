const visuals=require('../../utils/training-visuals');
const api = require('../../services/api');
const view = require('../../utils/view');
const {loadLatest,cancelLoad} = require('../../utils/page');
Page({
  data: {loading:true,error:'',isReport:false},
  onLoad(o) { this.id=o.id||''; this.self=o.self==='1';this.reportId=o.report||''; },
  onShow() { return this.refresh(); },
  onPullDownRefresh() { return this.refresh(); },
  onHide(){cancelLoad(this);},
  refresh() {this.setData({student:null});return loadLatest(this,async()=>{
    const s=await (this.reportId?api.getTrainingReport(this.reportId,this.self,this.id):this.self?api.getMyProfile():api.getStudentDetail(this.id));
    const history=await (this.self?api.getMyHistory('30',{action:s.action}):api.getStudentHistory(s.student_id,'30',{action:s.action}));
    const preceding=history.points.filter(p=>!this.reportId||p.date<s.date);
    const previous=preceding.length?preceding[preceding.length-1]:null;
    const delta=previous?s.score-previous.score:null;
    return {radarChart:visuals.radar([s]),completionChart:visuals.completion(s.reps,s.target_reps),student:view.student(s),metrics:view.metrics(s),isReport:!!this.reportId,self:this.self,actionName:s.action_name||({squat:'深蹲',jumping_jack:'开合跳'})[s.action]||'训练',date:s.date||'当前课堂快照',comparison:delta===null?'暂无同项目历史':(delta>=0?'+':'')+delta+' 分',previousDate:previous?previous.date:'',eventCount:(s.errors||[]).reduce((n,e)=>n+e.count,0)};
  }); },
  tutorial(){wx.navigateTo({url:'/pages/tutorials/index?action='+encodeURIComponent(this.data.student.action)});},
  advice(){wx.navigateTo({url:'/pages/advice/index'});},
  portrait() { wx.navigateTo({url:this.self?'/pages/portrait/index?self=1':'/pages/portrait/index?id='+encodeURIComponent(this.id)}); }
});
