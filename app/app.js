const data=JSON.parse(document.getElementById('payload').textContent),rs=data.recipes,creators=data.creators;
const $=id=>document.getElementById(id),el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const recipeById=new Map(rs.map(r=>[r.id,r]));
function formatTakenAt(value){return (value||'').replace(/^(\d{4}):(\d{2}):(\d{2})/, '$1-$2-$3')}
let library={captures:[],records:{recipeFavorites:[],photoFavorites:[],photoAssignments:{}},summary:{files:0,photos:0,matched:0,favoritePhotos:0},revision:[-1,-1]},pane='recipes',activePhoto=null,activeRecipe=null,writeQueue=Promise.resolve(),toastTimer,connectionFailures=0;
let visibleRecipeIds=[],visiblePhotoIds=[];
let newRecipeIds=new Set(data.syncMetadata?.newRecipeIds||[]),catalogRevision=data.syncMetadata?.revision||'',catalogLoading=false;
const assignmentQueries=new Map();
let keyboardNavigation=false;
document.addEventListener('pointerdown',event=>{keyboardNavigation=false;event.target.closest('.card,.photo-mini,.viewer-topnav button')?.classList.add('pointer-focus-return')},true);
document.addEventListener('keydown',()=>{keyboardNavigation=true;const elements=document.querySelector('dialog[open]')?[document.activeElement]:document.querySelectorAll('.pointer-focus-return');for(const element of elements)element?.classList.remove('pointer-focus-return')},true);
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
const authorCounts=new Map();let availableCreators=[];
function rebuildAuthors(){authorCounts.clear();for(const r of rs)for(const name of r.authors)authorCounts.set(name,(authorCounts.get(name)||0)+1);availableCreators=creators.filter(c=>authorCounts.has(c.name))}
rebuildAuthors();
function syncAuthorTrigger(){const c=availableCreators.find(c=>c.name===$('author').value);$('selected-author-name').textContent=c?c.name:'全部作者';$('selected-author-avatar').hidden=!c?.localPortrait;$('selected-author-initial').hidden=!!c?.localPortrait;if(c?.localPortrait)$('selected-author-avatar').src=c.localPortrait;$('selected-author-initial').textContent=c?c.name.slice(0,1):'全'}
function chooseAuthor(name){$('author').value=name;update();$('author-picker').close()}
function renderAuthors(){
 const query=$('author-search').value.trim().toLocaleLowerCase(),matches=availableCreators.filter(c=>!query||c.name.toLocaleLowerCase().includes(query));$('author-grid').replaceChildren();
 function tile(c){const name=c?.name||'',button=el('button',undefined,'author-choice');button.type='button';button.setAttribute('aria-pressed',String($('author').value===name));button.setAttribute('aria-label',c?'选择作者 '+name:'选择全部作者');const portrait=el(c?.localPortrait?'img':'span',c?.localPortrait?undefined:(c?name.slice(0,1):'全部'),'author-portrait'+(c?.localPortrait?'':' author-initial'));if(c?.localPortrait){portrait.src=c.localPortrait;portrait.alt='';portrait.loading='lazy';portrait.decoding='async'}button.append(portrait,el('span',c?name:'全部作者','author-choice-name'),el('span',(c?authorCounts.get(name):rs.length)+' 个方案','author-choice-count'));button.addEventListener('click',()=>chooseAuthor(name));return button}
 const fragment=document.createDocumentFragment();if(!query)fragment.append(tile(null));for(const c of matches)fragment.append(tile(c));$('author-grid').append(fragment);$('author-no-match').hidden=matches.length>0;$('author-match-count').textContent=query?`找到 ${matches.length} 位作者`:`${availableCreators.length} 位作者 · 点击官网头像筛选`;
}
$('author-trigger').onclick=()=>{$('author-search').value='';renderAuthors();$('author-picker').showModal();$('author-picker').scrollTop=0;$('author-search').focus({preventScroll:true})};$('author-search').addEventListener('input',renderAuthors);$('author-picker-close').onclick=()=>$('author-picker').close();
function button(text,handler,cls){const b=el('button',text,cls);b.type='button';b.addEventListener('click',handler);return b}
function setFavoriteContent(target,active,kind){
 const photo=kind==='photo',icon=el('span',photo?(active?'♥':'♡'):(active?'★':'☆'),'favorite-icon');icon.setAttribute('aria-hidden','true');
 target.classList.add('favorite-action');target.setAttribute('aria-pressed',String(active));target.title=photo?(active?'取消喜欢':'喜欢这张照片'):(active?'取消收藏':'收藏方案');target.replaceChildren(icon,el('span',photo?(active?'已喜欢':'喜欢'):(active?'已收藏':'收藏方案')));
}
function favoriteButton(id){const active=recipeFavorite(id),b=button('',()=>mutate({action:'toggle_recipe',id}));setFavoriteContent(b,active,'recipe');b.setAttribute('aria-label',(active?'取消收藏 ':'收藏方案 ')+recipeById.get(id).name);return b}
function photoLikeButton(photo){const b=button('',()=>mutate({action:'toggle_photo',id:photo.id}));setFavoriteContent(b,photo.favorite,'photo');b.setAttribute('aria-label',(photo.favorite?'取消喜欢 ':'喜欢照片 ')+photo.name);return b}
function update(){
 syncAuthorTrigger();const state=Object.fromEntries(controls.map(key=>[key,$(key).value]));try{localStorage.setItem('nikon-public-archive-filters',JSON.stringify(state))}catch{}
 const query=state.q.trim().toLocaleLowerCase(),favorites=$('only-favorites').checked;
 $('view-recipes').setAttribute('aria-pressed',String(pane==='recipes'));$('view-photos').setAttribute('aria-pressed',String(pane==='photos'));$('favorites-label').textContent=pane==='recipes'?'只看收藏':'只看喜欢';$('mine-filter').hidden=pane==='photos';$('unmatched-filter').hidden=pane==='recipes';$('q').placeholder=pane==='recipes'?'搜索方案、作者、器材…':'搜索照片、方案、器材…';
 $('view-help').textContent='点开照片后，方向键连续浏览 · F 专注看图';
 $('new-filter').hidden=pane==='photos';$('recipe-sort-control').hidden=pane==='photos';
 const c=creators.find(c=>c.name===state.author);$('creator').replaceChildren();$('creator').style.display=c?'flex':'none';
 if(c){if(c.localPortrait){const img=el('img');img.src=c.localPortrait;img.alt=c.name;$('creator').append(img)}const body=el('div');body.append(el('h3',c.name),el('span',c.position,'muted'),el('p',c.profile));$('creator').append(body)}
 const fragment=document.createDocumentFragment();let count=0,total=0;
 if(pane==='recipes'){
  total=rs.length;
  const list=rs.filter(r=>(!state.author||r.authors.includes(state.author))&&(!state.camera||r.camera===state.camera)&&(!state.lens||r.lens===state.lens)&&(!state.base||r.pictureControl.baseName===state.base)&&(!favorites||recipeFavorite(r.id))&&(!$('only-mine').checked||myPhotos(r.id).length>0)&&(!$('only-new').checked||newRecipeIds.has(r.id))&&(!query||[r.name,...r.authors,r.camera,r.lens,r.description,r.outline].join(' ').toLocaleLowerCase().includes(query)));
  const order=$('recipe-sort').value;if(order!=='official')list.sort((a,b)=>(order==='new'?Number(newRecipeIds.has(b.id))-Number(newRecipeIds.has(a.id)):0)||(Date.parse(b.publicDate)||0)-(Date.parse(a.publicDate)||0)||a.id.localeCompare(b.id));
  visibleRecipeIds=list.map(r=>r.id);count=list.length;for(const r of list){const tile=el('article',undefined,'recipe-tile');tile.dataset.recipeId=r.id;const card=button('',()=>openRecipe(r),'card');card.setAttribute('aria-label','查看 '+r.name);const img=el('img');img.src=r.image;img.alt=r.name;img.loading='lazy';img.decoding='async';const body=el('div',undefined,'text');body.append(el('h3',r.name));if(newRecipeIds.has(r.id))body.append(el('span','新增','new-badge'));body.append(el('div',r.authors.join(' / '),'author'),el('div',r.camera,'gear'),el('div',r.lens,'gear'),el('div','发布于 '+r.publicDate.slice(0,10),'date'));card.append(img,body);const actions=el('div',undefined,'tile-actions');actions.append(favoriteButton(r.id));const own=myPhotos(r.id);if(own.length)actions.append(button('我的实拍 '+own.length+' 张',()=>openRecipe(r),'photo-count'));tile.append(card,actions);fragment.append(tile)}
 }else{
  total=library.captures.length;
  const list=library.captures.filter(p=>{const r=recipeById.get(p.recipeId);return(!state.author||r?.authors.includes(state.author))&&(!state.camera||p.camera===state.camera)&&(!state.lens||p.lens===state.lens)&&(!state.base||p.pictureControlBase===state.base)&&(!favorites||p.favorite)&&(!$('only-unmatched').checked||!p.recipeId)&&(!query||[p.name,p.camera,p.lens,p.pictureControlName,r?.name,...(r?.authors||[]),...p.files.map(f=>f.path)].join(' ').toLocaleLowerCase().includes(query))});
  visiblePhotoIds=list.map(p=>p.id);count=list.length;for(const p of list)fragment.append(photoTile(p));
 }
 $('grid').replaceChildren(fragment);$('count').textContent=`${count===total?count:count+' / '+total} ${pane==='recipes'?'个方案':'张实拍'}`;$('empty').hidden=!!count;$('empty').textContent=pane==='photos'&&total===0?'把 JPG 或 NEF 放入最外层「photos」文件夹，稍等片刻即可在这里查看。':'没有符合条件的内容，请调整筛选。';
 renderFilterChips();
}
function photoAssociationStatus(p){
 const matched=recipeById.has(p.recipeId),name=String(p.pictureControlName||'').trim();
 if(matched)return {text:p.association==='manual'?'手动关联':'名称匹配',warning:false};
 if(p.association==='manual'&&!p.recipeId)return {text:'暂不关联',warning:false};
 if(p.metadataConflict)return {text:'记录有差异',warning:true};
 if(name.toUpperCase()==='AUTO')return {text:'自动优化校准',warning:false};
 if(!name)return {text:p.metadataIssueCode&&p.metadataIssueCode!=='missing_picture_control'?'未识别优化校准':'未提供优化校准',warning:true};
 return {text:'未匹配云创',warning:true};
}
function photoTile(p){
 const tile=el('article',undefined,'recipe-tile photo-tile');tile.dataset.photoId=p.id;
 const card=button('',()=>openPhoto(p.id),'card');card.setAttribute('aria-label','查看实拍 '+p.name);
 if(p.preview){const img=el('img');img.src='/'+p.preview;img.alt=p.name;img.loading='lazy';card.append(img)}else card.append(el('div','暂无预览，可打开原文件','placeholder'));
 const body=el('div',undefined,'text'),r=recipeById.get(p.recipeId),automatic=String(p.pictureControlName||'').trim().toUpperCase()==='AUTO';
 body.append(el('h3',p.name));
 const scheme=r?r.name:automatic?'自动优化校准（AUTO）':p.pictureControlName;
 if(scheme)body.append(el('div',scheme,'author'));
 body.append(el('div',p.camera+' · '+p.lens,'gear'),el('div',formatTakenAt(p.takenAt)||'未提供拍摄时间','date'));card.append(body);
 const actions=el('div',undefined,'tile-actions'),status=photoAssociationStatus(p);
 actions.append(photoLikeButton(p),el('span',status.text,'muted photo-association'+(status.warning?' is-unmatched':'')));
 tile.append(card,actions);return tile;
}
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
 const fileIssues=(p.issues||[]).filter(issue=>!['missing_picture_control','picture_control_unavailable','picture_control_encoding','incomplete_picture_control','unsupported_picture_control','conflicting_picture_control'].includes(issue.code));
 if(fileIssues.length){const warnings=el('section',undefined,'photo-problems');warnings.append(el('h3','文件读取提示'));for(const issue of fileIssues)warnings.append(problemItem(issue));info.append(warnings)}
 info.append(assignmentEditor(p));
 info.append(parameterTable('我的拍摄信息',{拍摄时间:formatTakenAt(p.takenAt),相机:p.camera,镜头:p.lens,ISO:p.iso||'',光圈:p.fNumber||'',快门秒:p.exposureTime||'',焦距:p.focalLength||''}));const sources=el('div',undefined,'source-files');sources.append(el('h3','原文件'));for(const file of p.files){const link=el('a',file.kind+' · '+file.path);link.href='/api/original?path='+encodeURIComponent(file.path);link.target='_blank';link.rel='noopener';sources.append(link)}info.append(sources);content.append(photo,info);setFavoriteContent($('photo-like'),p.favorite,'photo');$('photo-like').setAttribute('aria-label',(p.favorite?'取消喜欢 ':'喜欢照片 ')+p.name);photo.querySelector('img')?.addEventListener('click',()=>toggleFocus('photo'));syncViewer('photo');if(!$('photo-modal').open)$('photo-modal').showModal();
}
async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});const result=await response.json();if(!response.ok)throw new Error(result.error||'操作失败');return result}

let websiteStatus={running:false},catalogBusy=false,catalogStatusRequested=false;
function renderWebsiteStatus(status){
 if(data.mode==='static')return;
 if(status)websiteStatus=status;
 const s=websiteStatus;
 $('publish-website').disabled=!!s.running||catalogBusy;
 $('publish-website').textContent=s.running?'正在同步网页…':'同步到 GitHub 网页';
 $('website-sync-panel').hidden=!s.state||s.state==='idle';
 $('website-sync-status').textContent=(s.message||'')+(s.state==='succeeded'&&s.finishedAt?' · '+new Date(s.finishedAt).toLocaleString('zh-CN',{hour12:false}):'');
 $('website-sync-error').hidden=!s.error;$('website-sync-error').textContent=s.error||'';
 $('website-link').hidden=s.state!=='succeeded';
 $('sync-catalog').disabled=!!s.running||catalogBusy;
}
$('publish-website').onclick=async()=>{
 if(data.mode==='static')return;
 $('publish-website').disabled=true;
 try{renderWebsiteStatus(await api('/api/website/sync',{}))}
 catch(error){renderWebsiteStatus({...websiteStatus,state:'failed',message:'未能开始网页同步。',error:error.message})}
};

function acceptState(next){
 const changed=JSON.stringify(next.revision)!==JSON.stringify(library.revision);library=next;const s=next.summary;$('summary').textContent=`${rs.length} 个公开方案 · ${availableCreators.length} 位作者 · 我的实拍 ${s.photos} 张`;$('view-photos').textContent='我的实拍（'+s.photos+'）';$('library-status').textContent=next.scanning?'正在扫描新照片…':next.scanError?'照片扫描未完成':'自动检查已开启';$('scan-photos').disabled=next.scanning;
 $('library-alert').hidden=!next.scanError;$('library-alert').textContent=next.scanError?'照片扫描未完成：'+next.scanError+'。请检查 photos 文件夹的权限和可用磁盘空间。':'';if(next.catalogSync)renderCatalogStatus(next.catalogSync);if(next.websiteSync)renderWebsiteStatus(next.websiteSync);
 if(changed){populateFilters();update();if(activeRecipe&&$('modal').open)renderRecipePhotos(recipeById.get(activeRecipe));if(activePhoto&&$('photo-modal').open)openPhoto(activePhoto)}
}
async function refresh(){try{const next=await api('/api/state');connectionFailures=0;acceptState(next)}catch(error){if(++connectionFailures>=2){$('library-status').textContent='本地服务已断开';$('library-alert').hidden=false;$('library-alert').textContent='本地服务已断开，请重新双击最外层的 open-library.cmd。收藏记录仍保存在本地文件中。'}}}
function mutate(body){writeQueue=writeQueue.then(async()=>{acceptState(await api('/api/records',body))}).catch(error=>toast('保存失败：'+error.message));return writeQueue}
function switchPane(next){pane=next;$('only-favorites').checked=false;update()}
$('view-recipes').onclick=()=>switchPane('recipes');$('view-photos').onclick=()=>switchPane('photos');
$('scan-photos').onclick=async()=>{try{acceptState(await api('/api/scan',{}));toast('已开始检查新增照片')}catch(error){toast(error.message)}};
$('import-records').onclick=()=>$('records-file').click();$('records-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{const incoming=JSON.parse(await file.text());acceptState(await api('/api/import',incoming));toast('备份已合并，原有收藏已保留')}catch(error){toast('导入失败：'+error.message)}event.target.value=''};
$('close').onclick=()=>$('modal').close();$('photo-close').onclick=()=>$('photo-modal').close();$('photo-like').onclick=()=>activePhoto&&mutate({action:'toggle_photo',id:activePhoto});
for(const dialog of[$('modal'),$('photo-modal'),$('author-picker')])dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close()}});
for(const id of controls)$(id).addEventListener(id==='q'?'input':'change',update);for(const id of['only-favorites','only-mine','only-unmatched','only-new','recipe-sort'])$(id).addEventListener('change',update);
$('reset').onclick=()=>{for(const id of controls)$(id).value='';for(const id of['only-favorites','only-mine','only-unmatched','only-new'])$(id).checked=false;$('recipe-sort').value='latest';update()};
function applyRoute(){const route=location.hash.slice(1);if(route.startsWith('author=')){for(const id of controls)$(id).value='';$('author').value=decodeURIComponent(route.slice(7));pane='recipes';update()}else if(route){const selected=recipeById.get(route);if(selected)openRecipe(selected)}}
function renderFilterChips(){
 const filterCount=controls.filter(id=>id!=='q'&&$(id).value).length;$('filter-count').textContent=filterCount;$('filter-count').hidden=!filterCount;
 const labels={q:'搜索',author:'作者',camera:'相机',lens:'镜头',base:'优化校准'};const fragment=document.createDocumentFragment();
 for(const id of controls){const value=$(id).value;if(!value)continue;const chip=button(labels[id]+'：'+value,()=>{$(id).value='';update()},'filter-chip');chip.setAttribute('aria-label','移除'+labels[id]+'筛选：'+value);const cross=el('span','×');cross.setAttribute('aria-hidden','true');chip.append(cross);fragment.append(chip)}
 if(pane==='recipes'&&$('only-new').checked)fragment.append(button('本次同步新增 ×',()=>{$('only-new').checked=false;update()},'filter-chip'));
 $('active-filters').replaceChildren(fragment);
}
function setDensity(value){value=value==='compact'?'compact':'comfort';document.body.dataset.density=value;$('density-comfort').setAttribute('aria-pressed',String(value==='comfort'));$('density-compact').setAttribute('aria-pressed',String(value==='compact'));try{localStorage.setItem('nikon-library-density',value)}catch{}}
function viewerDialog(kind){return $(kind==='photo'?'photo-modal':'modal')}
function prepareViewer(kind,id,queue){
 const v=viewers[kind],dialog=viewerDialog(kind),all=kind==='photo'?library.captures.map(p=>p.id):rs.map(r=>r.id),visible=kind==='photo'?visiblePhotoIds:visibleRecipeIds;
 if(!dialog.open){const element=document.activeElement;element?.classList.toggle('pointer-focus-return',!keyboardNavigation);v.origin={x:scrollX,y:scrollY,element,keyboard:keyboardNavigation,parent:[...document.querySelectorAll('dialog[open]')].map(n=>({node:n,top:n.scrollTop}))};v.focus=false}
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
function bindViewerSwipe(kind){
 const dialog=viewerDialog(kind);let gesture=null,suppressClickUntil=0;
 const ready=()=>dialog.open&&(window.visualViewport?.scale||1)<=1.01;
 dialog.addEventListener('pointerdown',event=>{
  suppressClickUntil=0;
  if(event.pointerType!=='touch'){gesture=null;return}
  const stage=event.target.closest?.('.detailimage');
  if(!event.isPrimary||!ready()||!stage){gesture=null;return}
  gesture={pointer:event.pointerId,id:viewers[kind].id,x:event.clientX,y:event.clientY,axis:null,moved:false,threshold:Math.max(48,stage.clientWidth*.12)};
 });
 function movement(event){
  if(!gesture||gesture.pointer!==event.pointerId)return null;
  const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y,ax=Math.abs(dx),ay=Math.abs(dy);
  if(Math.max(ax,ay)>10){
   gesture.moved=true;
   if(!gesture.axis){if(ax>ay*1.25)gesture.axis='x';else if(ay>ax*1.25)gesture.axis='y'}
  }
  return {dx,ax,ay};
 }
 dialog.addEventListener('pointermove',movement,{passive:true});
 dialog.addEventListener('pointerup',event=>{
  const motion=movement(event);if(!motion)return;
  const current=gesture;gesture=null;
  // A drag must not become the image click that toggles the viewing mode.
  if(current.moved)suppressClickUntil=performance.now()+700;
  if(!ready()||current.id!==viewers[kind].id||current.axis!=='x'||motion.ax<current.threshold||motion.ax<=motion.ay*1.5)return;
  event.preventDefault();navigateViewer(kind,motion.dx<0?1:-1);
 });
 dialog.addEventListener('pointercancel',()=>{gesture=null});
 dialog.addEventListener('click',event=>{
  if(performance.now()>suppressClickUntil)return;
  suppressClickUntil=0;event.preventDefault();event.stopImmediatePropagation();
 },true);
 dialog.addEventListener('close',()=>{gesture=null;suppressClickUntil=0});
}
for(const kind of['recipe','photo']){
 bindViewerSwipe(kind);
 viewerDialog(kind).addEventListener('focusin',event=>{if(event.target.matches('.viewer-topnav button'))event.target.classList.toggle('pointer-focus-return',!keyboardNavigation)});
 $(kind+'-prev').onclick=()=>navigateViewer(kind,-1);$(kind+'-next').onclick=()=>navigateViewer(kind,1);$(kind+'-focus').onclick=()=>toggleFocus(kind);
 viewerDialog(kind).addEventListener('close',()=>{const v=viewers[kind],origin=v.origin;v.focus=false;viewerDialog(kind).classList.remove('is-focused');requestAnimationFrame(()=>{if(origin){for(const parent of origin.parent)if(parent.node.open)parent.node.scrollTop=parent.top;if(origin.element?.isConnected){origin.element.classList.toggle('pointer-focus-return',!origin.keyboard);origin.element.focus({preventScroll:true})}window.scrollTo(origin.x,origin.y)}})});
}
$('density-comfort').onclick=()=>setDensity('comfort');$('density-compact').onclick=()=>setDensity('compact');try{setDensity(localStorage.getItem('nikon-library-density'))}catch{setDensity('comfort')}
function openShortcuts(){const menu=$('utility-menu');if(menu.contains(document.activeElement))menu.querySelector('summary').focus({preventScroll:true});menu.open=false;$('shortcuts').showModal()}
$('shortcut-help').onclick=openShortcuts;$('shortcuts-close').onclick=()=>$('shortcuts').close();
document.addEventListener('keydown',event=>{
 if(event.ctrlKey||event.metaKey||event.altKey||event.target.closest?.('input,select,textarea,[contenteditable="true"]'))return;
 const key=event.key.toLowerCase();if(event.key==='?'&&!$('shortcuts').open){event.preventDefault();openShortcuts();return}
 if($('shortcuts').open||$('author-picker').open)return;
 const kind=$('photo-modal').open?'photo':$('modal').open?'recipe':null;
 if(kind){if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();navigateViewer(kind,event.key==='ArrowLeft'?-1:1)}else if(key==='f'){event.preventDefault();toggleFocus(kind)}}else if(event.key==='/'){event.preventDefault();$('q').focus();$('q').select()}
});
document.addEventListener('click',event=>{if(!event.target.closest('#utility-menu'))$('utility-menu').open=false});
function problemItem(issue){const item=el('div',undefined,'problem-item');item.append(el('strong',issue.file||'照片'),el('p',issue.message),el('p',issue.action,'muted'));if(issue.detail){const details=el('details');details.append(el('summary','技术详情'),el('p',issue.detail));item.append(details)}return item}
function assignmentEditor(photo){
 const section=el('details',undefined,'assignment');section.open=!photo.recipeId;
 section.append(el('summary',photo.recipeId?'手动修改关联':'手动关联方案'));
 const searchLabel=el('label','搜索方案');searchLabel.htmlFor='photo-assignment-search';
 const search=el('input');search.id='photo-assignment-search';search.type='search';search.placeholder='方案名称或作者…';search.value=assignmentQueries.get(photo.id)||'';
 const label=el('label','选择方案');label.htmlFor='photo-assignment';const select=el('select');select.id='photo-assignment';
 const status=el('p','没有找到符合条件的方案，请换个关键词。','muted');status.setAttribute('aria-live','polite');
 const current=recipeById.get(photo.recipeId),automatic=recipeById.get(photo.autoRecipeId),selected=photo.association==='manual'?(photo.recipeId||'__none__'):'__auto__';
 function addOption(value,text,disabled=false){const option=el('option',text);option.value=value;option.disabled=disabled;select.append(option)}
 function render(){
  const terms=search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches=rs.filter(r=>terms.every(term=>[r.name,...r.authors,r.pictureControlName].join(' ').toLocaleLowerCase().includes(term)));
  select.replaceChildren();
  addOption('__auto__',automatic?'自动匹配：'+automatic.name:photo.association==='manual'?'恢复自动识别':'未自动匹配，请选择方案');
  addOption('__none__','暂不关联');
  if(current&&!matches.some(r=>r.id===current.id))addOption(current.id,'当前关联：'+current.name+' · '+current.authors.join(' / '));
  for(const r of matches)addOption(r.id,r.name+' · '+r.authors.join(' / '));
  select.value=selected;status.hidden=matches.length>0;
 }
 search.addEventListener('input',()=>{assignmentQueries.set(photo.id,search.value);render()});
 select.addEventListener('change',()=>{assignmentQueries.delete(photo.id);mutate(select.value==='__auto__'?{action:'reset_assignment',id:photo.id}:{action:'assign_photo',id:photo.id,recipeId:select.value==='__none__'?null:select.value})});
 section.append(searchLabel,search,label,select,status);render();return section;
}
function renderCatalogStatus(status){catalogBusy=!!status.running;renderWebsiteStatus();$('sync-catalog').disabled=catalogBusy||!!websiteStatus.running;$('sync-catalog').textContent=status.running?'正在同步…':'同步云创';$('catalog-sync-status').hidden=!catalogStatusRequested;$('catalog-sync-status').textContent='云创方案：'+(status.state==='idle'?'尚未开始同步':status.message);$('catalog-sync-error').hidden=!catalogStatusRequested||!status.error;$('catalog-sync-error').textContent=status.error?'原因：'+status.error+'。请检查网络后点击「同步云创」重试；不会自动重试。':'';if(status.state==='succeeded'&&status.result?.revision!==catalogRevision&&!catalogLoading)loadCatalog().catch(error=>{$('catalog-sync-error').hidden=!catalogStatusRequested;$('catalog-sync-error').textContent='目录已更新，但页面读取失败：'+error.message+'。请刷新页面。'})}
async function loadCatalog(){catalogLoading=true;try{const next=await api('/api/catalog');if(!Array.isArray(next.recipes)||!Array.isArray(next.creators))throw new Error('目录数据格式无效');rs.splice(0,rs.length,...next.recipes);creators.splice(0,creators.length,...next.creators);recipeById.clear();for(const r of rs)recipeById.set(r.id,r);Object.assign(data,{report:next.report,labels:next.labels,values:next.values});newRecipeIds=new Set(next.syncMetadata?.newRecipeIds||[]);catalogRevision=next.syncMetadata?.revision||'';rebuildAuthors();populateFilters();update();$('stamp').textContent='官方目录时间：'+(next.syncMetadata?.syncedAt||next.report.finishedAt||'尚未同步');acceptState(library);if(next.sync)renderCatalogStatus(next.sync);if($('modal').open&&recipeById.has(activeRecipe)){const top=$('modal').scrollTop;openRecipe(recipeById.get(activeRecipe));$('modal').scrollTop=top}if($('photo-modal').open)openPhoto(activePhoto)}finally{catalogLoading=false}}
$('sync-catalog').onclick=async()=>{catalogStatusRequested=true;$('catalog-sync-status').hidden=false;$('catalog-sync-status').textContent='云创方案：正在开始同步…';$('sync-catalog').disabled=true;try{renderCatalogStatus(await api('/api/catalog/sync',{}))}catch(error){$('catalog-sync-error').hidden=false;$('catalog-sync-error').textContent='无法开始同步：'+error.message;$('sync-catalog').disabled=false}};
$('stamp').textContent='官方目录时间：'+(data.report.finishedAt||'尚未同步');update();window.addEventListener('hashchange',applyRoute);(async()=>{try{await loadCatalog()}catch(error){$('catalog-sync-error').hidden=false;$('catalog-sync-error').textContent='读取当前目录失败：'+error.message+'。请刷新页面。'}await refresh();applyRoute()})();setInterval(()=>{if(!document.hidden)refresh()},3000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});

// Menus close without losing the browsing position or keyboard focus.
document.addEventListener('click',event=>{if(!event.target.closest('#filter-menu')&&!event.target.closest('#author-picker'))$('filter-menu').open=false});
document.addEventListener('keydown',event=>{if(event.key!=='Escape'||document.querySelector('dialog[open]'))return;for(const id of['filter-menu','utility-menu']){const menu=$(id);if(menu.open){menu.open=false;menu.querySelector('summary').focus();event.preventDefault();break}}});
