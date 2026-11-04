const config = require('../config');
function networkError(result){
  const detail=result&&result.errMsg?String(result.errMsg):'未返回具体原因';
  if(/domain|not in.*list|合法域名/i.test(detail))return new Error('微信域名校验拦截：手机右上角···→开发调试→打开调试，重新进入后重试。'+detail);
  if(/ssl|certificate|https/i.test(detail))return new Error('微信安全连接限制：当前为局域网HTTP联调，请使用预览版的开发调试；正式接入需HTTPS。'+detail);
  if(/timeout/i.test(detail))return new Error('连接后台超时：确认手机与电脑在同一网络，并用手机浏览器检查 '+config.baseURL+'/health 。'+detail);
  return new Error('未能连接训练后台：'+detail+'。当前地址 '+config.baseURL+'；局域网预览请在手机开启开发调试。');
}
function request(path, method='GET', data){
  if(!config.baseURL)return Promise.reject(new Error('尚未配置真实服务地址'));
  return new Promise((resolve,reject)=>wx.request({url:config.baseURL+path,method,data,header:config.mode==='test'?{'X-Test-Token':config.testToken,'X-Test-Role':require('./session').get().role}:{},timeout:10000,success:r=>{if(r.statusCode>=200&&r.statusCode<300&&r.data&&r.data.code===0)resolve(r.data.data);else reject(new Error((r.data&&r.data.message)||'服务请求失败，请稍后重试'));},fail:r=>reject(networkError(r))}));
}
module.exports={request};
