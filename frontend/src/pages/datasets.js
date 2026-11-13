import {datasets} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,notify,date} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';

const MERGE_ACTIONS={added:'新增',replaced:'替换',ignored:'忽略',withdrawn:'撤回',conflict:'冲突',rejected:'拒收'};
const INTAKE_STATUS={accepted:'已接入',rejected:'整批拒收'};
const SEGMENT_STATUS={active:'有效',withdrawn:'已撤回'};
const segmentColumns=[{key:'run',label:'片段'},{key:'version',label:'当前版本'},{key:'versions',label:'版本数'},{key:'status',label:'状态'},{key:'arm',label:'实验组'},{key:'shift',label:'班次'},{key:'seconds',label:'单件节拍/秒'}];
const detailColumns=[{key:'row',label:'行'},{key:'run',label:'片段'},{key:'version',label:'版本'},{key:'action',label:'处理'},{key:'reason',label:'说明'}];
let selectedId=null;

function countChips(counts={}){
 const order=['added','replaced','ignored','withdrawn','conflict','rejected'];
 const chips=order.filter(k=>counts[k]).map(k=>'<span class="count-chip '+k+'">'+MERGE_ACTIONS[k]+' '+counts[k]+'</span>').join('');
 return chips||'<span class="count-chip">未写入片段</span>';
}

function intakeCard(item){
 const details=(item.details||[]).map(d=>({...d,action:MERGE_ACTIONS[d.action]||d.action}));
 return '<article class="intake-card '+esc(item.status)+'">'+
  '<div class="intake-head"><div><strong>接入 #'+item.id+' · '+esc(item.source)+'</strong>'+
  '<p>'+esc(item.actor||'—')+' · '+date(item.created_at)+' · 投递 '+item.rows+' 行</p></div>'+
  '<div class="intake-counts"><span class="intake-status '+esc(item.status)+'">'+esc(INTAKE_STATUS[item.status]||item.status)+'</span>'+countChips(item.counts)+'</div></div>'+
  (item.status==='rejected'?'<p class="warning">'+esc(item.error)+'</p>':'')+
  (details.length?table(detailColumns,details,'本次接入没有逐行明细。'):'<p class="hint">本次接入没有逐行明细。</p>')+
  '</article>';
}

function segmentSummary(selected){
 if(!selected)return '';
 const active=(selected.segments||[]).filter(s=>s.status==='active').length;
 return '<div class="numbers batch-numbers"><div><span>有效片段</span><strong>'+active+'</strong></div><div><span>撤回中</span><strong>'+(selected.withdrawn||0)+'</strong></div><div><span>接入次数</span><strong>'+((selected.intakes||[]).length)+'</strong></div><div><span>内容指纹</span><strong>'+esc(selected.checksum.slice(0,10))+'…</strong></div></div>';
}

export async function dataPage(){
 workspace.datasets=await datasets.list();
 if(!selectedId||!workspace.datasets.some(d=>d.id===selectedId))selectedId=workspace.datasets[0]?.id||null;
 const selected=selectedId?await datasets.get(selectedId):null,m=workspace.meta,canWrite=session.user.role!=='viewer';
 const mergeColumns=(m.merge_columns||[]).map(c=>esc(c.key)+' / '+esc(c.label)).join('，');
 const segments=(selected?.segments||[]).slice(0,60).map(s=>({...s,status:SEGMENT_STATUS[s.status]||s.status}));
 return heading('02 / DATA INTAKE','数据接入与批次追溯','补传并入原批次：同版本重复投递不新增样本，旧版本不覆盖新值，同版内容冲突整批拒收；撤回与更高版本更正逐次留痕。')+
 '<div class="dataset-grid"><section class="panel">'+
  '<div class="section-title"><h2>业务批次</h2><span>'+workspace.datasets.length+' 批</span></div>'+
  '<div class="batch-list">'+workspace.datasets.map(d=>'<button class="batch-item '+(d.id===selectedId?'active':'')+'" data-dataset="'+d.id+'"><strong>'+esc(d.name)+'</strong><span>'+d.rows+' 条有效片段 · '+esc(d.source)+'</span><small>撤回中 '+(d.withdrawn||0)+' · SHA256 '+esc(d.checksum.slice(0,18))+'…</small></button>').join('')+'</div>'+
  '<div class="section-title"><h2>向原批次补传</h2><span>合并，不新建批次</span></div>'+
  '<form id="merge-form"><label>目标批次<select name="dataset_id" required>'+workspace.datasets.map(d=>'<option value="'+d.id+'" '+(d.id===selectedId?'selected':'')+'>'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label>'+
  '<label>补传 CSV 或 JSON<input type="file" id="merge-file" accept=".json,.csv"></label>'+
  '<label>补传记录 JSON<textarea name="records" rows="6" required placeholder=\'[{"run":"YC-A001","arm":"A","shift":"早班","seconds":19.6,"version":2}]\'></textarea></label>'+
  '<p class="hint">字段：'+mergeColumns+'。version 缺省为 1；withdrawn 为 true 时只撤回该片段版本，不再要求组别与节拍。同版冲突整批拒收，不写入任何一行。</p>'+
  '<button type="submit" class="primary" '+(canWrite?'':'disabled')+'>校验并向原批次合并</button></form>'+
  '<div class="section-title"><h2>接入新批次</h2><span>独立批次</span></div>'+
  '<form id="dataset-form"><label>批次名称<input name="name" required minlength="2" placeholder="例如：11 月复测批次"></label>'+
  '<label>上传 CSV 或 JSON<input type="file" id="file-input" accept=".json,.csv"></label>'+
  '<label>记录 JSON<textarea name="records" rows="6" placeholder="粘贴 JSON 数组，或先选择上方文件" required></textarea></label>'+
  '<p class="hint">字段：'+m.columns.map(c=>esc(c.key)+' / '+esc(c.label)).join('，')+'。5–5,000 条记录；每条默认版本 1。</p>'+
  '<button type="submit" class="primary" '+(canWrite?'':'disabled')+'>校验并接入新批次</button></form>'+
 '</section><section>'+
  '<section class="panel"><div class="section-title"><h2>'+esc(selected?.name||'尚无批次')+'</h2><span>片段版本前 60 条</span></div>'+
  segmentSummary(selected)+
  '<div id="data-preview">'+table(segmentColumns,segments,'该批次当前没有有效片段。')+'</div></section>'+
  '<section class="panel"><div class="section-title"><h2>接入历史</h2><span>新增、替换、忽略、撤回逐次可查</span></div>'+
  ((selected?.intakes||[]).length?selected.intakes.map(intakeCard).join(''):'<div class="empty">尚无补传记录。向原批次合并后，这里会保留每次接入的逐行处理结果。</div>')+
  '</section></section></div>';
}

export function bindData(refresh){
 const readFile=async(input,textarea,setSource)=>{try{const file=input.files[0];if(!file)return;const text=await file.text(),records=file.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);textarea.value=JSON.stringify(records,null,2);setSource(file.name);}catch(error){notify(error.message,true);}};
 let mergeSource='manual.json',newSource='manual.json';
 document.querySelector('#file-input').addEventListener('change',e=>readFile(e.target,document.querySelector('#dataset-form [name=records]'),name=>newSource=name));
 document.querySelector('#merge-file').addEventListener('change',e=>readFile(e.target,document.querySelector('#merge-form [name=records]'),name=>mergeSource=name));
 document.querySelectorAll('[data-dataset]').forEach(button=>button.onclick=async()=>{selectedId=Number(button.dataset.dataset);await refresh();});
 submit(document.querySelector('#dataset-form'),async f=>{await datasets.create({name:f.get('name'),source:newSource,records:JSON.parse(f.get('records'))});notify('数据已接入，可前往工作台执行分析');await refresh();});
 // 补传失败（整批拒收）也要刷新：接入历史保留拒收原因与冲突行。
 const mergeForm=document.querySelector('#merge-form');
 mergeForm.addEventListener('submit',async e=>{
  e.preventDefault();const button=mergeForm.querySelector('[type=submit]');button.disabled=true;
  try{
   const target=Number(new FormData(mergeForm).get('dataset_id'));
   const result=await datasets.merge(target,{source:mergeSource,records:JSON.parse(mergeForm.querySelector('[name=records]').value)});
   selectedId=target;
   const c=result.counts||{};
   notify('已并入原批次：新增 '+(c.added||0)+'，替换 '+(c.replaced||0)+'，忽略 '+(c.ignored||0)+'，撤回 '+(c.withdrawn||0));
   await refresh();
  }catch(error){notify(error.message,true);await refresh();}
  finally{button.disabled=false;}
 });
}
