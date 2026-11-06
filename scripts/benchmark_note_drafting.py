"""Sequential frozen-evidence drafting comparison; never applies notes or reads labels."""
import argparse
import copy
import json
import os
from pathlib import Path
import time
from dotenv import load_dotenv
from services.note_generation.runtime import Runtime
from services.note_generation.storage import write,memory_slot
from services.note_generation.workflow import draft_packet
from services.retrieval.engine import digest


def summarize(calls):
    return {'calls':len(calls),'input_tokens':sum(c.get('formatted_prompt_tokens',0) for c in calls),
            'generated_tokens':sum(c.get('tokens_predicted',0) for c in calls),
            'prompt_tokens_reused':sum((c.get('timings') or {}).get('cache_n',0) for c in calls),
            'input_processing_seconds':sum((c.get('timings') or {}).get('prompt_ms',0) for c in calls)/1000,
            'output_generation_seconds':sum((c.get('timings') or {}).get('predicted_ms',0) for c in calls)/1000}


def main():
    load_dotenv()
    p=argparse.ArgumentParser(__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--job');source.add_argument('--case');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--only',choices=['baseline','optimized'],default='optimized')
    a=p.parse_args()
    if a.case:
        from services.retrieval.workspace import ResearchStore
        ds=ResearchStore().dataset('annomi')
        case=next(c for c in ds['conversations'] if c['conversation_id']==a.case)
        if case['split']!='dev':raise RuntimeError('Case must be development data')
        rows=case['segments'];split='dev'
        packet={'session_id':a.case,'revision':digest(rows),'mode':'predefined','full_transcript_turns':len(rows),
                'overview':[],'evidence':{r['id']:{'source':r,'retrieval':[{'provider':'frozen-development'}]} for r in rows}}
    else:
        job_path=Path('data/note-generation')/a.job
        job=json.loads((job_path/'job.json').read_text());packet=json.loads((job_path/'evidence.json').read_text())
        from services.retrieval.workspace import ResearchStore
        cid=job['request']['session_id'].removeprefix('demo-')
        ds=ResearchStore().dataset(job['request']['dataset'])
        split=next(c['split'] for c in ds['conversations'] if c['conversation_id']==cid)
        if split!='dev':raise RuntimeError('Benchmark restricted to development data; test/blind sources refused')
        if job['request']['kind']=='clinical':
            from services.api.main import session_factory
            from services.api.db import SessionRecord
            with session_factory() as db:
                record=db.get(SessionRecord,job['request']['session_id'])
                if record is None or json.loads(record.consent_json).get('independent_final_reserved'):
                    raise RuntimeError('Missing or independently reserved session refused')
    if any(a.output.glob('*/result.json')):raise RuntimeError('Choose a new benchmark output; successful results are immutable')
    a.output.mkdir(parents=True,exist_ok=True)
    write(a.output/'frozen-evidence.json',packet)
    write(a.output/'source-policy.json',{'job':a.job,'source_split':split,'evidence_hash':digest(packet),'policy':'Frozen local artifacts only; no note application or dataset mutation'})
    strategy='sections' if a.only=='baseline' else 'auto'
    rt=Runtime(a.output/'runtime')
    with memory_slot():
        try:
            t=time.monotonic();rt.load('qwen');load_seconds=time.monotonic()-t
            for run in ['fresh','warm','cached']:
                folder=a.output/('fresh-output' if run=='fresh' else 'warm-output')/run
                start=len(rt.metrics['calls']);before=time.monotonic()
                fields,selections=draft_packet(copy.deepcopy(packet),rt,folder,strategy=strategy)
                elapsed=time.monotonic()-before;calls=rt.metrics['calls'][start:]
                result={'strategy':strategy,'run':run,'evidence_hash':digest(packet),'seconds':elapsed,
                        'loading_seconds':load_seconds if run=='fresh' else 0,'metrics':summarize(calls),
                        'calls':calls,'recovery':copy.deepcopy(rt.metrics.get('recovery',{})),'backend':rt.metrics.get('backend'),'vram':rt.metrics.get('vram'),
                        'runtime_peak_rss_kib':rt.metrics['runtime_peak_rss_kib'],
                        'memory_scope':'Peak of the owned inference process; warm/cached peaks cumulative',
                        'fields':fields,'selections':selections}
                write(a.output/run/'result.json',result)
                print(json.dumps({k:v for k,v in result.items() if k not in ['fields','selections','calls']}),flush=True)
        finally:
            rt.close();write(a.output/'runtime-metrics.json',rt.metrics)

if __name__=='__main__':main()
