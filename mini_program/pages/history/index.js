const api=require('../../services/api'),{loadLatest,cancelLoad}=require('../../utils/page');
Page({
 data:{records:[],personalPoints:[],period:'7',isStudent:false,loading:true,error:'',selected:null,selectedReports:[],detailError:'',detailLoading:false,actionIndex:0,actionNames:['全部项目','深蹲','开合跳'],startDate:'',endDate:''},
 onShow(){return this.refresh();},onHide(){cancelLoad(this);this.detailVersion=(this.detailVersion||0)+1;},onPullDownRefresh(){return this.refresh();},
 period(e){this.setData({period:e.currentTarget.dataset.value});return this.refresh();},
 action(e){this.setData({actionIndex:Number(e.detail.value)});return this.refresh();},
 start(e){this.setData({startDate:e.detail.value});return this.refresh();},end(e){this.setData({endDate:e.detail.value});return this.refresh();},
 clearFilters(){this.setData({startDate:'',endDate:'',actionIndex:0});return this.refresh();},
 refresh(){const {period,actionIndex,startDate,endDate}=this.data;this.close();this.setData({records:[],personalPoints:[]});return loadLatest(this,async()=>{
  if(startDate&&endDate&&startDate>endDate)throw Error('开始日期不能晚于结束日期');
  const filters={action:['all','squat','jumping_jack'][actionIndex],startDate,endDate};const viewer=await api.getViewer();const isStudent=viewer.role==='student';
  if(isStudent){const history=await api.getMyHistory(period,filters);return {isStudent,personalPoints:[...history.points].reverse(),asOf:history.as_of||''};}
  return {isStudent,records:await api.getClassroomHistory(filters)};
 });},
 async open(e){const id=e.currentTarget.dataset.id;const selected=this.data.records.find(x=>x.id===id);if(!selected)return;const version=this.detailVersion=(this.detailVersion||0)+1;this.setData({selected,selectedReports:[],detailLoading:true,detailError:''});try{const selectedReports=await api.getClassroomReport(id);if(version===this.detailVersion)this.setData({selectedReports},()=>{if(wx.pageScrollTo)wx.pageScrollTo({selector:"#classroom-review",duration:250});});}catch(e){if(version===this.detailVersion)this.setData({detailError:e.message});}finally{if(version===this.detailVersion)this.setData({detailLoading:false});}},
 close(){this.detailVersion=(this.detailVersion||0)+1;this.setData({selected:null,selectedReports:[],detailError:'',detailLoading:false});},
 report(e){const {id,student}=e.currentTarget.dataset;wx.navigateTo({url:'/pages/student/index?'+(this.data.isStudent?'self=1':'id='+encodeURIComponent(student))+'&report='+encodeURIComponent(id)});}
});
