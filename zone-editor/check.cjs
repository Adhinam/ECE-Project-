// Browser smoke check using the real sample video and native-resolution export.
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const path = require('path');
const fs = require('fs');
(async () => {
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try {
 const page=await browser.newPage({viewport:{width:1280,height:900}}), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(require('url').pathToFileURL(path.join(__dirname,'index.html')).href);
 await page.locator('#videoFile').setInputFiles(path.join(__dirname,'../sample_video/Resized video.mp4'));
 await page.waitForFunction(()=>ready);
 const size=await page.evaluate(()=>[canvas.width,canvas.height]);
 async function click(x,y){const b=await page.locator('#canvas').boundingBox();await page.mouse.click(b.x+b.width*x,b.y+b.height*y);}
 await page.locator('#name').fill('dairy');await page.locator('#new').click();
 await click(.15,.2);await click(.45,.2);await click(.45,.6);await click(.15,.6);await page.locator('#finish').click();
 await page.locator('#kind').selectOption('entry_exit_lines');await page.locator('#name').fill('main-door');await page.locator('#new').click();
 await click(.55,.6);await click(.9,.6);await click(.7,.3);
 const data=await page.evaluate(()=>layout());
 if(data.zones.length!==1||data.entry_exit_lines.length!==1||data.frame_width!==size[0]||data.entry_exit_lines[0].direction_in!=='bottom')throw Error('Export or entry direction mismatch');
 await page.evaluate(()=>{window.showSaveFilePicker=undefined;});
 const downloadPromise=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadPromise;
 const exported=JSON.parse(fs.readFileSync(await download.path(),'utf8'));if(exported.zones[0].id!=='dairy')throw Error('Download mismatch');
 await page.locator('#flip').click();if(await page.evaluate(()=>shapes[1].direction_in)!=='top')throw Error('Flip failed');await page.locator('#undo').click();
 await page.locator('#items .item').nth(1).locator('button').first().click();
 const beforeInset=await page.evaluate(()=>shapes[1].p1[1]);
 await page.locator('#insetDoor').click();
 if(await page.evaluate(()=>shapes[1].p1[1])>=beforeInset)throw Error('Inward doorway move failed');
 await page.locator('#undo').click();
 if(await page.evaluate(()=>shapes[1].p1[1])!==beforeInset)throw Error('Doorway move undo failed');
 // Moving a selected polygon point uses the displayed-to-source transform.
 await page.locator('#items .item').first().locator('button').first().click();
 await page.locator('#canvas').scrollIntoViewIfNeeded();
 const b=await page.locator('#canvas').boundingBox();await page.mouse.move(b.x+b.width*.15,b.y+b.height*.2);await page.mouse.down();await page.mouse.move(b.x+b.width*.1,b.y+b.height*.15);await page.mouse.up();
 if(await page.evaluate(()=>shapes[0].polygon[0][0])!==Math.round(size[0]*.1))throw Error('Vertex drag mismatch');
 await page.locator('#undo').click();
 const validation=await page.evaluate(()=>{try{validateShape({kind:'zones',id:'bad',polygon:[[0,0],[100,100],[0,100],[100,0]]},canvas.width,canvas.height);return false;}catch{return true;}});if(!validation)throw Error('Invalid polygon accepted');
 page.on('dialog',d=>d.accept());await page.locator('#importFile').setInputFiles({name:'layout.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
 await page.waitForFunction(()=>document.getElementById('status').textContent.startsWith('Layout loaded'));
 const bad={...data,frame_width:data.frame_width+1};await page.locator('#importFile').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});await page.waitForFunction(()=>document.getElementById('status').textContent.includes('does not match'));
 await page.evaluate(()=>message('Ready. Test shapes shown here are examples, not calibrated store boundaries.'));
 await page.screenshot({path:path.join(__dirname,'preview.png'),fullPage:true});
 fs.writeFileSync(path.join(__dirname,'smoke-layout.json'),JSON.stringify(data,null,2));
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS: real video, polygon, doorway, direction flip, vertex drag, undo, download, import, invalid geometry and dimension rejection. Source: '+size.join(' x '));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
