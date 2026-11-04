const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),app=require('../app.json'),config=require('../project.config.json');
const ignores=new Set(config.packOptions.ignore.filter(x=>x.type==='folder').map(x=>x.value));
function walk(dir,prefix=''){return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(path.join(dir,e.name),prefix+e.name+'/'):[prefix+e.name]);}
const files=walk(root).filter(f=>!ignores.has(f.split('/')[0]));
const packages=[{name:'main',files:files.filter(f=>!app.subPackages.some(p=>f.startsWith(p.root+'/')))},...app.subPackages.map(p=>({name:p.root,files:files.filter(f=>f.startsWith(p.root+'/'))}))];
for(const p of packages){p.bytes=p.files.reduce((n,f)=>n+fs.statSync(path.join(root,f)).size,0);assert(p.bytes<2*1024*1024,p.name+' exceeds conservative local budget');}
for(const page of [...app.pages,...app.subPackages.flatMap(p=>p.pages.map(f=>p.root+'/'+f))])for(const ext of ['js','json','wxml','wxss'])assert(fs.existsSync(path.join(root,page+'.'+ext)),page+'.'+ext);
for(const f of files.filter(f=>f.endsWith('.js'))){const source=fs.readFileSync(path.join(root,f),'utf8');for(const match of source.matchAll(/require\(['"](\.[^'"]+)['"]\)/g)){const target=path.resolve(path.dirname(path.join(root,f)),match[1]);assert(fs.existsSync(target)||fs.existsSync(target+'.js')||fs.existsSync(target+'.json'),f+' -> '+match[1]);}}
let definition;global.Component=x=>definition=x;require('../components/training-chart/index');let scheduled=0;const mock={scheduleDraw(){scheduled++;}};definition.pageLifetimes.show.call(mock);definition.pageLifetimes.resize.call(mock);definition.properties.height.observer.call(mock);assert.equal(scheduled,3);
console.log(JSON.stringify({status:'PASS',scope:'LOCAL_SOURCE_AND_SIZE_CHECK_NOT_WECHAT_COMPILE',packages:packages.map(p=>({name:p.name,bytes:p.bytes})),tab_pages:app.tabBar.list.map(p=>p.text),chart_return_resize:'PASS'}));
