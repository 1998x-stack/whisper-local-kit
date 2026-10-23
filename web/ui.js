import {AdaptiveSoundGate, encodeWav} from './sound-gate.js';
const $=s=>document.getElementById(s);
const headers={'X-Local-Whisper-Client':'1'};
const states={uploading:'上传中',queued:'排队中',running:'转写中',complete:'完成',failed:'失败',interrupted:'中断'};
const escapeHTML=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>{const x=Math.max(0,Math.floor(Number(t)||0));return `${String(Math.floor(x/60)).padStart(2,'0')}:${String(x%60).padStart(2,'0')}`;};
function tab(name){document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.tab===name));document.querySelectorAll('.mode').forEach(e=>e.classList.toggle('hidden',e.id!==`pane-${name}`));}
document.querySelectorAll('.tab').forEach(e=>e.addEventListener('click',()=>tab(e.dataset.tab)));
async function request(url,options={}) {const response=await fetch(url, options); const body=await response.json(); if(!response.ok)throw Error(typeof body.detail==='string'?body.detail:`HTTP ${response.status}`);return body;}
let picked=null;
function selectFile(f){picked=f; $('picked').textContent=f?`${f.name} · ${(f.size/1048576).toFixed(1)} MB`:'MP3 · WAV · M4A · MP4 · FLAC · WEBM 等';}
$('drop').onclick=()=>$('file').click();
$('drop').onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();$('file').click();}};
$('file').onchange=e=>selectFile(e.target.files[0]||null);
for(const name of ['dragover','drop']) $('drop').addEventListener(name,e=>e.preventDefault());
$('drop').addEventListener('drop',e=>selectFile(e.dataTransfer.files[0]||null));
$('upload-send').onclick=async()=>{
  if(!picked){$('upload-message').textContent='请先选择文件';return;}
  const fd=new FormData(); fd.append('file',picked);fd.append('model',$('upload-model').value);fd.append('language',$('upload-lang').value);fd.append('task','transcribe');
  $('upload-send').disabled=true; $('upload-message').textContent='上传中…';
  try{const j=await request('/api/transcribe',{method:'POST',headers,body:fd});$('upload-message').textContent='任务已排队：'+j.id.slice(0,8);selectFile(null);$('file').value='';await refreshJobs();}
  catch(e){$('upload-message').textContent='提交失败：'+e.message;}
  finally{$('upload-send').disabled=false;}
};
function batchRequest(){return {input_directory:$('batch-input').value.trim(),output_directory:$('batch-output').value.trim()||null,recursive:$('batch-recursive').checked,skip_existing:$('batch-skip').checked,model:$('batch-model').value,language:$('batch-lang').value};}
$('batch-preview').onclick=async()=>{
  $('batch-info').textContent='正在检查路径…';
  try{const d=await request('/api/batch/preview',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(batchRequest())});
  $('batch-info').textContent=`找到 ${d.count} 个 MP3\n`+d.files.join('\n')+(d.count>40?'\n…仅展示前 40 个':'');}
  catch(e){$('batch-info').textContent='扫描失败：'+e.message;}
};
$('batch-start').onclick=async()=>{
  $('batch-start').disabled=true;
  try{const d=await request('/api/batch',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(batchRequest())});
  $('batch-info').textContent=`已提交 ${d.total_files} 个 MP3，正在按顺序处理。\nSRT 输出位置：${d.output_directory}\n批量任务：${d.id.slice(0,8)}`;await refreshJobs();}
  catch(e){$('batch-info').textContent='提交失败：'+e.message;}
  finally{$('batch-start').disabled=false;}
};
async function refreshJobs(){
  try{const d=await request('/api/jobs');const root=$('jobs');if(!d.length){root.innerHTML='<div class="empty">暂无任务</div>';return;}
  root.innerHTML=d.slice(0,25).map(j=>{
    const suffix=j.kind==='batch'?` · 已完成 ${j.completed_files||0}/${j.total_files||0} · 跳过 ${j.skipped_files||0} · 失败 ${j.failed_files||0}`:'';
    const downloads=j.status==='complete'&&(!j.kind||j.kind==='upload')?`<div class="downloads">${['txt','srt','vtt','json'].map(f=>`<a href="/api/jobs/${encodeURIComponent(j.id)}/download/${f}">${f.toUpperCase()}</a>`).join('')}</div>`:'';
    const preview=j.text?`<div class="preview">${escapeHTML(j.text.slice(0,1000))}</div>`:'';
    const errors=j.errors?.length?`<div class="error">失败样例：${escapeHTML(j.errors.slice(0,3).map(x=>x.file+': '+x.error).join(' | '))}</div>`:'';
    return `<div class="job"><div class="jobhead"><b class="jobname">${escapeHTML(j.name)}</b><span class="pill ${escapeHTML(j.status)}">${states[j.status]||escapeHTML(j.status)}</span></div><div class="stage">${escapeHTML(j.stage||'')} ${escapeHTML(suffix)} ${j.error?`<span class="error">${escapeHTML(j.error)}</span>`:''}</div>${preview}${downloads}${errors}</div>`;
  }).join('');
  }catch(e){/* leave prior status visible */}
}

let liveSession=null, micStream=null, micContext=null, micSource=null, micNode=null, gate=null;
let monitoring=false, sending=false, stopping=false, pending=[], dropped=0;
function micState(state,stats){
  $('mic-orb').className=`orb ${state==='recording'?'recording':state==='calibrating'?'calibrating':'silent'}`;
  $('mic-glyph').textContent=state==='recording'?'●':'◌';
  $('live-state').textContent={calibrating:'校准环境底噪…',silent:'静止待机 · 检测中',recording:'检测到声音 · 采集中',stopped:'监听已停止'}[state]||'等待开始';
  $('live-note').textContent=state==='recording'?'正在本机录制这段声音':'未检测到明显声音时，不提交推理任务';
  $('meter-fill').style.width=`${Math.max(0,Math.min(100,(stats.levelDb+80)/70*100))}%`;
  $('meter-label').textContent=`当前 ${stats.levelDb} dBFS · 触发阈值 ${stats.thresholdDb} dBFS · 背景 ${stats.noiseDb} dBFS`;
  $('elapsed').textContent=fmt(stats.elapsed);
}
function liveClip(clip){
  if(pending.length>=12){dropped++;$('live-message').textContent=`CPU 转写速度落后，已有 ${dropped} 段未提交；建议改用 tiny 或稍后上传完整录音。`;return;}
  pending.push({...clip,blob:encodeWav(clip.samples,clip.sampleRate)});
  $('live-message').textContent=`采集 ${clip.startSeconds.toFixed(1)}–${(clip.startSeconds+clip.durationSeconds).toFixed(1)}s · 本地等待上传 ${pending.length} 段`;
  drain();
}
async function drain(){
  if(sending||!liveSession)return;
  sending=true;
  try{
    while(pending.length){
      const c=pending[0];const fd=new FormData();fd.append('file',c.blob,'mic.wav');fd.append('offset_seconds',String(c.startSeconds));fd.append('duration_seconds',String(c.durationSeconds));
      try{
        await request(`/api/live/${liveSession}/chunks`,{method:'POST',headers,body:fd});
        pending.shift();
      }catch(e){
        if(String(e.message).includes('队列已满')) {await new Promise(r=>setTimeout(r,2500));continue;}
        pending.shift();dropped++;
        $('live-message').textContent=`音频片段提交失败：${e.message}，丢失段数 ${dropped}`;
      }
    }
  }finally{sending=false;if(stopping){
    try{await request(`/api/live/${liveSession}/stop`,{method:'POST',headers});}catch(e){}
    stopping=false;$('mic-btn').disabled=false;
  }}
}
async function beginMic(){
  if(!navigator.mediaDevices?.getUserMedia||!window.AudioWorkletNode) throw Error('浏览器不支持安全上下文中的 AudioWorklet 麦克风录制。请使用最新版 Chrome / Firefox 的 localhost 页面。');
  micStream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:false},video:false});
  try{
    const s=await request('/api/live/sessions',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({model:$('live-model').value,language:$('live-lang').value})});
    liveSession=s.id;
    micContext=new AudioContext({latencyHint:'interactive'});
    await micContext.audioWorklet.addModule('/assets/recorder-worklet.js');
    micSource=micContext.createMediaStreamSource(micStream);
    micNode=new AudioWorkletNode(micContext,'mic-frames');
    gate=new AdaptiveSoundGate({sampleRate:micContext.sampleRate,sensitivityDb:+$('sensitivity').value,onClip:liveClip,onState:micState});
    micNode.port.onmessage=e=>gate?.push(e.data);
    micSource.connect(micNode);micNode.connect(micContext.destination);
    await micContext.resume();
    monitoring=true;pending=[];dropped=0;
    $('mic-btn').textContent='停止监听并生成字幕';
    $('live-model').disabled=true;$('live-lang').disabled=true;
    $('live-message').textContent='已请求麦克风权限。仅在检测到声音时发送录音片段给本机 Whisper。';
  }catch(e){micStream?.getTracks().forEach(t=>t.stop()); micStream=null; if(micContext)await micContext.close().catch(()=>{});micContext=null;throw e;}
}
async function endMic(){
  monitoring=false;stopping=true;
  micNode?.port.close();if(gate){gate.finish();gate=null;}
  micStream?.getTracks().forEach(t=>t.stop());micStream=null;
  if(micContext)await micContext.close().catch(()=>{});
  micContext=null;micSource=null;micNode=null;
  $('mic-btn').disabled=true;$('mic-btn').textContent='正在提交剩余录音…';
  $('live-model').disabled=false;$('live-lang').disabled=false;
  $('live-message').textContent=`麦克风已停止。剩余待提交：${pending.length} 段，后台转写仍可能继续。`;
  await drain();
  $('mic-btn').textContent='开始新的监听';
  $('live-state').textContent='已停止，麦克风已关闭';
}
$('mic-btn').onclick=async()=>{
  $('mic-btn').disabled=true;
  try{if(monitoring)await endMic();else await beginMic();}
  catch(e){$('live-message').textContent='麦克风启动失败：'+e.message;}
  finally{if(!stopping)$('mic-btn').disabled=false;}
};
$('sensitivity').oninput=()=>{const x=+$('sensitivity').value;if(gate)gate.sensitivityDb=x;$('sensitivity-label').textContent=`高于背景 ${x} dB（数值越小越灵敏）`;};
async function refreshLive(){
  if(!liveSession)return;
  try{
    const d=await request(`/api/live/${liveSession}`);
    const timeline=$('live-transcript');
    timeline.innerHTML=d.segments?.length?d.segments.slice(-80).map(s=>`<p><time>${fmt(s.start)}</time>${escapeHTML(s.text)}</p>`).join(''):'<span class="muted">尚无转写内容。声音片段会排队由 CPU 处理。</span>';
    $('live-downloads').innerHTML=`${['srt','txt','vtt','json'].map(f=>`<a href="/api/live/${encodeURIComponent(liveSession)}/download/${f}">保存完整 ${f.toUpperCase()}</a>`).join('')}`;
    if(d.failed)$('live-message').textContent=`已转写 ${d.completed}/${d.submitted} 段，失败 ${d.failed} 段：${d.last_error||''}`;
  }catch(e){}
}
async function tick(){try{const h=await request('/api/health');$('health').textContent=`本地运行 · ${h.threads} CPU 线程`;$('health').classList.add('ok');}catch(e){$('health').textContent='服务断开';$('health').classList.remove('ok');}await Promise.all([refreshJobs(),refreshLive()]);}
window.addEventListener('pagehide',()=>{micStream?.getTracks().forEach(t=>t.stop());});
tick();setInterval(tick,2600);
