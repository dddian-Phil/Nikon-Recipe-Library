const data=JSON.parse(document.getElementById('payload').textContent),rs=data.recipes,creators=data.creators;
const $=id=>document.getElementById(id),el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const recipeById=new Map(rs.map(r=>[r.id,r]));
let library={captures:[],records:{recipeFavorites:[],photoFavorites:[],photoAssignments:{}},summary:{files:0,photos:0,matched:0,favoritePhotos:0},revision:[-1,-1]},pane='recipes',activePhoto=null,activeRecipe=null,writeQueue=Promise.resolve(),toastTimer,connectionFailures=0;
let visibleRecipeIds=[],visiblePhotoIds=[];
const viewers={recipe:{queue:[],id:null,focus:false,origin:null},photo:{queue:[],id:null,focus:false,origin:null}};
const controls=['q','author','camera','lens','base'];
const fmt=(key,value)=>value==='gamma'?'自定义曲线（未公开曲线数值）':(data.values[key]||{})[String(value)]??(value!==null&&typeof value==='object'?JSON.stringify(value):String(value??''));
const recipeFavorite=id=>library.records.recipeFavorites.includes(id);
const myPhotos=id=>library.captures.filter(p=>p.recipeId===id);
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500)}
function setOptions(id,values){const selected=$(id).value;while($(id).options.length>1)$(id).remove(1);for(const value of [...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'))){const option=el('option',value);option.value=value;$(id).append(option)}$(id).value=selected}
function populateFilters(){setOptions('camera',[...rs.map(r=>r.camera),...library.captures.map(p=>p.camera)]);setOptions('lens',[...rs.map(r=>r.lens),...library.captures.map(p=>p.lens)]);setOptions('base',[...rs.map(r=>r.pictureControl.baseName),...library.captures.map(p=>p.pictureControlBase)])}
populateFilters();
try{const state=JSON.parse(localStorage.getItem('nikon-public-archive-filters')||'{}');for(const key of controls)if(state[key])$(key).value=state[key]}catch{}
const authorCounts=new Map();for(const r of rs)for(const name of r.authors)authorCounts.set(name,(authorCounts.get(name)||0)+1);
const availableCreators=creators.filter(c=>authorCounts.has(c.name));
function syncAuthorTrigger(){const c=availableCreators.find(c=>c.name===$('author').value);$('selected-author-name').textContent=c?c.name:'全部作者';$('selected-author-avatar').hidden=!c?.localPortrait;$('selected-author-initial').hidden=!!c?.localPortrait;if(c?.localPortrait)$('selected-author-avatar').src=c.localPortrait;$('selected-author-initial').textContent=c?c.name.slice(0,1):'全'}
function chooseAuthor(name){$('author').value=name;update();$('author-picker').close()}
function renderAuthors(){
 const query=$('author-search').value.trim().toLocaleLowerCase(),matches=availableCreators.filter(c=>!query||c.name.toLocaleLowerCase().includes(query));$('author-grid').replaceChildren();
 function tile(c){const name=c?.name||'',button=el('button',undefined,'author-choice');button.type='button';button.setAttribute('aria-pressed',String($('author').value===name));button.setAttribute('aria-label',c?'选择作者 '+name:'选择全部作者');const portrait=el(c?.localPortrait?'img':'span',c?.localPortrait?undefined:(c?name.slice(0,1):'全部'),'author-portrait'+(c?.localPortrait?'':' author-initial'));if(c?.localPortrait){portrait.src=c.localPortrait;portrait.alt='';portrait.loading='lazy';portrait.decoding='async'}button.append(portrait,el('span',c?name:'全部作者','author-choice-name'),el('span',(c?authorCounts.get(name):rs.length)+' 个方案','author-choice-count'));button.addEventListener('click',()=>chooseAuthor(name));return button}
 const fragment=document.createDocumentFragment();if(!query)fragment.append(tile(null));for(const c of matches)fragment.append(tile(c));$('author-grid').append(fragment);$('author-no-match').hidden=matches.length>0;$('author-match-count').textContent=query?`找到 ${matches.length} 位作者`:`${availableCreators.length} 位作者 · 点击官网头像筛选`;
}
$('author-trigger').onclick=()=>{$('author-search').value='';renderAuthors();$('author-picker').showModal();$('author-picker').scrollTop=0;$('author-search').focus({preventScroll:true})};$('author-search').addEventListener('input',renderAuthors);$('author-picker-close').onclick=()=>$('author-picker').close();
function button(text,handler,cls){const b=el('button',text,cls);b.type='button';b.addEventListener('click',handler);return b}
function favoriteButton(id){const b=button(recipeFavorite(id)?'★ 已收藏':'☆ 收藏方案',()=>mutate({action:'toggle_recipe',id}));b.setAttribute('aria-pressed',String(recipeFavorite(id)));b.setAttribute('aria-label',(recipeFavorite(id)?'取消收藏 ':'收藏方案 ')+recipeById.get(id).name);return b}
function photoLikeButton(photo){const b=button(photo.favorite?'♥ 已喜欢':'♡ 喜欢',()=>mutate({action:'toggle_photo',id:photo.id}));b.setAttribute('aria-pressed',String(photo.favorite));b.setAttribute('aria-label',(photo.favorite?'取消喜欢 ':'喜欢照片 ')+photo.name);return b}
function update(){
 syncAuthorTrigger();const state=Object.fromEntries(controls.map(key=>[key,$(key).value]));try{localStorage.setItem('nikon-public-archive-filters',JSON.stringify(state))}catch{}
 const query=state.q.trim().toLocaleLowerCase(),favorites=$('only-favorites').checked;
 $('view-recipes').setAttribute('aria-pressed',String(pane==='recipes'));$('view-photos').setAttribute('aria-pressed',String(pane==='photos'));$('favorites-label').textContent=pane==='recipes'?'只看收藏方案':'只看喜欢的照片';$('mine-filter').hidden=pane==='photos';$('unmatched-filter').hidden=pane==='recipes';$('q').placeholder=pane==='recipes'?'方案、作者、器材、介绍关键词…':'文件名、方案、作者、器材…';
 $('view-help').textContent='点开照片后，方向键连续浏览 · F 专注看图';
 const c=creators.find(c=>c.name===state.author);$('creator').replaceChildren();$('creator').style.display=c?'flex':'none';
 if(c){if(c.localPortrait){const img=el('img');img.src=c.localPortrait;img.alt=c.name;$('creator').append(img)}const body=el('div');body.append(el('h3',c.name),el('span',c.position,'muted'),el('p',c.profile));$('creator').append(body)}
 const fragment=document.createDocumentFragment();let count=0,total=0;
 if(pane==='recipes'){
  total=rs.length;
  const list=rs.filter(r=>(!state.author||r.authors.includes(state.author))&&(!state.camera||r.camera===state.camera)&&(!state.lens||r.lens===state.lens)&&(!state.base||r.pictureControl.baseName===state.base)&&(!favorites||recipeFavorite(r.id))&&(!$('only-mine').checked||myPhotos(r.id).length>0)&&(!query||[r.name,...r.authors,r.camera,r.lens,r.description,r.outline].join(' ').toLocaleLowerCase().includes(query)));
  visibleRecipeIds=list.map(r=>r.id);count=list.length;for(const r of list){const tile=el('article',undefined,'recipe-tile');tile.dataset.recipeId=r.id;const card=button('',()=>openRecipe(r),'card');card.setAttribute('aria-label','查看 '+r.name);const img=el('img');img.src=r.image;img.alt=r.name;img.loading='lazy';img.decoding='async';const body=el('div',undefined,'text');body.append(el('h3',r.name),el('div',r.authors.join(' / '),'author'),el('div',r.camera,'gear'),el('div',r.lens,'gear'),el('div','发布于 '+r.publicDate.slice(0,10),'date'));card.append(img,body);const actions=el('div',undefined,'tile-actions');actions.append(favoriteButton(r.id));const own=myPhotos(r.id);if(own.length)actions.append(button('我的实拍 '+own.length+' 张',()=>openRecipe(r),'photo-count'));tile.append(card,actions);fragment.append(tile)}
 }else{
  total=library.captures.length;
  const list=library.captures.filter(p=>{const r=recipeById.get(p.recipeId);return(!state.author||r?.authors.includes(state.author))&&(!state.camera||p.camera===state.camera)&&(!state.lens||p.lens===state.lens)&&(!state.base||p.pictureControlBase===state.base)&&(!favorites||p.favorite)&&(!$('only-unmatched').checked||!p.recipeId)&&(!query||[p.name,p.camera,p.lens,p.pictureControlName,r?.name,...(r?.authors||[]),...p.files.map(f=>f.path)].join(' ').toLocaleLowerCase().includes(query))});
  visiblePhotoIds=list.map(p=>p.id);count=list.length;for(const p of list)fragment.append(photoTile(p));
 }
 $('grid').replaceChildren(fragment);$('count').textContent=`${count} / ${total} ${pane==='recipes'?'个方案':'张实拍'}`;$('empty').hidden=!!count;$('empty').textContent=pane==='photos'&&total===0?'把 JPG 或 NEF 放入最外层「photos」文件夹，稍等片刻即可在这里查看。':'没有符合条件的内容，请调整筛选。';
 renderFilterChips();
}
function photoTile(p){const tile=el('article',undefined,'recipe-tile photo-tile');tile.dataset.photoId=p.id;const card=button('',()=>openPhoto(p.id),'card');card.setAttribute('aria-label','查看实拍 '+p.name);if(p.preview){const img=el('img');img.src='/'+p.preview;img.alt=p.name;img.loading='lazy';card.append(img)}else card.append(el('div','暂无预览，可打开原文件','placeholder'));const body=el('div',undefined,'text'),r=recipeById.get(p.recipeId);body.append(el('h3',p.name),el('div',r?r.name:(p.pictureControlName==='AUTO'?'自动优化校准（AUTO）':p.pictureControlName||'待手动关联'),'author'),el('div',p.camera+' · '+p.lens,'gear'),el('div',p.takenAt||'未提供拍摄时间','date'));card.append(body);const actions=el('div',undefined,'tile-actions');actions.append(photoLikeButton(p),el('span',p.association==='manual'?'手动关联':r?'名称匹配':p.metadataConflict?'记录有差异':'未关联云创','muted'));tile.append(card,actions);return tile}
function parameterTable(title,obj){const section=el('section',undefined,'section');section.append(el('h3',title));const table=el('table');for(const[key,value]of Object.entries(obj)){const tr=el('tr');tr.append(el('th',data.labels[key]||key),el('td',fmt(key,value)));table.append(tr)}section.append(table);return section}
function miniPhotos(photos){const grid=el('div',undefined,'photo-grid');for(const p of photos){const b=button('',()=>openPhoto(p.id,photos.map(p=>p.id)),'photo-mini');b.setAttribute('aria-label','查看实拍 '+p.name);if(p.preview){const img=el('img');img.src='/'+p.preview;img.alt=p.name;img.loading='lazy';b.append(img)}else b.append(el('div','无预览','placeholder'));b.append(el('span',(p.favorite?'♥ ':'')+p.name));grid.append(b)}return grid}
function renderRecipePhotos(r){const target=$('recipe-my-photos');if(!target)return;const own=myPhotos(r.id);target.replaceChildren(el('h3','我的实拍（'+own.length+' 张）'));if(own.length)target.append(miniPhotos(own));else target.append(el('p','把使用这个方案拍摄的照片放进「photos」，识别后会出现在这里。也可以在照片详情里手动关联。','muted'));const favorite=$('recipe-detail-favorite');if(favorite){favorite.replaceChildren(favoriteButton(r.id))}}
function openRecipe(r,queue=null){
 prepareViewer('recipe',r.id,queue);
 activeRecipe=r.id;const content=$('detail');content.replaceChildren();const photo=el('div',undefined,'detailimage'),img=el('img');img.src=r.image;img.alt=r.name;photo.append(img,el('p','官网展示样片 · '+(r.imageInfo?`${r.imageInfo.width} × ${r.imageInfo.height} px`:'')));
 const text=el('div'),title=el('h2',r.name);title.id='detailtitle';text.append(title,el('p',r.authors.join(' / '),'author'),el('p',r.camera+' / '+r.lens,'muted'));const favorite=el('div');favorite.id='recipe-detail-favorite';text.append(favorite);const own=el('section',undefined,'section');own.id='recipe-my-photos';text.append(own);
 for(const [heading,value]of[['概要',r.outline],['方案介绍',r.description],['备注',r.memo]]){const body=(value||'').replace(/^\s*创作者的(?:说明|介绍)[：:\t ]*(?:\r?\n|$)\s*/,'').trim();if(body)text.append(el('h3',heading),el('p',body,'prose'));}
 text.append(parameterTable('优化校准',r.pictureControl),parameterTable('官网样片拍摄参数',r.exif));const links=el('div',undefined,'links');for(const[label,path]of[['打开官网样片',r.image],['完整公开数据','catalog/raw/details/'+r.id+'.json'],['查看官网',r.source]]){const a=el('a',label);a.href=path;a.target='_blank';a.rel='noopener';links.append(a)}text.append(links);content.append(photo,text);renderRecipePhotos(r);img.addEventListener('click',()=>toggleFocus('recipe'));syncViewer('recipe');if(!$('modal').open)$('modal').showModal();$('modal').scrollTop=0;
}
function openPhoto(id,queue=null){
 const p=library.captures.find(p=>p.id===id);if(!p)return;activePhoto=id;const content=$('photo-detail');content.replaceChildren();const photo=el('div',undefined,'detailimage');if(p.preview){const img=el('img');img.src='/'+p.preview;img.alt=p.name;photo.append(img)}else photo.append(el('div',p.previewIssue||'暂无预览','placeholder'));
 prepareViewer('photo',id,queue);
 const info=el('div'),title=el('h2',p.name);title.id='photo-title';info.append(title,el('p',p.camera+' / '+p.lens,'muted'));const r=recipeById.get(p.recipeId);info.append(el('h3','这张照片使用的方案'));
 if(r){info.append(el('p',r.name));info.append(el('p','作者：'+r.authors.join(' / '),'muted'));info.append(button('查看这个云创方案',()=>{$('photo-modal').close();openRecipe(r)}))}else info.append(el('p',p.pictureControlName==='AUTO'?'相机记录为 AUTO（自动优化校准），未关联具体云创。':'尚未关联到具体云创方案。'));
 info.append(el('p','相机记录：'+(p.pictureControlName||'未提供')+' / '+(p.pictureControlBase||'未提供'),'muted'));
 if(p.metadataIssue||p.metadataConflict)info.append(el('p',p.metadataConflict?'同组文件的优化校准名称不一致，请手动确认。':p.metadataIssue,'muted'));
 if(p.candidateRecipeIds?.length)info.append(el('p','名称可能被相机截短，请在下方选择并确认方案。','muted'));
 const assignment=el('section',undefined,'assignment'),label=el('label','关联方案（可手动修正）');label.htmlFor='photo-assignment';const select=el('select');select.id='photo-assignment';for(const[value,text]of[['__auto__','按相机记录自动识别'],['__none__','暂不关联任何方案'],...rs.map(r=>[r.id,r.name+' — '+r.authors.join(' / ')])]){const option=el('option',text);option.value=value;select.append(option)}select.value=p.association==='manual'?(p.recipeId||'__none__'):'__auto__';select.onchange=()=>mutate(select.value==='__auto__'?{action:'reset_assignment',id}:{action:'assign_photo',id,recipeId:select.value==='__none__'?null:select.value});assignment.append(label,select,el('p',p.association==='manual'?'当前使用手动关联，后续扫描会保留。':'当前按名称识别，未核对所有调色参数。','muted'));info.append(assignment);
 info.append(parameterTable('我的拍摄信息',{拍摄时间:p.takenAt,相机:p.camera,镜头:p.lens,ISO:p.iso||'',光圈:p.fNumber||'',快门秒:p.exposureTime||'',焦距:p.focalLength||''}));const sources=el('div',undefined,'source-files');sources.append(el('h3','原文件（JPG＋NEF 归为一次拍摄）'));for(const file of p.files){const link=el('a',file.kind+' · '+file.path);link.href='/api/original?path='+encodeURIComponent(file.path);link.target='_blank';link.rel='noopener';sources.append(link)}info.append(sources);content.append(photo,info);$('photo-like').textContent=p.favorite?'♥ 已喜欢':'♡ 喜欢这张';$('photo-like').setAttribute('aria-pressed',String(p.favorite));photo.querySelector('img')?.addEventListener('click',()=>toggleFocus('photo'));syncViewer('photo');if(!$('photo-modal').open)$('photo-modal').showModal();
}
async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});const result=await response.json();if(!response.ok)throw new Error(result.error||'操作失败');return result}
function acceptState(next){
 const changed=JSON.stringify(next.revision)!==JSON.stringify(library.revision);library=next;const s=next.summary;$('summary').textContent=`${rs.length} 个公开方案 · ${availableCreators.length} 位作者 · 我的实拍 ${s.photos} 张（${s.files} 个文件） · 已关联 ${s.matched} 张`;$('view-photos').textContent='我的实拍（'+s.photos+'）';$('library-status').textContent=next.scanning?'正在扫描新照片…':'自动检查已开启 · 将新照片放入「photos」即可 · 收藏已保存到本地';$('scan-photos').disabled=next.scanning;
 const issues=next.scanReport?.errors?.length||0;$('library-alert').hidden=!(next.scanError||issues);$('library-alert').textContent=next.scanError?'更新未完成：'+next.scanError:issues+' 个文件暂未读出元数据，可在照片详情中手动关联；其余照片已导入。';
 if(changed){populateFilters();update();if(activeRecipe&&$('modal').open)renderRecipePhotos(recipeById.get(activeRecipe));if(activePhoto&&$('photo-modal').open)openPhoto(activePhoto)}
}
async function refresh(){try{const next=await api('/api/state');connectionFailures=0;acceptState(next)}catch(error){if(++connectionFailures>=2){$('library-alert').hidden=false;$('library-alert').textContent='本地服务已断开，请重新双击最外层的 open-library.cmd。收藏记录仍保存在本地文件中。'}}}
function mutate(body){writeQueue=writeQueue.then(async()=>{acceptState(await api('/api/records',body))}).catch(error=>toast('保存失败：'+error.message));return writeQueue}
function switchPane(next){pane=next;$('only-favorites').checked=false;update()}
$('view-recipes').onclick=()=>switchPane('recipes');$('view-photos').onclick=()=>switchPane('photos');
$('scan-photos').onclick=async()=>{try{acceptState(await api('/api/scan',{}));toast('已开始检查新增照片')}catch(error){toast(error.message)}};
$('import-records').onclick=()=>$('records-file').click();$('records-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{const incoming=JSON.parse(await file.text());acceptState(await api('/api/import',incoming));toast('备份已合并，原有收藏已保留')}catch(error){toast('导入失败：'+error.message)}event.target.value=''};
$('close').onclick=()=>$('modal').close();$('photo-close').onclick=()=>$('photo-modal').close();$('photo-like').onclick=()=>activePhoto&&mutate({action:'toggle_photo',id:activePhoto});
for(const dialog of[$('modal'),$('photo-modal'),$('author-picker')])dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close()}});
for(const id of controls)$(id).addEventListener(id==='q'?'input':'change',update);for(const id of['only-favorites','only-mine','only-unmatched'])$(id).addEventListener('change',update);
$('reset').onclick=()=>{for(const id of controls)$(id).value='';for(const id of['only-favorites','only-mine','only-unmatched'])$(id).checked=false;update()};
function applyRoute(){const route=location.hash.slice(1);if(route.startsWith('author=')){for(const id of controls)$(id).value='';$('author').value=decodeURIComponent(route.slice(7));pane='recipes';update()}else if(route){const selected=recipeById.get(route);if(selected)openRecipe(selected)}}
function renderFilterChips(){
 const labels={q:'搜索',author:'作者',camera:'相机',lens:'镜头',base:'优化校准'};const fragment=document.createDocumentFragment();
 for(const id of controls){const value=$(id).value;if(!value)continue;const chip=button(labels[id]+'：'+value,()=>{$(id).value='';update()},'filter-chip');chip.setAttribute('aria-label','移除'+labels[id]+'筛选：'+value);const cross=el('span','×');cross.setAttribute('aria-hidden','true');chip.append(cross);fragment.append(chip)}
 $('active-filters').replaceChildren(fragment);
}
function setDensity(value){value=value==='compact'?'compact':'comfort';document.body.dataset.density=value;$('density-comfort').setAttribute('aria-pressed',String(value==='comfort'));$('density-compact').setAttribute('aria-pressed',String(value==='compact'));try{localStorage.setItem('nikon-library-density',value)}catch{}}
function viewerDialog(kind){return $(kind==='photo'?'photo-modal':'modal')}
function prepareViewer(kind,id,queue){
 const v=viewers[kind],dialog=viewerDialog(kind),all=kind==='photo'?library.captures.map(p=>p.id):rs.map(r=>r.id),visible=kind==='photo'?visiblePhotoIds:visibleRecipeIds;
 if(!dialog.open){v.origin={x:scrollX,y:scrollY,element:document.activeElement,parent:[...document.querySelectorAll('dialog[open]')].map(n=>({node:n,top:n.scrollTop}))};v.focus=false}
 const source=queue||(dialog.open?v.queue:(visible.includes(id)?visible:all));v.queue=[...new Set(source.filter(key=>all.includes(key)))];if(!v.queue.includes(id))v.queue.unshift(id);v.id=id;
}
function syncViewer(kind){
 const v=viewers[kind],dialog=viewerDialog(kind),position=v.queue.indexOf(v.id);$(kind+'-position').textContent=(position+1)+' / '+v.queue.length;$(kind+'-prev').disabled=position<=0;$(kind+'-next').disabled=position>=v.queue.length-1;$(kind+'-focus').textContent=v.focus?'显示资料':'专注看图';$(kind+'-focus').setAttribute('aria-pressed',String(v.focus));dialog.classList.toggle('is-focused',v.focus);
 const strip=$(kind+'-strip');strip.replaceChildren();if(!v.focus)return;const fragment=document.createDocumentFragment();let selected;
 for(const id of v.queue){const item=kind==='photo'?library.captures.find(p=>p.id===id):recipeById.get(id);if(!item)continue;const thumb=button('',()=>kind==='photo'?openPhoto(id,v.queue):openRecipe(item,v.queue));thumb.setAttribute('aria-label','查看 '+item.name);thumb.setAttribute('aria-pressed',String(id===v.id));const path=kind==='photo'?item.preview&&'/'+item.preview:item.image;if(path){const img=el('img');img.src=path;img.alt='';img.loading='lazy';thumb.append(img)}else thumb.append(el('span',item.name));fragment.append(thumb);if(id===v.id)selected=thumb}
 strip.append(fragment);requestAnimationFrame(()=>{if(selected)strip.scrollLeft=selected.offsetLeft-strip.offsetLeft-strip.clientWidth/2+selected.clientWidth/2});
}
function toggleFocus(kind){const v=viewers[kind];v.focus=!v.focus;syncViewer(kind);viewerDialog(kind).scrollTop=0}
function navigateViewer(kind,delta){const v=viewers[kind],index=v.queue.indexOf(v.id)+delta;if(index<0||index>=v.queue.length)return;const id=v.queue[index];kind==='photo'?openPhoto(id,v.queue):openRecipe(recipeById.get(id),v.queue);viewerDialog(kind).scrollTop=0}
for(const kind of['recipe','photo']){
 $(kind+'-prev').onclick=()=>navigateViewer(kind,-1);$(kind+'-next').onclick=()=>navigateViewer(kind,1);$(kind+'-focus').onclick=()=>toggleFocus(kind);
 viewerDialog(kind).addEventListener('close',()=>{const v=viewers[kind],origin=v.origin;v.focus=false;viewerDialog(kind).classList.remove('is-focused');requestAnimationFrame(()=>{if(origin){for(const parent of origin.parent)if(parent.node.open)parent.node.scrollTop=parent.top;if(origin.element?.isConnected)origin.element.focus({preventScroll:true});window.scrollTo(origin.x,origin.y)}})});
}
$('density-comfort').onclick=()=>setDensity('comfort');$('density-compact').onclick=()=>setDensity('compact');try{setDensity(localStorage.getItem('nikon-library-density'))}catch{setDensity('comfort')}
$('shortcut-help').onclick=()=>$('shortcuts').showModal();$('shortcuts-close').onclick=()=>$('shortcuts').close();
document.addEventListener('keydown',event=>{
 if(event.ctrlKey||event.metaKey||event.altKey||event.target.closest('input,select,textarea,[contenteditable="true"]'))return;
 const key=event.key.toLowerCase();if(event.key==='?'&&!$('shortcuts').open){event.preventDefault();$('shortcuts').showModal();return}
 if($('shortcuts').open||$('author-picker').open)return;
 const kind=$('photo-modal').open?'photo':$('modal').open?'recipe':null;
 if(kind){if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();navigateViewer(kind,event.key==='ArrowLeft'?-1:1)}else if(key==='f'){event.preventDefault();toggleFocus(kind)}}else if(event.key==='/'){event.preventDefault();$('q').focus();$('q').select()}
});
document.addEventListener('click',event=>{if(!event.target.closest('#utility-menu'))$('utility-menu').open=false});
$('stamp').textContent='官网方案采集时间：'+data.report.finishedAt;update();applyRoute();window.addEventListener('hashchange',applyRoute);refresh();setInterval(()=>{if(!document.hidden)refresh()},3000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
