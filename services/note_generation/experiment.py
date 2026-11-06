"""Development-only reproducible selection, real smoke, frozen writer comparison."""
import argparse
import csv
import json
from pathlib import Path
from .runtime import Runtime
from .workflow import run, draft_packet
from .storage import write, memory_slot
from services.retrieval.engine import digest


def main():
    p=argparse.ArgumentParser(__doc__)
    p.add_argument('action',choices=['prepare','smoke','run','omi'])
    p.add_argument('--output',type=Path,default=Path('data/note-generation-experiment'))
    p.add_argument('--case');p.add_argument('--mode',choices=['full','predefined','followup'],default='predefined')
    p.add_argument('--packet',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    if a.action=='prepare':
        from services.retrieval.workspace import ResearchStore
        ds=ResearchStore().dataset('annomi')
        chosen=[c for c in ds['conversations'] if c['split']=='dev' and c['conversation_id'] in ds['selection']['pilot_ids']][:10]
        out={'policy':'pilot development conversations only, dataset order; no labels or held-out conversations', 'cases':[]}
        for c in chosen:
            rows=c['segments'];snap={'session_id':c['conversation_id'],'revision':digest(rows),'segments':rows}
            write(a.output/(c['conversation_id']+'.json'),snap)
            out['cases'].append({'id':c['conversation_id'],'hash':digest(rows),'rationale':'Available pilot development conversation; selected without quality/length filtering'})
        write(a.output/'selection.json',out)
        with (a.output/'human-review.csv').open('w') as f:
            csv.writer(f).writerow(['case','method','reviewer','unsupported_facts','important_omissions','wrong_person_speaker','negation_errors','temporal_errors','citation_support','section_compliance','human_corrections','editing_seconds','full_transcript_reviewed','comments'])
        return
    folder=a.output/(a.case or a.action)/a.mode
    rt=Runtime(folder)
    with memory_slot():
        try:
            if a.action=='omi':
                if not a.packet: p.error('--packet evidence.json required')
                packet=json.loads(a.packet.read_text());rt.load('omi')
                fields,selection=draft_packet(packet,rt)
                write(folder/'omi.json',{'original':fields,'packet_hash':digest(packet),'evidence_selection':selection,'review':'pending','metrics':rt.metrics})
            else:
                if a.action=='smoke':
                    rows=[{'id':'smoke-1','session_id':'fictional-smoke','start_ms':0,'end_ms':1000,'speaker_role':'CLIENT','text':'I have slept poorly for two weeks. I do not take medication.','revision':1},
                          {'id':'smoke-2','session_id':'fictional-smoke','start_ms':1000,'end_ms':2000,'speaker_role':'CLINICIAN','text':'We discussed keeping a sleep diary; would you like to try that?', 'revision':1},
                          {'id':'smoke-3','session_id':'fictional-smoke','start_ms':2000,'end_ms':3000,'speaker_role':'CLIENT','text':'Yes, I agree to try the diary before our next session.', 'revision':1}]
                    snap={'session_id':'fictional-smoke','revision':digest(rows),'segments':rows}
                else:
                    selection=json.loads((a.output/'selection.json').read_text())
                    if a.case not in {c['id'] for c in selection['cases']}: p.error('Case must be selected development ID')
                    snap=json.loads((a.output/(a.case+'.json')).read_text())
                    if digest(snap['segments'])!=next(c['hash'] for c in selection['cases'] if c['id']==a.case): p.error('Selected transcript changed')
                if (folder/'result.json').exists(): p.error('Successful result already cached; choose a new output to rerun')
                result=run(snap,rt,folder,mode=a.mode,progress=lambda s:print(s,flush=True))
                write(folder/'result.json',result)
        except Exception as exc:
            write(folder/'failure.json',{'error':str(exc),'metrics':rt.metrics})
            raise
        finally:
            rt.close()
            write(folder/'runtime-metrics.json',rt.metrics)

if __name__=='__main__': main()
