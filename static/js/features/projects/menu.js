// Project menu and submenu presentation.

// -------------------- projects --------------------
const PROJECT_MENU_GAP=5;
$('#brandButton').addEventListener('click', e => { e.stopPropagation(); toggleProjectMenu(); });
$('#projectImportInput').onchange = event=>importProjectPackage(event);
$('#projectJsonImportInput').onchange = event=>importJson(event);
$('#cutsImportInput').onchange = event=>importCutsPackage(event);

function hideProjectSubmenu(){ const submenu=$('#projectSubmenu'); submenu.classList.add('hidden'); submenu.innerHTML=''; }
function hideProjectMenu(){ $('#projectMenu').classList.add('hidden'); hideProjectSubmenu(); }
function projectMenuIsOpen(){ return !$('#projectMenu').classList.contains('hidden'); }
function activeWorkspaceProject(){ return state.workspace?.projects?.find(p=>p.id===state.workspace.active_project_id)||null; }
function projectMenuButton(id,label,{submenu=false,danger=false}={}){return `<button id="${id}" class="project-menu-item${danger?' danger':''}" type="button"><span>${escapeHtml(i18nSource(label))}</span>${submenu?'<span class="project-menu-caret" aria-hidden="true">›</span>':''}</button>`}

function toggleProjectMenu(){ if(projectMenuIsOpen()){hideProjectMenu();return} renderProjectMenu(); positionProjectMenu(); }
function positionProjectMenu(){
  const menu=$('#projectMenu'),button=$('#brandButton'),r=button.getBoundingClientRect();
  menu.classList.remove('hidden');
  const width=menu.offsetWidth||250;let left=Math.max(6,Math.min(innerWidth-width-6,r.left));
  menu.style.left=`${Math.round(left)}px`;menu.style.top=`${Math.round(r.bottom+PROJECT_MENU_GAP)}px`;
}
function renderProjectMenu(){
  const active=activeWorkspaceProject(),others=(state.workspace?.projects||[]).filter(p=>p.id!==state.workspace?.active_project_id);
  const menu=$('#projectMenu');
  menu.innerHTML=`
    <div class="project-menu-current">
      <div class="project-menu-current-copy"><small>{{i18n:dynamic.html.text.small.4f798f98}}</small><b id="projectMenuProjectName" title="${escapeHtml(active?.name||'')}">${escapeHtml(active?.name||i18nSource('No project'))}</b></div>
      ${active?'<button id="projectMenuRename" class="project-menu-pencil" type="button" title="{{i18n:dynamic.html.attr.title.10f1c39b}}" aria-label="{{i18n:dynamic.html.attr.aria.label.e02cb6a7}}">✎</button>':''}
    </div>
    <div class="context-sep"></div>
    ${projectMenuButton('projectMenuNew','New Project…')}
    ${projectMenuButton('projectMenuSwitch',others.length?'Switch Project':'Switch Project',{submenu:true})}
    ${projectMenuButton('projectMenuImport','Import',{submenu:true})}
    ${projectMenuButton('projectMenuExport','Export',{submenu:true})}
    <div class="context-sep"></div>
    ${projectMenuButton('projectMenuSettings','Project Settings…')}
    ${projectMenuButton('projectMenuLanguage',i18nText('settings.language.row_label'))}
  `;
  $('#projectMenuRename')?.addEventListener('click',e=>{e.stopPropagation();startProjectMenuRename()});
  $('#projectMenuNew').onclick=()=>{hideProjectMenu();createProjectDialog()};
  wireProjectSubmenuButton($('#projectMenuSwitch'),'switch');
  wireProjectSubmenuButton($('#projectMenuImport'),'import');
  wireProjectSubmenuButton($('#projectMenuExport'),'export');
  $('#projectMenuSettings').onclick=()=>{hideProjectMenu();openProjectSettings()};
  $('#projectMenuLanguage').onclick=()=>{hideProjectMenu();openLanguageSettings()};
}
function wireProjectSubmenuButton(button,kind){
  if(!button)return;const open=e=>{e?.preventDefault();showProjectSubmenu(kind,button)};
  button.addEventListener('mouseenter',open);button.addEventListener('click',open);
}
function showProjectSubmenu(kind,anchor){
  const submenu=$('#projectSubmenu'),active=activeWorkspaceProject(),projects=state.workspace?.projects||[];let html='';
  if(kind==='switch'){
    const others=projects.filter(p=>p.id!==state.workspace.active_project_id).slice(0,8);
    html=others.map(p=>`<button class="project-menu-item" type="button" data-switch-project="${p.id}"><span class="project-menu-project-label">${escapeHtml(p.name)}</span></button>`).join('');
    if(!others.length)html='<div class="project-menu-empty">{{i18n:dynamic.html.text.div.40d28fdc}}</div>';
    html+=`<div class="context-sep"></div><button id="projectMenuBrowseAll" class="project-menu-item" type="button">{{i18n:dynamic.html.text.button.5cf51671}}</button>`;
  }else if(kind==='import'){
    html=`${projectMenuButton('projectMenuImportManuscript','Manuscript…')}${projectMenuButton('projectMenuImportJsonProject','Import JSON…')}${projectMenuButton('projectMenuImportProject','Breadmaker Project…')}${projectMenuButton('projectMenuImportCuts','Cuts…')}`;
  }else if(kind==='export'){
    html=`${projectMenuButton('projectMenuExportDocx','Manuscript as DOCX')}${projectMenuButton('projectMenuExportPdf','Manuscript as PDF…')}${projectMenuButton('projectMenuExportJsonProject','JSON Project')}${projectMenuButton('projectMenuExportProject','Breadmaker Project')}${projectMenuButton('projectMenuExportCuts','Cuts')}`;
  }
  submenu.innerHTML=html;submenu.classList.remove('hidden');
  const r=anchor.getBoundingClientRect(),width=submenu.offsetWidth||220;let left=r.right+PROJECT_MENU_GAP;if(left+width>innerWidth-6)left=Math.max(6,r.left-width-PROJECT_MENU_GAP);
  submenu.style.left=`${Math.round(left)}px`;submenu.style.top=`${Math.round(Math.max(6,Math.min(innerHeight-submenu.offsetHeight-6,r.top-4)))}px`;
  if(kind==='switch'){
    $$('[data-switch-project]',submenu).forEach(button=>button.onclick=async()=>{hideProjectMenu();await flushSave();await api(`/api/projects/${button.dataset.switchProject}/activate`,{method:'POST'});openWorkspaceProject(button.dataset.switchProject)});
    $('#projectMenuBrowseAll').onclick=()=>{hideProjectMenu();openAllProjects()};
  }else if(kind==='import'){
    $('#projectMenuImportManuscript').onclick=()=>{hideProjectMenu();$('#importInput').click()};
    $('#projectMenuImportJsonProject').onclick=()=>{hideProjectMenu();beginJsonImport()};
    $('#projectMenuImportProject').onclick=()=>{hideProjectMenu();$('#projectImportInput').click()};
    $('#projectMenuImportCuts').onclick=()=>{hideProjectMenu();$('#cutsImportInput').click()};
  }else if(kind==='export'){
    $('#projectMenuExportDocx').onclick=async()=>{hideProjectMenu();await flushSave();location.href=projectScopedUrl('/api/export/docx')};
    $('#projectMenuExportPdf').onclick=()=>{hideProjectMenu();openPdfExport()};
    $('#projectMenuExportJsonProject').onclick=async()=>{hideProjectMenu();if(active){await flushSave();location.href=projectScopedUrl(`/api/projects/${active.id}/export-json`)}};
    $('#projectMenuExportProject').onclick=async()=>{hideProjectMenu();if(active){await flushSave();location.href=projectScopedUrl(`/api/projects/${active.id}/export`)}};
    $('#projectMenuExportCuts').onclick=()=>{hideProjectMenu();location.href=projectScopedUrl('/api/cuts/export')};
  }
}
async function startProjectMenuRename(){
  const active=activeWorkspaceProject(),holder=$('.project-menu-current-copy');if(!active||!holder)return;
  const original=active.name;holder.innerHTML=`<small>{{i18n:dynamic.html.text.small.4f798f98}}</small><input id="projectMenuRenameInput" class="project-menu-rename-input" value="${escapeHtml(original)}" aria-label="{{i18n:dynamic.html.attr.aria.label.ac5d3eaf}}">`;
  $('#projectMenuRename')?.classList.add('hidden');const input=$('#projectMenuRenameInput');let finished=false;
  const finish=async save=>{if(finished)return;finished=true;const name=input.value.trim();if(save&&name&&name!==original){try{await api(`/api/projects/${active.id}`,{method:'PATCH',body:JSON.stringify({name})});active.name=name;document.title=name}catch(error){toast(error.message||'Could not rename project',4000)}}renderProjectMenu();positionProjectMenu()};
  input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();finish(true)}else if(e.key==='Escape'){e.preventDefault();finish(false)}});
  input.addEventListener('blur',()=>finish(true));queueMicrotask(()=>{input.focus();input.select()});
}
