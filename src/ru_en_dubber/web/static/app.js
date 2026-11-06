const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const state = {token:'', settings:[], channels:[], formChannel:null, formVideo:null, runs:[], selected:new Set(), route:0, lang:'en'};

// --- Lightweight i18n: locale JSON files, English default, offline-friendly ---
const LOCALES = {};
let L = {status:{}, script_status:{}, choices:{}, groups:{}, stages:{}, labels:{}, ui:{}, date_locale:'en-US'};
async function loadLocale(lang) {
  if (!LOCALES[lang]) {
    const response = await fetch('/i18n-'+lang+'.json');
    if (!response.ok) throw new Error('locale '+lang+' unavailable');
    LOCALES[lang] = await response.json();
  }
  return LOCALES[lang];
}
function t(key, vars) {
  let value = L;
  for (const part of key.split('.')) value = value && value[part];
  if (value === undefined || value === null) return key;
  if (typeof value === 'string' && vars) for (const [name, replacement] of Object.entries(vars)) value = value.replaceAll('{'+name+'}', replacement);
  return value;
}
const stat = (s) => L.status[s] || s;
const sstat = (s) => L.script_status[s] || s;
function applyStatic() {
  document.documentElement.lang = L.lang;
  document.title = L.doc_title || document.title;
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll('[data-i18n-html]')) node.innerHTML = t(node.dataset.i18nHtml);
  for (const node of document.querySelectorAll('[data-i18n-ph]')) node.placeholder = t(node.dataset.i18nPh);
  for (const node of document.querySelectorAll('[data-i18n-aria]')) node.setAttribute('aria-label', t(node.dataset.i18nAria));
  for (const node of document.querySelectorAll('[data-i18n-title]')) node.title = t(node.dataset.i18nTitle);
}
async function setLang(lang) {
  L = await loadLocale(lang);
  state.lang = lang;
  try { localStorage.setItem('dubber-lang', lang); } catch { /* ignore */ }
  $('lang-select').value = lang;
  applyStatic();
  if (state.settings.length) { const options = readOptions(); renderSettings(); applyOptions(options); modeChanged(); }
  renderProgress();
  await route();
}

function error(message) {$('error').hidden=!message; $('error').textContent=message||'';}
async function api(path, data) {
  const init = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':state.token}, body:JSON.stringify(data)};
  const response = await fetch('/api/v1/'+path, init);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || t('ui.api_error'));
  return value;
}
function duration(value, precise=false) {const seconds=Math.max(0,Number(value)||0); return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`+(precise?'.'+String(Math.floor((seconds%1)*1000)).padStart(3,'0'):'');}
function date(value) {return new Date(typeof value==='number'?value*1000:value).toLocaleString(L.date_locale,{dateStyle:'short',timeStyle:'short'});}
function makeProgress(value) {const node=el('progress'); node.max=1; if(value!==null&&value!==undefined)node.value=value; node.setAttribute('aria-label',t('ui.progress_aria')); return node;}
function idNote(value) {return el('small',`${t('ui.id_prefix')}${value}`,'entity-id');}

function readOptions() {
  const options={};
  for(const field of state.settings){const input=$('setting-'+field.name); let value=field.kind==='boolean'?input.checked:field.multiple?Array.from(input.selectedOptions,o=>o.value):input.value; if(value==='')continue; if(!field.multiple&&['number','integer'].includes(field.kind))value=Number(value); options[field.name]=value;}
  return options;
}
function applyOptions(options={}) {
  for(const field of state.settings){const input=$('setting-'+field.name); const value=Object.hasOwn(options,field.name)?options[field.name]:field.default; if(field.kind==='boolean')input.checked=!!value; else if(field.multiple)for(const option of input.options)option.selected=(value||[]).includes(option.value); else input.value=value??'';}
}
function renderSettings() {
  $('settings').replaceChildren();
  const groups=new Map();
  for(const field of state.settings){
    if(!groups.has(field.group)){const section=el('details'); section.open=field.group==='general'; section.append(el('summary',L.groups[field.group]||field.group)); const fields=el('div',undefined,'fields'); section.append(fields); $('settings').append(section); groups.set(field.group,fields);}
    const label=el('label'); label.dataset.search=[L.labels[field.name],field.name,field.help].join(' ').toLowerCase(); const input=el(field.choices?'select':'input'); input.id='setting-'+field.name;
    if(field.choices){if(!field.multiple&&field.default===null){const empty=el('option',field.name==='visual_media_type'?t('ui.settings_media_default'):t('ui.settings_auto_unset')); empty.value=''; input.append(empty);} for(const choice of field.choices){const option=el('option',L.choices[choice]||String(choice));option.value=choice;input.append(option);} if(field.multiple){input.multiple=true;input.className='multiple';}}
    else if(field.kind==='boolean'){input.type='checkbox';label.className='check-field';} else {input.type=['number','integer'].includes(field.kind)?'number':'text';if(input.type==='number')input.step=field.kind==='integer'?'1':'any';}
    if(field.minimum!==null)input.min=field.minimum;if(field.maximum!==null)input.max=field.maximum;label.append(el('span',L.labels[field.name]||field.flag),input);input.title=field.help||'';if(field.multiple)label.append(el('small',t('ui.settings_multi_hint')));groups.get(field.group).append(label);
  }
  $('settings-count').textContent=t('ui.settings_count',{n:state.settings.length});applyOptions();
}

async function loadChannels() {
  const data=await api('channels'); state.channels=data.channels; $('channel-warnings').textContent=data.warnings.join('. '); renderChannels(); renderChannelSelect();
}
function renderChannelSelect() {
  const select=$('channel-select'), previous=select.value; select.replaceChildren();
  if(!state.channels.length){const option=el('option',t('ui.channel_select_empty'));option.value='';select.append(option);select.disabled=true;return;}
  select.disabled=false; for(const channel of state.channels){const option=el('option',channel.display_name);option.value=channel.id;select.append(option);} if(state.channels.some(c=>c.id===previous))select.value=previous; loadFormChannel().catch(e=>error(e.message));
}
async function loadFormChannel() {
  const channelId=$('channel-select').value; if(!channelId){state.formChannel=null;return;} state.formChannel=await api('channels/'+encodeURIComponent(channelId));
  populateFormVideos();
  await loadFormVideo();
}
function populateFormVideos() {
  const select=$('existing-video'),previous=select.value,prepared=$('mode').value==='script';select.replaceChildren();if(!state.formChannel)return;
  const videos=prepared?state.formChannel.videos.filter(video=>video.has_script&&video.script_status==='ready'&&video.script_is_current):state.formChannel.videos;
  if(!videos.length){const option=el('option',prepared?t('ui.videos_no_ready_scripts'):t('ui.videos_none'));option.value='';select.append(option);}
  for(const video of videos){const suffix=prepared?` · ${t('ui.words',{n:video.script_word_count})}`:'';const option=el('option',video.working_title+suffix);option.value=video.id;select.append(option);}if(videos.some(video=>video.id===previous))select.value=previous;
}
async function loadFormVideo() {
  const channelId=$('channel-select').value, videoId=$('existing-video').value; const select=$('existing-run'); select.replaceChildren(); state.formVideo=null;
  const readiness=$('script-readiness');readiness.hidden=true;readiness.textContent='';if(!channelId||!videoId)return; state.formVideo=await api(`channels/${encodeURIComponent(channelId)}/videos/${encodeURIComponent(videoId)}`);
  if($('mode').value==='script'){readiness.hidden=false;readiness.textContent=`${sstat(state.formVideo.script_status)} · ${t('ui.words',{n:state.formVideo.script_word_count})} · ${state.formVideo.script_file}`;}
  for(const run of state.formVideo.runs){const option=el('option',`${date(run.created_at)} · ${stat(run.status)}`);option.value=run.id;select.append(option);}
}
function renderChannels() {
  const box=$('channel-list'); box.replaceChildren(); const query=$('channel-search').value.trim().toLowerCase(); const channels=state.channels.filter(channel=>`${channel.display_name} ${channel.id}`.toLowerCase().includes(query));
  if(!channels.length){const empty=el('div',undefined,'panel empty');empty.append(el('h3',state.channels.length?t('ui.channels_not_found'):t('ui.channels_create_first')),el('p',state.channels.length?t('ui.channels_change_query'):t('ui.channels_after_create')));box.append(empty);return;}
  for(const channel of channels){const card=el('a',undefined,'job-card');card.href='#channel/'+encodeURIComponent(channel.id);const body=el('div');body.append(el('h2',channel.display_name),idNote(channel.id));const meta=el('div',undefined,'job-meta');meta.append(el('span',t('ui.meta_videos',{n:channel.video_count})),el('span',t('ui.meta_competitors',{n:channel.competitor_count})),el('span',t('ui.meta_playbooks',{n:channel.playbook_count})),el('span',t('ui.meta_updated',{date:date(channel.updated_at)})));body.append(meta);card.append(body,el('span',t('ui.open'),'badge'));box.append(card);}
}
function renderChannel(channel) {
  const box=$('channel-content');box.replaceChildren();const heading=el('div',undefined,'job-heading');const title=el('div');title.append(el('h2',channel.display_name),idNote(channel.id),el('p',channel.path));heading.append(title,el('span',t('ui.meta_videos',{n:channel.video_count}),'badge'));box.append(heading);
  const stats=el('section',undefined,'panel stat-grid');for(const [number,label] of [[channel.video_count,t('ui.stat_videos')],[channel.competitor_count,t('ui.stat_competitors')],[channel.playbook_count,t('ui.stat_playbooks')]]){const item=el('div');item.append(el('strong',String(number)),el('span',label));stats.append(item);}box.append(stats);
  const panel=el('section',undefined,'panel');panel.append(el('h2',t('ui.ch_videos_heading'))); if(!channel.videos.length)panel.append(el('p',t('ui.ch_no_videos')));
  for(const video of channel.videos){const card=el('a',undefined,'job-card');card.href=`#video/${encodeURIComponent(channel.id)}/${encodeURIComponent(video.id)}`;const body=el('div');body.append(el('h2',video.working_title),idNote(video.id));const meta=el('div',undefined,'job-meta');const scriptLabel=!video.has_script?t('ui.script_none'):video.script_status==='ready'&&!video.script_is_current?t('ui.script_changed'):`${sstat(video.script_status)} · ${t('ui.words',{n:video.script_word_count})}`;meta.append(el('span',t('ui.segments_count',{n:video.run_count})),el('span',scriptLabel),el('span',date(video.updated_at)),el('span',video.folder_name));body.append(meta);card.append(body,el('span',stat(video.status),'badge '+video.status));panel.append(card);}box.append(panel);
  const knowledge=el('section',undefined,'panel');knowledge.append(el('h2',t('ui.knowledge_title')));const rows=el('div',undefined,'knowledge-grid');for(const [name,items] of [[t('ui.knowledge_competitors'),channel.competitors],[t('ui.knowledge_playbooks'),channel.playbooks]]){const block=el('div');block.append(el('h3',name));if(items.length)for(const item of items)block.append(el('p',item.folder_name));else block.append(el('p',t('ui.knowledge_empty'),'muted'));rows.append(block);}knowledge.append(rows);box.append(knowledge);
}
function renderVideo(video) {
  const box=$('video-content');box.replaceChildren();$('video-back').href='#channel/'+encodeURIComponent(video.channel_id);const heading=el('div',undefined,'job-heading');const title=el('div');title.append(el('h2',video.working_title),idNote(video.id),el('p',video.path),el('p',video.has_script?t('ui.video_script_line',{status:sstat(video.script_status),n:video.script_word_count}):t('ui.video_no_script')));heading.append(title,el('span',stat(video.status),'badge '+video.status));box.append(heading);
  const panel=el('section',undefined,'panel');panel.append(el('h2',t('ui.run_history')));if(!video.runs.length)panel.append(el('p',t('ui.run_no_manifest')));
  for(const run of video.runs){const card=el('a',undefined,'job-card');card.href=`#run/${encodeURIComponent(video.channel_id)}/${encodeURIComponent(video.id)}/${encodeURIComponent(run.id)}`;const body=el('div');body.append(el('h2',date(run.created_at)),idNote(run.id));const meta=el('div',undefined,'job-meta');meta.append(el('span',t('ui.segments_count',{n:run.segments})),el('span',run.has_export?t('ui.export_ready'):t('ui.export_intermediate')));body.append(meta);card.append(body,el('span',stat(run.status),'badge '+run.status));panel.append(card);}box.append(panel);
}

function renderProgress() {
  const run=state.runs[0], box=$('progress-content');box.replaceChildren();
  if(!run){const empty=el('div',undefined,'empty');empty.append(el('h3',t('ui.empty_ready_title')),el('p',t('ui.empty_ready_text_form')));box.append(empty);$('launch').disabled=!state.channels.length;return;}
  const progress=run.progress;$('run-status').textContent=stat(progress.status);box.append(el('strong',run.video_title),el('small',`${run.channel_name} · ${date(run.created_at)}`),idNote(run.run_id));const percent=el('div',String(Math.floor((progress.overall||0)*100)),'percent');percent.append(el('small',' %'));box.append(percent,makeProgress(progress.overall||0),el('p',t('ui.progress_stage_note'),'progress-note'));
  for(const stage of progress.stages||[]){const row=el('div',undefined,'stage '+stage.status),heading=el('div',undefined,'stage-title');heading.append(el('span',`${stage.index}. ${L.stages[stage.name]||stage.label||stage.name}`),el('span',stage.fraction===null?'…':`${Math.floor(stage.fraction*100)}%`));row.append(heading,el('p',stage.message));if(stage.status==='running')row.append(makeProgress(stage.fraction));box.append(row);}
  if(progress.error)box.append(el('div',progress.error,'error'));const link=el('a',t('ui.open_run'),'run-link');link.href=`#run/${encodeURIComponent(run.channel_id)}/${encodeURIComponent(run.video_id)}/${encodeURIComponent(run.run_id)}`;box.append(link);$('launch').disabled=!state.channels.length||state.runs.some(item=>['running','starting','untracked'].includes(item.progress.status));
}

function playAsset(asset,start=0){const video=$('video-player'),audio=$('audio-player');video.pause();audio.pause();video.hidden=asset.kind!=='video';audio.hidden=asset.kind!=='audio';const player=asset.kind==='video'?video:audio;player.src=asset.url;player.onloadedmetadata=()=>{if(start)player.currentTime=start;};$('asset-title').textContent=asset.path;$('asset-hint').textContent=asset.kind==='video'?t('ui.asset_hint_video'):t('ui.asset_hint_audio');for(const button of document.querySelectorAll('.asset-list button'))button.classList.toggle('active',button.dataset.path===asset.path);}
function renderRun(run){
  state.selected.clear();const box=$('job-content');box.replaceChildren();$('run-back').href=`#video/${encodeURIComponent(run.channel_id)}/${encodeURIComponent(run.video_id)}`;const heading=el('div',undefined,'job-heading'),text=el('div');text.append(el('h2',run.video_title),el('p',run.source),idNote(run.id));const repeat=el('button',t('ui.configure_rerun'));repeat.onclick=async()=>{$('channel-select').value=run.channel_id;await loadFormChannel();$('existing-video').value=run.video_id;await loadFormVideo();$('existing-run').value=run.id;$('mode').value=run.has_export?'export':'resume';modeChanged();location.hash='pipeline';};text.append(repeat);heading.append(text,el('span',stat(run.status),'badge '+run.status));box.append(heading);if(run.error)box.append(el('div',run.error,'error'));
  if(run.no_safe_match_count)box.append(el('div',t('ui.no_safe_match',{n:run.no_safe_match_count}),'error'));
  const narration=run.media.find(item=>item.path==='narration.wav');if(narration){const download=el('a',t('ui.download_narration'),'run-link');download.href=narration.url;download.download='narration.wav';box.append(download);}
  const subtitles=run.media.find(item=>item.path==='narration.srt');if(subtitles){const download=el('a',t('ui.download_subs'),'run-link');download.href=subtitles.url;download.download='narration.srt';box.append(download);}
  const layout=el('div',undefined,'job-layout'),preview=el('section',undefined,'panel player-panel');preview.append(el('h2',t('ui.preview_title')));const video=el('video');video.id='video-player';video.controls=true;video.preload='metadata';const audio=el('audio');audio.id='audio-player';audio.controls=true;audio.preload='none';audio.hidden=true;const title=el('p',t('ui.select_file_right'));title.id='asset-title';const hint=el('p',t('ui.codec_hint'));hint.id='asset-hint';preview.append(video,audio,title,hint);
  const assets=el('section',undefined,'panel');assets.append(el('h2',t('ui.media_count',{n:run.media.length})));const assetList=el('div',undefined,'asset-list');for(const asset of run.media){const button=asset.kind==='subtitle'?el('a',asset.name):el('button',asset.name);button.dataset.path=asset.path;button.append(el('small',`${(asset.bytes/1048576).toFixed(1)} MB · ${asset.path}`));if(asset.kind==='subtitle'){button.href=asset.url;button.download=asset.name;}else button.onclick=()=>playAsset(asset);assetList.append(button);}if(!run.media.length)assetList.append(el('p',t('ui.no_media')));assets.append(assetList);layout.append(preview,assets);box.append(layout);
  if(run.output_mode!=='audio'){const episodes=el('section',undefined,'panel');episodes.append(el('h2',t('ui.episode_plan',{n:run.episodes.length})));const list=el('div',undefined,'episode-list');for(const episode of run.episodes){const row=el('div',undefined,'episode-row'),body=el('div');body.append(el('h3',`${episode.index}. ${episode.topic||t('ui.episode_word')}`),el('p',episode.subject||episode.visual_intent||''),el('small',`${duration(episode.timeline_start,true)}–${duration(episode.timeline_end,true)}`));row.append(body);list.append(row);}if(!run.episodes.length)list.append(el('p',t('ui.no_episode_plan')));episodes.append(list);box.append(episodes);}
  if(run.visual_beats?.length){const visuals=el('section',undefined,'panel');visuals.append(el('h2',t('ui.visuals_count',{n:run.visual_beats.length})));const beats=el('div',undefined,'episode-list');for(const beat of run.visual_beats){const row=el('div',undefined,'episode-row'),info=el('div');const status=beat.status==='matched'?t('ui.status_matched'):beat.status==='no_safe_match'?t('ui.status_no_safe'):beat.status;info.append(el('h3',`#${beat.index} · ${duration(beat.start)}–${duration(beat.end)} · ${status}`),el('p',beat.narration||''));if(beat.asset)info.append(el('small',`${beat.asset.provider} · ${beat.asset.media_type} · ${beat.asset.page_url}`));row.append(info);beats.append(row);}visuals.append(beats);box.append(visuals);}
  const speech=el('section',undefined,'panel');speech.append(el('h2',t('ui.speech_count',{n:run.segments.length})));const speechList=el('div',undefined,'episode-list');for(const segment of run.segments){const row=el('div',undefined,'speech');row.append(el('small',`#${segment.id} · ${segment.speaker} · ${duration(segment.start)}–${duration(segment.end)}`),el('p',segment.en||t('ui.no_translation')));if(run.input_type!=='script')row.append(el('p',segment.ru,'muted'));const file=segment.dub_file||segment.tts_file,asset=run.media.find(item=>item.path===file);if(asset){const player=el('audio');player.controls=true;player.preload='none';player.src=asset.url;row.append(player);}speechList.append(row);}speech.append(speechList);box.append(speech);
  const first=narration||run.media.find(item=>item.kind==='video'&&item.path.includes('/video/'))||run.media.find(item=>item.kind==='video')||run.media.find(item=>item.kind==='audio');if(first)playAsset(first);else video.hidden=true;
}

function modeChanged(){const mode=$('mode').value,prepared=mode==='script',importing=mode==='script_import',scriptMode=prepared||importing,repeat=!['new','script','script_import'].includes(mode),needsVideo=prepared||repeat;$('video-title-label').hidden=!(mode==='new'||importing);$('video-title').required=mode==='new'||importing;$('existing-video-label').hidden=!needsVideo;$('existing-video-caption').textContent=prepared?t('ui.cap_prepared_video'):t('ui.cap_video');$('existing-run-label').hidden=!repeat;$('existing-video').required=needsVideo;$('existing-run').required=repeat;$('source-label').hidden=mode!=='new';$('source').required=mode==='new';$('script-label').hidden=!importing;$('script-file-label').hidden=!importing;$('script-output-label').hidden=!scriptMode;$('script-text').required=importing;const capcut=$('script-output').querySelector('[value="capcut"]');capcut.disabled=!prepared;if(!prepared&&$('script-output').value==='capcut')$('script-output').value='audio';const sceneSetting=$('setting-capcut_scene_seconds');if(sceneSetting){const active=prepared&&$('script-output').value==='capcut';sceneSetting.parentElement.hidden=!active;if(!active)sceneSetting.value='0';else sceneSetting.closest('details').open=true;}const videoMode=$('setting-video_mode');if(videoMode){if(scriptMode)videoMode.value='rebuild';videoMode.disabled=scriptMode;}const noTts=$('setting-no_tts');if(noTts){if(scriptMode)noTts.checked=false;noTts.disabled=scriptMode;}populateFormVideos();loadFormVideo().catch(problem=>error(problem.message));}
async function route(){const version=++state.route,hash=location.hash.slice(1)||'pipeline',parts=hash.split('/').map(decodeURIComponent);error('');for(const id of ['pipeline-view','channels-view','channel-view','video-view','run-view'])$(id).hidden=true;const isPipeline=parts[0]==='pipeline',isChannels=parts[0]==='channels';$(isPipeline?'pipeline-view':isChannels?'channels-view':parts[0]==='channel'?'channel-view':parts[0]==='video'?'video-view':'run-view').hidden=false;$('nav-pipeline').classList.toggle('active',isPipeline);$('nav-channels').classList.toggle('active',!isPipeline);$('page-title').textContent=isPipeline?t('ui.pipeline'):isChannels?t('ui.channels'):parts[0]==='channel'?t('ui.channel'):parts[0]==='video'?t('ui.video'):t('ui.run_materials');if(isChannels)await loadChannels();if(parts[0]==='channel'){const value=await api('channels/'+encodeURIComponent(parts[1]));if(version===state.route)renderChannel(value);}if(parts[0]==='video'){const value=await api(`channels/${encodeURIComponent(parts[1])}/videos/${encodeURIComponent(parts[2])}`);if(version===state.route)renderVideo(value);}if(parts[0]==='run'){const value=await api(`channels/${encodeURIComponent(parts[1])}/videos/${encodeURIComponent(parts[2])}/runs/${encodeURIComponent(parts[3])}`);if(version===state.route)renderRun(value);}}
async function poll(){try{const data=await api('runs');state.runs=data.runs;renderProgress();$('connection').textContent=t('ui.conn_connected');}catch{$('connection').textContent=t('ui.conn_lost');}finally{setTimeout(poll,1500);}}

$('lang-select').onchange=()=>setLang($('lang-select').value).catch(e=>error(e.message));
$('mode').onchange=modeChanged;$('script-output').onchange=modeChanged;$('channel-select').onchange=()=>loadFormChannel().catch(e=>error(e.message));$('existing-video').onchange=()=>loadFormVideo().catch(e=>error(e.message));
$('script-file').onchange=async()=>{error('');const file=$('script-file').files[0];if(!file)return;try{if(!/\.(txt|md)$/i.test(file.name))throw new Error(t('ui.pick_txt_md'));if(file.size>128000)throw new Error(t('ui.too_large'));const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());$('script-text').value=text.replace(/^﻿/,'');$('form-note').textContent=t('ui.loaded_file',{name:file.name});}catch(problem){$('script-file').value='';error(problem.message);}};
$('pipeline-form').onsubmit=async(event)=>{event.preventDefault();error('');$('launch').disabled=true;try{const mode=$('mode').value,payload={mode,channel_id:$('channel-select').value,options:readOptions()};if(['new','script_import'].includes(mode))payload.video_title=$('video-title').value.trim();if(mode==='script'||!['new','script_import'].includes(mode))payload.video_id=$('existing-video').value;if(!['new','script','script_import'].includes(mode))payload.run_id=$('existing-run').value;if(mode==='new')payload.source=$('source').value.trim();if(mode==='script_import')payload.script_text=$('script-text').value;if(['script','script_import'].includes(mode)){payload.output_mode=$('script-output').value;payload.options.video_mode='rebuild';payload.options.no_tts=false;if(['audio','capcut'].includes(payload.output_mode)){payload.options.no_premiere_export=false;payload.options.avatar_mode='off';payload.options.music_mode='off';}}const result=await api('runs',payload);$('form-note').textContent=t('ui.launched',{title:result.video_title});await loadChannels();state.runs=(await api('runs')).runs;renderProgress();}catch(problem){error(problem.message);$('launch').disabled=false;}};
$('channel-form').onsubmit=async(event)=>{event.preventDefault();error('');try{const channel=await api('channels',{display_name:$('new-channel-name').value.trim()});$('new-channel-name').value='';await loadChannels();location.hash='#channel/'+encodeURIComponent(channel.id);}catch(problem){error(problem.message);}};
$('save-preset').onclick=()=>{try{localStorage.setItem('dubber-settings-v1',JSON.stringify(readOptions()));$('form-note').textContent=t('ui.preset_saved');}catch(problem){error(t('ui.preset_save_failed',{error:problem.message}));}};$('load-preset').onclick=()=>{try{const saved=localStorage.getItem('dubber-settings-v1');if(!saved)throw new Error(t('ui.preset_save_first'));applyOptions(JSON.parse(saved));$('form-note').textContent=t('ui.preset_loaded');}catch(problem){error(problem.message);}};$('reset-preset').onclick=()=>{applyOptions();$('form-note').textContent=t('ui.preset_defaults');};
$('settings-search').oninput=()=>{const query=$('settings-search').value.toLowerCase();for(const section of $('settings').children){let count=0;for(const label of section.querySelectorAll('label')){label.hidden=!label.dataset.search.includes(query);if(!label.hidden)count++;}section.hidden=!count;if(query&&count)section.open=true;}};$('channel-search').oninput=renderChannels;$('refresh-channels').onclick=()=>loadChannels().catch(problem=>error(problem.message));window.addEventListener('hashchange',()=>route().catch(problem=>error(problem.message)));

let startLang='en';try{const saved=localStorage.getItem('dubber-lang');if(saved==='en'||saved==='ru')startLang=saved;}catch{/* ignore */}
L=await loadLocale(startLang);state.lang=startLang;$('lang-select').value=startLang;applyStatic();
try{const bootstrap=await api('bootstrap');state.token=bootstrap.token;state.settings=bootstrap.settings;$('workspace').textContent=bootstrap.root;renderSettings();modeChanged();await loadChannels();await route();poll();}catch(problem){error(problem.message);$('connection').textContent=t('ui.conn_none');}
