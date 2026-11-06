"""Rerun a failed local job without applying or modifying any saved note."""
import argparse
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from services.api.note_generation_routes import Start, snapshot
from services.note_generation.runtime import Runtime
from services.note_generation.workflow import run
from services.note_generation.storage import write, memory_slot


def main():
    load_dotenv()
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('job',nargs='?');p.add_argument('--case');p.add_argument('--legacy',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.case:
        request=Start(kind='research',dataset='annomi',session_id=a.case,transcript_reviewed=True,mode='predefined')
        snap,scope=snapshot(request)
        previous={'request':request.model_dump()}
    else:
        previous=json.loads((Path('data/note-generation')/a.job/'job.json').read_text())
        snap,scope=snapshot(Start(**previous['request']))
        if snap['revision'] != previous['snapshot']['revision']:
            raise RuntimeError('Failed job transcript has changed')
        from services.retrieval.workspace import ResearchStore
        cid=snap['session_id'].removeprefix('demo-')
        ds=ResearchStore().dataset(previous['request']['dataset'])
        if not any(c['conversation_id']==cid and c['split']=='dev' for c in ds['conversations']):
            raise RuntimeError('Real rerun restricted to development conversations')
    a.output.mkdir(parents=True,exist_ok=True)
    if (a.output/'result.json').exists(): raise RuntimeError('Result already exists; choose new output')
    write(a.output/'snapshot.json',snap)
    rt=Runtime(a.output);start=time.monotonic()
    with memory_slot():
        try:
            if a.legacy:
                from services.note_generation.workflow import pack
                # Original v3 policy/schema captured before editing, retained in local failed-job cache key docs.
                from services.note_generation.prompts import POLICY, ITEM
                legacy=POLICY+ITEM+'Read every supplied source. Extract distinct factual items with their structured attributes, consolidating related facts and retaining qualifiers. No AI formulation at this stage. Return {"items":[...]} only. These items are navigation hints; original sources remain required for drafting. Include unresolved discrepancies and action/response pairs.'
                rt.load('qwen')
                for chunk in pack(rt,legacy,snap['segments'],output=1100):rt.generate(legacy,chunk,1100)
                write(a.output/'legacy-completed.json',{'completed':True})
                return
            result=run(snap,rt,a.output,mode=previous['request']['mode'],provider='bm25',scope=scope,progress=lambda s:print(s,flush=True))
            write(a.output/'result.json',result)
        except Exception as exc:
            write(a.output/'failure.json',{'error':str(exc)});raise
        finally:
            rt.close();write(a.output/'runtime-metrics.json',rt.metrics)
            print(json.dumps({'seconds':time.monotonic()-start,'calls':len(rt.metrics['calls']),'recovery':rt.metrics.get('recovery')}),flush=True)

if __name__=='__main__':main()
