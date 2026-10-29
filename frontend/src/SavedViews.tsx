import { useEffect, useState } from 'react';
import { Button, Icon, Input, Popover, Select, Text } from '@particle-academy/react-fancy';
import type { PersonalViewsController, ViewsState } from './personal-views.js';

export function SavedViews({ state, controller, error, run }: { state: ViewsState; controller: PersonalViewsController | null;
  error: string; run: (operation: () => Promise<void>) => void }) {
  const [open,setOpen]=useState(false),[selected,setSelected]=useState(''),[name,setName]=useState('');
  const views=state.value?.views??[], chosen=views.find(v=>v.id===selected)??views[0];
  useEffect(()=>{if(chosen&&selected!==chosen.id)setSelected(chosen.id);},[chosen?.id,selected]);
  useEffect(()=>{setName(chosen?.title??'');},[chosen?.id]);
  if (!views.length && state.phase !== 'unavailable' && !error) return null;
  const ready=state.phase==='ready' && !!controller;
  const invoke=(name:string,args:Record<string,unknown>)=>run(()=>controller!.tool(name,args));
  return <div className="saved-view-bar" role="group" aria-label="Saved views" data-views-state={state.phase}>
    {views.filter(v=>v.pinned).map(view=><Button key={view.id} size="sm" variant="ghost" title={view.title}
      aria-label={`Open saved view ${view.title}`} aria-pressed={state.activeId===view.id} disabled={!ready||!view.available}
      onClick={()=>invoke('screens_navigate',{screen:view.id})}><Icon name="pin" size="sm" /></Button>)}
    <Popover open={open} onOpenChange={setOpen} placement="bottom-start">
      <Popover.Trigger><Button size="sm" variant="ghost" aria-label="Manage saved views" title="Saved views"><Icon name="book-marked" size="sm" /></Button></Popover.Trigger>
      <Popover.Content className="maipa-views-popover">
        <div className="view-actions"><Text>Saved views</Text><Button size="sm" variant="ghost" aria-label="Close saved views" onClick={()=>setOpen(false)}>Close</Button></div>
        {chosen ? <>
          <Select id="maipa-saved-view" label="Saved view" value={chosen.id} list={views.map(v=>({value:v.id,label:v.title+(v.available?'':' · unavailable')}))}
            onValueChange={setSelected} />
          <Input id="maipa-view-title" label="Name" value={name} maxLength={80} onValueChange={setName} disabled={!ready||!chosen.available} />
          <div className="view-actions">
            <Button size="sm" disabled={!ready||!chosen.available||!name.trim()} onClick={()=>invoke('screens_update_content',{id:chosen.id,partial:{title:name}})}>Save name</Button>
            <Button size="sm" variant="ghost" disabled={!ready||!chosen.available}
              onClick={()=>invoke(chosen.pinned?'screens_destroy':'screens_update_content',chosen.pinned?{id:chosen.id}:{id:chosen.id,partial:{pinned:true}})}>{chosen.pinned?'Unpin':'Pin'}</Button>
            <Button size="sm" variant="ghost" aria-label="Move saved view earlier" disabled={!ready||views.indexOf(chosen)===0} onClick={()=>run(async()=>{
              const ids=views.map(v=>v.id),index=ids.indexOf(chosen.id);[ids[index-1],ids[index]]=[ids[index],ids[index-1]];await controller!.reorder(ids);
            })}><Icon name="arrow-left" size="sm" /></Button>
            <Button size="sm" variant="ghost" aria-label="Move saved view later" disabled={!ready||views.indexOf(chosen)===views.length-1} onClick={()=>run(async()=>{
              const ids=views.map(v=>v.id),index=ids.indexOf(chosen.id);[ids[index],ids[index+1]]=[ids[index+1],ids[index]];await controller!.reorder(ids);
            })}><Icon name="arrow-right" size="sm" /></Button>
          </div>
          <Text size="sm">Revision {chosen.revision}. Unpinning keeps the saved definition.</Text>
          {!!chosen.history.length&&<ol aria-label="Saved view history">{chosen.history.map(entry=><li key={entry.revision}>
            Revision {entry.revision}: {entry.title}{entry.available?'':' · unavailable under current access'}
          </li>)}</ol>}
        </>:<Text size="sm">No saved views are available.</Text>}
        <Button size="sm" variant="ghost" onClick={()=>run(()=>controller!.load())} disabled={state.phase==='saving'}>Reload saved views</Button>
        {error&&<Text size="sm" role="alert">{error}</Text>}
      </Popover.Content>
    </Popover>
    {state.phase==='saving'&&<span role="status">Saving view…</span>}
    {(state.phase==='conflict'||state.phase==='unavailable')&&<Button size="sm" variant="ghost" onClick={()=>run(()=>controller!.load())}>Reload saved views</Button>}
    {error&&!open&&<Text size="sm" role="alert">{error}</Text>}
  </div>;
}
