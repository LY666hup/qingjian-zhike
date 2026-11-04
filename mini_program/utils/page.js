function load(page,task){page.setData({loading:true,error:''});return Promise.resolve().then(task).catch(e=>page.setData({error:e.message||'加载失败'})).finally(()=>{page.setData({loading:false});wx.stopPullDownRefresh();});}
// Data loaders return a payload; only the newest request may commit it.
function loadLatest(page,task){
 const version=page.loadVersion=(page.loadVersion||0)+1;
 page.setData({loading:true,error:''});
 return Promise.resolve().then(task).then(payload=>{if(page.loadVersion===version&&payload)page.setData(payload);})
 .catch(e=>{if(page.loadVersion===version)page.setData({error:e.message||'加载失败'});})
 .finally(()=>{if(page.loadVersion===version){page.setData({loading:false});wx.stopPullDownRefresh();}});
}
function cancelLoad(page){page.loadVersion=(page.loadVersion||0)+1;}
module.exports={load,loadLatest,cancelLoad};
