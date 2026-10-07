// Headless Chrome over the DevTools protocol, for the console's `screenshot` and the tests' layout checks (#76;
// before that tests/_chrome_layout.js, #52 review). Node 22+ has fetch and WebSocket built in; --dump-dom and
// --screenshot were not used because they hang on some macOS builds.
//
//   node headless_chrome.js <chrome> <url> <width> <expr> [png]
//
// Opens <url> at <width> px, waits 1.5 s, evaluates <expr> (a promise is awaited) and prints its JSON value. With
// [png], the page is then captured whole (its own height, 600..6000 px) to that file and {"png","width","height"}
// is printed instead. The window is set to the width BEFORE the page loads (about:blank first), so the page is
// never reloaded, and Chrome is killed outright at the end: the Open Loops page says goodbye to its server on
// unload (/api/bye), after which a server with no other tab quits, and a screenshot must never do that.
const {spawn}=require('child_process'),fs=require('fs'),os=require('os'),path=require('path');
const [chrome,url,width,expr,png]=process.argv.slice(2);
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ol-cdp-'));
const p=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--use-mock-keychain',`--user-data-dir=${dir}`,'--remote-debugging-port=0',`--window-size=${width},800`,'about:blank'],{stdio:'ignore'});
const done=(code,out)=>{if(out!==undefined)console.log(out);try{p.kill('SIGKILL')}catch(e){}setTimeout(()=>{try{fs.rmSync(dir,{recursive:true,force:true})}catch(e){}process.exit(code)},300)};
setTimeout(()=>done(3,JSON.stringify({error:'timed out'})),60000);
(async()=>{let port;for(let i=0;i<200&&!port;i++){try{port=fs.readFileSync(path.join(dir,'DevToolsActivePort'),'utf8').split('\n')[0]}catch(e){await new Promise(r=>setTimeout(r,100))}}
 if(!port)return done(2,JSON.stringify({error:'no DevToolsActivePort'}));
 const t=await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
 const ws=new WebSocket(t.webSocketDebuggerUrl);let id=0;const wait={};
 ws.onmessage=m=>{const d=JSON.parse(m.data);if(d.id&&wait[d.id]){wait[d.id](d);delete wait[d.id]}};
 const send=(method,params)=>new Promise(r=>{const i=++id;wait[i]=r;ws.send(JSON.stringify({id:i,method,params}))});
 await new Promise(r=>ws.onopen=r);
 const metrics=h=>send('Emulation.setDeviceMetricsOverride',{width:+width,height:h,deviceScaleFactor:1,mobile:false});
 await metrics(800);
 await send('Page.navigate',{url});await new Promise(r=>setTimeout(r,1500));
 const res=await send('Runtime.evaluate',{expression:expr,awaitPromise:true,returnByValue:true});
 const value=res.result&&res.result.result?res.result.result.value:res;
 if(!png)return done(0,JSON.stringify(value));
 const lm=await send('Page.getLayoutMetrics',{});
 const h=Math.max(600,Math.min(6000,Math.ceil(((lm.result||{}).cssContentSize||(lm.result||{}).contentSize||{}).height||800)));
 await metrics(h);await new Promise(r=>setTimeout(r,300));
 const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
 if(!shot.result||!shot.result.data)return done(1,JSON.stringify({error:'no screenshot data',detail:shot}));
 // the page must not say goodbye when Chrome goes (its pagehide handler checks `stopped`); the kill is the safety net
 try{await send('Runtime.evaluate',{expression:'typeof stopped!=="undefined"&&(stopped=true)'})}catch(e){}
 fs.writeFileSync(png,Buffer.from(shot.result.data,'base64'));
 done(0,JSON.stringify({png,width:+width,height:h,waited:value}))})().catch(e=>done(1,JSON.stringify({error:String(e)})));
