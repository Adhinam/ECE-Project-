'use strict';
let appInfo=null, currentRun=null, pollTimer=null, dashboardReport=null, busy=false;
let reportComplete=false;
function rememberRun(identity){
    currentRun=identity;
    const url=new URL(location.href);url.searchParams.set('run',identity);window.history.replaceState(null,'',url);
    try{localStorage.setItem('zoneApp.lastRun',identity);}catch{}
}
function lastRun(){try{return localStorage.getItem('zoneApp.lastRun');}catch{return null;}}
function procMessage(text){$('processingStatus').textContent=text;}
async function api(url, options={}){
    const response=await fetch(url,{...options,headers:{'X-App-Token':appInfo?.token||'',...(options.headers||{})}});
    if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{detail=response.statusText;}throw Error(typeof detail==='string'?detail:JSON.stringify(detail));}
    return response.json();
}
function setBusy(value){busy=value;$('processVideo').disabled=value||!appInfo;$('previousRuns').disabled=value;}
function clock(seconds){if(seconds===null||seconds===undefined)return '—';const ticks=Math.round(Math.max(0,Number(seconds)||0)*10);return `${Math.floor(ticks/600)}:${((ticks%600)/10).toFixed(1).padStart(4,'0')}`;}
function duration(seconds){if(seconds===null||seconds===undefined)return '—';const s=Math.max(0,Number(seconds)||0);return s<60?`${s.toFixed(1)} sec`:`${Math.floor(s/60)} min ${(s%60).toFixed(1)} sec`;}
function visitStatus(status){return ({closed:'Left the zone',open:'Still in zone', 'in queue':'Still in queue area','end of video (partial)':'Video ended before departure'})[status]||status;}
function table(root,headers,rows){
    const container=$(root);container.replaceChildren();
    if(!rows.length){const p=document.createElement('p');p.textContent='No matching events recorded.';p.className='hint';container.append(p);return;}
    const element=document.createElement('table');element.style.cssText='width:100%;border-collapse:collapse;text-align:left';
    const head=document.createElement('tr');headers.forEach(h=>{const cell=document.createElement('th');cell.textContent=h;cell.style.cssText='padding:10px;border-bottom:1px solid #52617b';head.append(cell);});element.append(head);
    rows.slice(0,1000).forEach(row=>{const tr=document.createElement('tr');row.forEach(value=>{const td=document.createElement('td');td.textContent=value??'—';td.style.cssText='padding:10px;border-bottom:1px solid #344056';tr.append(td);});element.append(tr);});container.append(element);
    if(rows.length>1000){const p=document.createElement('p');p.textContent='Showing the first 1,000 matching rows. Download the CSV for all rows.';container.append(p);}
}
function showTables(){if(!dashboardReport)return;const id=$('filterId').value.trim(),zone=$('filterZone').value,queue=$('filterQueue').value;const match=r=>(!id||String(r.id)===id)&&(!zone||r.zone===zone);
    table('crossingTable',['Event','ID on video','Video time','Doorway'],dashboardReport.crossings.filter(r=>!id||String(r.id)===id).map(r=>[r.type.toUpperCase(),r.id,clock(r.seconds),r.doorway]));
    table('totalsTable',['ID on video','Zone','Total time in area'],dashboardReport.zone_totals.filter(match).map(r=>[r.id,r.zone,duration(r.dwell_seconds)]));
    table('visitsTable',['ID on video','Zone','Visit','Entered at','Left at','Time in area','Status'],dashboardReport.visits.filter(match).map(r=>[r.id,r.zone,r.visit,clock(r.entered_seconds),clock(r.exited_seconds),duration(r.dwell_seconds),visitStatus(r.status)]));
    table('queueTable',['Queue',reportComplete?'At video end / capacity':'Latest count / capacity','Highest count','Average sampled count','Visits','Tracking IDs','Combined time in area'],(dashboardReport.queues||[]).filter(r=>!queue||r.queue===queue).map(r=>[r.queue,`${r.current_depth} / ${r.capacity??'—'}`,r.peak_depth,r.average_depth,r.visits,r.unique_ids,duration(r.total_wait_seconds)]));
    table('queueVisitsTable',['ID on video','Queue','Visit','Joined at','Left at','Time in area','Status'],(dashboardReport.queue_visits||[]).filter(r=>(!id||String(r.id)===id)&&(!queue||r.queue===queue)).map(r=>[r.id,r.queue,r.visit,clock(r.joined_seconds),clock(r.left_seconds),duration(r.wait_seconds),visitStatus(r.status)]));
}
function showReport(report){
    dashboardReport=report;$('kpis').replaceChildren();
    $('qualityNotice').textContent=(report.calibration_warnings||[]).join(' ');
    const queues=report.queues||[],currentDepth=queues.reduce((sum,q)=>sum+q.current_depth,0),peakDepth=queues.reduce((maximum,q)=>Math.max(maximum,q.peak_depth),0);
    for(const [label,value] of [['Recorded entries',report.entries],['Recorded exits',report.exits],['Tracking IDs seen',report.observed_ids],['Video analysed',duration(report.video_seconds_processed)],['Billing queue areas',queues.length],[reportComplete?'In queues at video end':'Latest queue count',currentDepth],['Highest count in one queue',peakDepth]]){const card=document.createElement('div');card.className='metric';const n=document.createElement('strong');n.textContent=value;const l=document.createElement('span');l.textContent=label;card.append(n,l);$('kpis').append(card);}
    $('reportNote').textContent=[...(report.calibration_warnings||[]),report.notes,queues.length?'Queue counts are sampled about once per second. Combined time adds all visits, including overlapping visits.':('queues' in report?'No billing queue areas were saved for this run. Add a Billing queue polygon and process the video again.':'This older run has no queue analytics. Its existing results are preserved; process a new run to calculate queue reports.')].filter(Boolean).join(' ');
    const selectedZone=$('filterZone').value, zoneNames=[...new Set(report.zone_totals.map(r=>r.zone))].sort();$('filterZone').replaceChildren(new Option('All zones',''),...zoneNames.map(z=>new Option(z,z)));$('filterZone').value=zoneNames.includes(selectedZone)?selectedZone:'';const selectedQueue=$('filterQueue').value,queueNames=queues.map(q=>q.queue);$('filterQueue').replaceChildren(new Option('All queues',''),...queueNames.map(q=>new Option(q,q)));$('filterQueue').value=queueNames.includes(selectedQueue)?selectedQueue:'';showTables();
}
$('filterId').oninput=showTables;$('filterZone').onchange=showTables;$('filterQueue').onchange=showTables;
function clearResults(){dashboardReport=null;for(const id of ['kpis','crossingTable','totalsTable','visitsTable','queueTable','queueVisitsTable','resultDownloads'])$(id).replaceChildren();$('reportNote').textContent='';$('qualityNotice').textContent='';$('downloadStatus').textContent='';$('outputPlayer').hidden=true;$('resultVideo').pause();$('resultVideo').removeAttribute('src');$('resultVideo').load();$('runProgress').value=0;}
async function downloadReport(identity,file,button){
    button.disabled=true;$('downloadStatus').textContent='Preparing download…';
    try{
        const response=await fetch(`/api/jobs/${identity}/files/${file}?download=true`);
        if(!response.ok){let message='File is unavailable for this run.';try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch{}throw Error(message);}
        const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
        link.href=url;link.download=`${identity.slice(0,8)}_${file}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
        if(identity===currentRun)$('downloadStatus').textContent='Download sent to your browser. Your results remain saved.';
    }catch(e){if(identity===currentRun)$('downloadStatus').textContent=`Download failed: ${e.message} Your results are still saved. You can retry.`;}
    finally{button.disabled=false;}
}
function showDownloads(identity,available){
    const files=new Set(available||[]);$('outputPlayer').hidden=false;$('resultVideo').hidden=!files.has('annotated.mp4');
    if(files.has('annotated.mp4'))$('resultVideo').src=`/api/jobs/${identity}/files/annotated.mp4`;
    const root=$('resultDownloads');root.replaceChildren();
    for(const [file,label] of [['annotated.mp4','Download video'],['layout.json','Layout JSON'],['report.json','Full report'],['crossings.csv','Entry / exit CSV'],['zone_totals.csv','Zone totals CSV'],['zone_visits.csv','Zone visits CSV'],['queue_totals.csv','Billing queue summary CSV'],['queue_visits.csv','Billing queue visits CSV']]){
        if(!files.has(file))continue;
        const control=document.createElement(file.endsWith('.mp4')?'a':'button');control.textContent=label;
        if(file.endsWith('.mp4')){control.href=`/api/jobs/${identity}/files/${file}?download=true`;control.download=file;control.target='_blank';control.rel='noopener';}
        else{control.type='button';control.onclick=()=>downloadReport(identity,file,control);}
        root.append(control);
    }
    $('downloadStatus').textContent=files.has('queue_totals.csv')?'CSV downloads contain the full run, regardless of dashboard filters. Times in CSV files are in seconds.':'Billing CSV files are unavailable for this saved run. Existing video and reports remain available.';
}
async function refreshRuns(){if(!appInfo)return;const jobs=await api('/api/jobs'),select=$('previousRuns');select.replaceChildren(new Option('Choose a saved run…',''),...jobs.map(j=>new Option(`${j.name} · ${j.state} · ${j.id.slice(0,8)}`,j.id)));if(jobs.some(j=>j.id===currentRun))select.value=currentRun;}
async function poll(identity){
    clearTimeout(pollTimer);
    try{const state=await api(`/api/jobs/${identity}`);if(identity!==currentRun)return;procMessage(state.message);$('savedPath').textContent='Saved folder: '+state.output_folder;
        $('runProgress').value=state.total_frames?Math.min(99,100*(state.frames_processed||0)/state.total_frames):0;
        reportComplete=state.state==='complete';if(state.report)showReport(state.report);
        if(['running','finalizing'].includes(state.state)){setBusy(true);pollTimer=setTimeout(()=>poll(identity),1500);}
        else{setBusy(false);if(state.state==='complete'){$('runProgress').value=100;showDownloads(identity,state.available_files);}await refreshRuns();}
    }catch(e){if(identity!==currentRun)return;procMessage('Cannot read progress: '+e.message+'. Reopen the local app if it stopped.');setBusy(false);}
}
function uploadVideo(file,output){return new Promise((resolve,reject)=>{const request=new XMLHttpRequest();request.open('POST',`/api/jobs?output=${encodeURIComponent(output)}&filename=${encodeURIComponent(file.name)}`);request.setRequestHeader('X-App-Token',appInfo.token);request.setRequestHeader('Content-Type','application/octet-stream');request.upload.onprogress=e=>{if(e.lengthComputable){const percent=Math.round(100*e.loaded/e.total);procMessage(`Saving source video to the output folder… ${percent}%`);$('runProgress').value=percent;}};request.onload=()=>{try{const data=JSON.parse(request.responseText);if(request.status>=400)throw Error(data.detail||'Upload failed');resolve(data);}catch(e){reject(e);}};request.onerror=()=>reject(Error('Could not save the video. Check the local app is running.'));request.send(file);});}
function processingLayout(data,job){
    // HTML video dimensions may include sample-aspect-ratio correction, whereas
    // OpenCV supplies encoded pixels. Map the drawn view to those actual pixels.
    const sx=job.width/data.frame_width,sy=job.height/data.frame_height;
    const point=p=>[Math.max(0,Math.min(job.width-1,Math.round(p[0]*sx))),Math.max(0,Math.min(job.height-1,Math.round(p[1]*sy)))];
    const result={...data,frame_width:job.width,frame_height:job.height};
    for(const kind of kinds)result[kind]=(data[kind]||[]).map(s=>kind==='entry_exit_lines'?{...s,p1:point(s.p1),p2:point(s.p2)}:{...s,polygon:s.polygon.map(point)});
    return result;
}
$('processVideo').onclick=async()=>{
    if(!appInfo||busy)return;let data;try{data=layout();}catch(e){procMessage(e.message);return;}
    const file=$('videoFile').files[0];if(!file){procMessage('Choose the original video first.');return;}
    const camera=$('cameraId').value.trim();if(!/^[A-Za-z0-9_-]{1,100}$/.test(camera)||!/^[A-Za-z0-9_-]{1,100}$/.test(data.store_id)){procMessage('Store and camera IDs must use letters, numbers, hyphens or underscores.');return;}
    const output=$('outputFolder').value.trim();setBusy(true);clearTimeout(pollTimer);procMessage('Saving video and layout…');
    try{const job=await uploadVideo(file,output);const calibrated=processingLayout(data,job);await api(`/api/jobs/${job.id}/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({layout:calibrated,camera_id:camera})});rememberRun(job.id);clearResults();await poll(job.id);}
    catch(e){procMessage(e.message);setBusy(false);await refreshRuns().catch(()=>{});}
};
$('previousRuns').onchange=()=>{const id=$('previousRuns').value;if(!id)return;rememberRun(id);clearResults();$('downloadStatus').textContent='';poll(id);};
$('refreshRuns').onclick=()=>refreshRuns().catch(e=>procMessage(e.message));
(async()=>{if(!/^https?:$/.test(location.protocol))return;try{appInfo=await api('/api/info');$('backendHint').textContent=`Model processing runs locally on your computer. Output folders must be inside ${appInfo.workspace}. Full videos may take longer than their playback duration on CPU.`;$('outputFolder').value=appInfo.default_output;$('refreshRuns').disabled=false;setBusy(false);await refreshRuns();const requested=new URLSearchParams(location.search).get('run')||lastRun();if(requested&&/^[a-f0-9]{32}$/.test(requested)){rememberRun(requested);$('previousRuns').value=requested;clearResults();await poll(requested);}}catch(e){procMessage('Processing server is unavailable. Open start-zone-app.cmd in the project folder.');}})();
