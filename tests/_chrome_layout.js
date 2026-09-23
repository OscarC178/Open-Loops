// Layout checks in a real browser, for tests (test_messages.py: toasts never cover a checklist row, #52 review).
// Headless Chrome driven over the DevTools protocol (Node 22+ has fetch and WebSocket built in); --dump-dom was
// not used because it hangs on some macOS builds. Prints the JSON value of <expr> after loading <url> at <width> px.
// usage: node cdp.js <chrome> <url> <width> <expr>  -> prints the expression's JSON value
const {spawn}=require('child_process'),fs=require('fs'),os=require('os'),path=require('path');
const [chrome,url,width,expr]=process.argv.slice(2);
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ol-cdp-'));
const p=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--use-mock-keychain',`--user-data-dir=${dir}`,'--remote-debugging-port=0',`--window-size=${width},800`,'about:blank'],{stdio:'ignore'});
const done=(code,out)=>{if(out!==undefined)console.log(out);try{p.kill('SIGKILL')}catch(e){}setTimeout(()=>{try{fs.rmSync(dir,{recursive:true,force:true})}catch(e){}process.exit(code)},300)};
setTimeout(()=>done(3,JSON.stringify({error:'timed out'})),60000);
(async()=>{let port;for(let i=0;i<200&&!port;i++){try{port=fs.readFileSync(path.join(dir,'DevToolsActivePort'),'utf8').split('\n')[0]}catch(e){await new Promise(r=>setTimeout(r,100))}}
 if(!port)return done(2,JSON.stringify({error:'no DevToolsActivePort'}));
 const t=await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`,{method:'PUT'})).json();
 const ws=new WebSocket(t.webSocketDebuggerUrl);let id=0;const wait={};
 ws.onmessage=m=>{const d=JSON.parse(m.data);if(d.id&&wait[d.id]){wait[d.id](d);delete wait[d.id]}};
 const send=(method,params)=>new Promise(r=>{const i=++id;wait[i]=r;ws.send(JSON.stringify({id:i,method,params}))});
 await new Promise(r=>ws.onopen=r);
 await send('Emulation.setDeviceMetricsOverride',{width:+width,height:800,deviceScaleFactor:1,mobile:false});
 await send('Page.reload',{});await new Promise(r=>setTimeout(r,1500));
 const res=await send('Runtime.evaluate',{expression:expr,awaitPromise:true,returnByValue:true});
 done(0,JSON.stringify(res.result&&res.result.result?res.result.result.value:res))})().catch(e=>done(1,JSON.stringify({error:String(e)})));
