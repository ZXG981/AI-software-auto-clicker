const $ = (s) => document.querySelector(s);
const escapeHtml = (s) => s.replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
let profiles = {};
let photoObjectUrl = null;
let installPrompt = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}
function message(text, type='assistant') { $('#messages').insertAdjacentHTML('beforeend', `<div class="bubble ${type}">${escapeHtml(text)}</div>`); }
function renderProfile(agent='cet4') {
  const profile = profiles[agent] || {};
  $('#profile').innerHTML = `<p>${escapeHtml(profile.signature || '')}</p><strong>阅读方法</strong><ul>${(profile.reading_methods || []).map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul><strong>写作方法</strong><ul>${(profile.writing_methods || []).map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul><strong>思考顺序</strong><p>${(profile.answer_sequence || []).map(escapeHtml).join(' → ')}</p>`;
}
function agentName(agent) { return ({cet4:'四级',cet6:'六级',postgrad:'考研'})[agent] || '共享'; }
function renderSources(sources) { $('#sources').innerHTML = sources.length ? sources.map(s => `<div class="source"><strong>${escapeHtml(s.title)}</strong> <span class="tag">${agentName(s.agent)}</span> <span class="tag">${escapeHtml(s.type)}</span><div class="citation">${s.chunk_count} 个课程片段</div></div>`).join('') : '<p>还没有课程资料。先导入字幕或文本。</p>'; }
async function refresh() { const d = await api('/api/library'); profiles=d.profiles; renderProfile($('#agent').value); renderSources(d.sources); }
function jobStatusName(status) { return ({queued:'等待中',running:'识别中',completed:'已完成',partial:'部分完成',failed:'失败',interrupted:'已中断'})[status] || status; }
function renderJobs(items) {
  const box=$('#jobs');
  if(!items.length) { box.innerHTML=''; return; }
  box.innerHTML=items.slice(0,5).map(job => {
    const pct=job.total?Math.round(job.current/job.total*100):0;
    const failures=(job.items || []).filter(x=>!x.ok).map(x=>`<li>${escapeHtml(x.url)}：${escapeHtml(x.error)}</li>`).join('');
    return `<details class="job"><summary><strong>${jobStatusName(job.status)}</strong> · ${job.current}/${job.total} · 成功 ${job.success_count||0}</summary><div class="job-progress"><span style="width:${pct}%"></span></div>${failures?`<ul class="job-errors">${failures}</ul>`:''}<p class="hint">手机可以锁屏或离开页面，电脑会继续处理；稍后回来查看结果。</p></details>`;
  }).join('');
}
async function refreshJobs() {
  const d=await api('/api/jobs?limit=5');
  renderJobs(d.items || []);
  if((d.items || []).some(job => ['completed','partial'].includes(job.status))) await refresh();
}

$('#uploadForm').addEventListener('submit', async e => { e.preventDefault(); const status=$('#importStatus'); status.textContent='正在导入与整理…'; try { const d=await api('/api/import/upload',{method:'POST',body:new FormData(e.target)}); status.textContent=`已导入「${d.title}」，生成 ${d.chunks} 个课程片段。`; e.target.reset(); await refresh(); } catch(err) { status.textContent=err.message; } });
$('#textImport').addEventListener('click', async () => { const status=$('#importStatus'), content=$('#lessonText').value; if(!content.trim()) return status.textContent='请先粘贴文本。'; status.textContent='正在整理…'; try { const d=await api('/api/import/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('#textTitle').value || '课堂文本',content,agent:$('#importAgent').value})}); status.textContent=`已导入「${d.title}」。`; $('#lessonText').value=''; await refresh(); } catch(err) { status.textContent=err.message; } });
$('#queueSource').addEventListener('click', async () => { const status=$('#importStatus'); const urls=$('#sourceUrls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean).slice(0,20); if(!urls.length) return status.textContent='请至少填写一个公开视频链接。'; status.textContent=`已提交 ${urls.length} 个链接。任务在电脑后台运行，手机可以锁屏或离开页面。`; try { await api('/api/import/links/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls,agent:$('#importAgent').value,force_transcribe:$('#forceTranscribe').checked})}); $('#sourceUrls').value=''; await refreshJobs(); } catch(err) { status.textContent=err.message; } });
$('#agent').addEventListener('change', () => renderProfile($('#agent').value));
function clearPhoto() {
  $('#photoInput').value='';
  $('#cameraInput').value='';
  if (photoObjectUrl) URL.revokeObjectURL(photoObjectUrl);
  photoObjectUrl=null;
  $('#photoPreview').removeAttribute('src');
  $('#photoPreview').classList.remove('visible');
  $('#photoName').textContent='图片只在本机识别';
}
function selectPhoto(event) {
  const file=event.target.files[0];
  if(!file) return clearPhoto();
  const other=event.target.id==='cameraInput'?$('#photoInput'):$('#cameraInput');
  other.value='';
  if(photoObjectUrl) URL.revokeObjectURL(photoObjectUrl);
  photoObjectUrl=URL.createObjectURL(file);
  $('#photoPreview').src=photoObjectUrl;
  $('#photoPreview').classList.add('visible');
  $('#photoName').textContent=`${file.name} · ${(file.size/1024/1024).toFixed(2)} MB`;
}
function selectedPhoto() { return $('#cameraInput').files[0] || $('#photoInput').files[0]; }
$('#photoInput').addEventListener('change', selectPhoto);
$('#cameraInput').addEventListener('change', selectPhoto);
$('#clearPhoto').addEventListener('click', clearPhoto);
$('#ocrPhoto').addEventListener('click', async () => {
  const photo=selectedPhoto();
  if(!photo) return message('请先拍照，或从相册选择题目图片。','assistant');
  $('#ocrPhoto').disabled=true;
  $('#ocrPhoto').textContent='正在识别…';
  try {
    const data=new FormData();
    data.append('file',photo);
    const d=await api('/api/ocr/photo',{method:'POST',body:data});
    $('#question').value=d.ocr_text;
    clearPhoto();
    $('#question').focus();
    message(`已识别文字，请检查或修改后点击“发送问题”。\n\n${d.ocr_text}`,'assistant');
  } catch(err) { message(err.message,'assistant'); }
  finally { $('#ocrPhoto').disabled=false; $('#ocrPhoto').textContent='先识别到输入框'; }
});
$('#askForm').addEventListener('submit', async e => { e.preventDefault(); const q=$('#question').value.trim(); const photo=selectedPhoto(); if(!q && !photo) return message('请输入问题，或先拍照上传题目。','assistant'); if(photo && photoObjectUrl) { const img=document.createElement('img');img.src=photoObjectUrl;img.className='chat-photo';const bubble=document.createElement('div');bubble.className='bubble student';bubble.appendChild(img);$('#messages').append(bubble); } if(q) message(q,'student'); $('#question').value=''; const loading=document.createElement('div');loading.className='bubble assistant';loading.textContent=photo?'正在本地识别图片中的文字并组织答案…':'正在按课程方法组织答案…';$('#messages').append(loading); try { let d; if(photo) { const data=new FormData();data.append('file',photo);data.append('question',q);data.append('mode',$('#mode').value);data.append('agent',$('#agent').value);d=await api('/api/ask/photo',{method:'POST',body:data}); } else { d=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,mode:$('#mode').value,agent:$('#agent').value})}); } loading.remove(); message(`${d.classification}\n\n${d.answer}`); if(d.ocr_text) message(`图片识别文字：\n${d.ocr_text}`,'assistant'); if(d.citations.length) $('#messages').insertAdjacentHTML('beforeend', `<div class="citation">课程依据：${d.citations.map(c=>`${escapeHtml(c.title)}${c.time?' · '+c.time:''}`).join('；')}</div>`); clearPhoto(); } catch(err) { loading.textContent=err.message; } });
refresh().catch(err => { $('#profile').textContent=err.message; });
refreshJobs().catch(() => {});
setInterval(() => refreshJobs().catch(() => {}), 5000);

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  installPrompt=event;
  $('#installApp').hidden=false;
});
$('#installApp').addEventListener('click', async () => {
  if(!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt=null;
  $('#installApp').hidden=true;
});
if ('serviceWorker' in navigator && (window.isSecureContext || location.hostname === '127.0.0.1' || location.hostname === 'localhost')) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

