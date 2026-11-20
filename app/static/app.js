const $ = (id) => document.getElementById(id);
let uploaded = [];
async function init(){
  const r = await fetch('/api/status'); const s = await r.json();
  $('mode').textContent = s.label;
  if(s.mode === 'azure') $('mode').classList.add('azure');
}
$('uploadForm').addEventListener('submit', async (e)=>{
  e.preventDefault(); const file=$('file').files[0]; if(!file)return;
  $('uploadStatus').textContent='Uploading and indexing…';
  const fd=new FormData(); fd.append('file',file);
  try{
    const r=await fetch('/api/upload',{method:'POST',body:fd}); const data=await r.json();
    if(!r.ok) throw new Error(data.detail||'Upload failed');
    uploaded.push(data.filename); $('docs').innerHTML=uploaded.map(n=>`<span class="doc-pill">▤ ${escapeHtml(n)}</span>`).join('');
    $('uploadStatus').textContent=data.message; $('file').value='';
  }catch(err){$('uploadStatus').textContent=err.message}
});
$('askForm').addEventListener('submit', async (e)=>{
  e.preventDefault(); const question=$('question').value.trim(); if(!question)return;
  $('answerArea').hidden=false; $('answer').textContent='Searching your notes…'; $('citations').innerHTML='';
  try{
    const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})});
    const data=await r.json(); if(!r.ok) throw new Error(data.detail||'Question failed');
    $('answer').textContent=data.answer;
    $('citations').innerHTML=data.citations.length ? data.citations.map((c,i)=>`
      <article class="citation" id="source-${i+1}">
        <a href="#source-${i+1}">[${i+1}] ${escapeHtml(c.filename)}</a>
        <div class="meta">Page ${c.page} · Source passage</div>
        <p>${escapeHtml(c.passage)}</p>
      </article>`).join('') : '<p class="hint">No source passages to cite.</p>';
  }catch(err){$('answer').textContent=err.message}
});
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
init();
