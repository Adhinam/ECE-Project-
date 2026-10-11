// Read-only browser check of an existing processed run; does not start inference.
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
(async () => {
 const id=process.argv[2];
 if(!/^[a-f0-9]{32}$/.test(id||''))throw Error('Supply a saved run ID');
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try {
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:8766/?run=${id}`);
  await page.waitForFunction(()=>document.getElementById('processingStatus').textContent==='Video and reports saved.');
  const info=await page.evaluate(()=>({id:currentRun,frames:dashboardReport.frames_processed,entries:dashboardReport.entries,exits:dashboardReport.exits}));
  if(info.id!==id||info.frames<1)throw Error('Saved run was not loaded');
  await page.locator('#resultVideo').evaluate(async video=>{await video.play();});
  await page.waitForFunction(()=>document.getElementById('resultVideo').currentTime>.2);
  await page.locator('#resultVideo').evaluate(video=>{video.pause();video.currentTime=Math.min(35.2,video.duration/2);});
  await page.waitForFunction(()=>!document.getElementById('resultVideo').seeking);
  if(errors.length)throw Error(errors.join('\n'));
  console.log(JSON.stringify({passed:true,...info,checks:'saved-run link, dashboard, video playback and seeking'}));
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
