import {useEffect,useState} from 'react';
import {Button,Icon,Tooltip} from '@particle-academy/react-fancy';
import {readCommonViews,type CommonView,type CommonViewKey} from './common-views.js';

export function HeaderShortcuts({userId,active,busy,open}:{userId:string;active?:CommonViewKey;busy:boolean;open:(key:CommonViewKey)=>void}){
 const [views,setViews]=useState<CommonView[]>([]);
 const [failed,setFailed]=useState(false);
 const [attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const lifetime=new AbortController();let sequence=0;
  const load=async()=>{
   const current=++sequence;
   try{const next=await readCommonViews(userId,lifetime.signal);if(!lifetime.signal.aborted&&current===sequence){setViews(next);setFailed(false);}}
   catch{if(!lifetime.signal.aborted&&current===sequence){setViews([]);setFailed(true);}}
  };
  void load();window.addEventListener('focus',load);
  return()=>{lifetime.abort();window.removeEventListener('focus',load);};
 },[userId,attempt]);
 return <div className="header-shortcuts" role="group" aria-label="Common views">
  {views.map(view=><Tooltip key={view.key} content={view.title} placement="bottom">
   <Button size="sm" variant="ghost" aria-label={view.title} aria-pressed={active===view.key}
    disabled={busy} onClick={()=>open(view.key)}><Icon name={view.icon} size="sm"/></Button>
  </Tooltip>)}
  {failed&&<Tooltip content="Reload view shortcuts" placement="bottom"><Button size="sm" variant="ghost" aria-label="Reload view shortcuts"
   onClick={()=>setAttempt(value=>value+1)}><Icon name="refresh-cw" size="sm"/></Button></Tooltip>}
 </div>;
}
