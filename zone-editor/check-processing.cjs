const {chromium}=require(process.env.PLAYWRIGHT_PACKAGE||'playwright');
const path=require('path');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
 const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async r=>{if(r.status()>=400&&r.url().includes('/api/')){try{console.log('API error',await r.text());}catch{}}});
 await page.goto(process.env.ZONE_APP_URL||'http://127.0.0.1:8766');
 await page.waitForFunction(()=>!!appInfo);
 await page.locator('#videoFile').setInputFiles(path.join(__dirname,'../.runtime/zone-app-smoke/input.mp4'));
 await page.waitForFunction(()=>ready);
 console.log(await page.evaluate(()=>JSON.stringify({browser:[video.videoWidth,video.videoHeight],calibration:[canvas.width,canvas.height]})));
 await page.locator('#outputFolder').fill('.runtime/zone-app-smoke/results');
 await page.locator('#name').fill('dairy');await page.locator('#new').click();
 async function click(x,y){await page.locator('#canvas').scrollIntoViewIfNeeded();const b=await page.locator('#canvas').boundingBox();await page.mouse.click(b.x+b.width*x,b.y+b.height*y);}
 await click(.05,.05);await click(.95,.05);await click(.95,.95);await click(.05,.95);await page.locator('#finish').click();
 await page.locator('#kind').selectOption('entry_exit_lines');await page.locator('#name').fill('door');await page.locator('#new').click();await click(.1,.7);await click(.9,.7);await click(.5,.4);
 await page.locator('#processVideo').click();
 console.log(await page.evaluate(()=>JSON.stringify({shapes:shapes.length,draft:!!draft,status:document.getElementById('processingStatus').textContent})));
 await page.waitForFunction(()=>!busy&&document.getElementById('processingStatus').textContent!=='Saving video and layout…',{},{timeout:180000});
 const resultMessage=await page.locator('#processingStatus').textContent();if(resultMessage!=='Video and reports saved.')throw Error(resultMessage);
 const state=await page.evaluate(async()=>({report:dashboardReport,run:currentRun,status:await api(`/api/jobs/${currentRun}`)}));
 if(state.report.frames_processed<20||state.report.observed_ids<1||!state.report.zone_totals.length)throw Error('Real inference did not yield expected observed people and zone visits');
 await page.locator('#resultVideo').evaluate(v=>v.play());
 await page.waitForFunction(()=>document.getElementById('resultVideo').currentTime>.2);
 const range=await page.evaluate(async()=>{const r=await fetch(`/api/jobs/${currentRun}/files/annotated.mp4`,{headers:{Range:'bytes=0-100'}});return {status:r.status,bytes:(await r.arrayBuffer()).byteLength};});
 if(range.status!==206||range.bytes!==101)throw Error('Output player range support failed');
 const dl=page.waitForEvent('download');await page.getByText('Zone totals CSV',{exact:true}).click();await dl;
 const unauthorized=await page.evaluate(async()=>{const r=await fetch('/api/jobs?filename=x.mp4',{method:'POST',body:'x'});return r.status;});if(unauthorized!==403)throw Error('Untrusted mutation accepted');
 const traversal=await page.evaluate(async()=>{const r=await fetch('/api/jobs?filename=x.mp4&output=../../outside',{method:'POST',headers:{'X-App-Token':appInfo.token},body:'x'});return r.status;});if(traversal!==400)throw Error('Outside workspace path accepted');
 await page.screenshot({path:path.join(__dirname,'processing-preview.png'),fullPage:true});
 const run=state.run;await page.reload();await page.waitForFunction(()=>!!appInfo);await page.locator('#previousRuns').selectOption(run);await page.waitForFunction(()=>document.getElementById('processingStatus').textContent==='Video and reports saved.');
 if(errors.length)throw Error(errors.join('\n'));
 console.log(JSON.stringify({passed:true,frames:state.report.frames_processed,tracking_ids:state.report.observed_ids,entries:state.report.entries,exits:state.report.exits,zone_rows:state.report.zone_totals.length,output:state.status.output_folder,range,checks:'upload, inference, saved video, browser playback, CSV, dashboard, previous run, token and output path checks'}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
