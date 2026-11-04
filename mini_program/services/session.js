// Demo identity only. This never grants server permissions.
const config=require('../config');
let selected={role:'teacher',student_id:'20260001'};
function get(){return {...selected};}
function select(role){if(config.mode==='live')throw Error('真实身份须通过后端登录确认');if(!['teacher','student'].includes(role))throw Error('未知身份');selected={role,student_id:'20260001'};}
module.exports={get,select};
