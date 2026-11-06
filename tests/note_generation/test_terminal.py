import threading
import pytest
from services.note_generation.runtime import Runtime, OutputLimitError
from services.note_generation.terminal import TerminalTrace


def test_event_default_omits_content_and_off_is_quiet(monkeypatch, capsys):
    monkeypatch.delenv('NOTE_LOG_LEVEL', raising=False)
    log=TerminalTrace('job123');log.stage='overview'
    log.event('stage.start', turns=3)
    log.block('PRIVATE PROMPT','private transcript')
    output=capsys.readouterr().out
    assert '[notes:job123] [overview]' in output and 'stage.start' in output
    assert 'private transcript' not in output
    monkeypatch.setenv('NOTE_LOG_LEVEL','off')
    quiet=TerminalTrace('quiet');quiet.event('stage.start');quiet.block('PROMPT','private')
    assert capsys.readouterr().out==''


def test_full_logs_prompt_before_request_and_response_after(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('NOTE_LOG_LEVEL','full')
    rt=Runtime(tmp_path);rt.writer='qwen';rt.model={'sha256':'mock'}
    rt.prompt=lambda *a:'<system>policy</system><user>private transcript</user>'
    rt.count=lambda prompt:20
    before=[];sent=[]
    def request(endpoint,payload):
        before.append(capsys.readouterr().out);sent.append(payload)
        return {'content':'{"items":[]}', 'stop_type':'eos', 'tokens_predicted':5}
    rt.request=request
    assert rt.generate('policy',{'text':'private transcript'},800)=={'items':[]}
    after=capsys.readouterr().out
    assert 'MODEL PROMPT call=1' in before[0] and 'private transcript' in before[0]
    assert 'MODEL OPTIONS call=1' in before[0] and 'output_reserved' in before[0]
    assert 'RAW MODEL RESPONSE call=1 finish=complete' in after
    assert '{"items":[]}' in after
    assert sent[0]['prompt']=='<system>policy</system><user>private transcript</user>'
    assert sent[0]['n_predict']==800 and sent[0]['stream'] is False
    assert not (tmp_path/'generated-responses.jsonl').exists()


def test_limited_response_is_labelled_raw_and_withheld(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('NOTE_LOG_LEVEL','full')
    rt=Runtime(tmp_path);rt.writer='qwen';rt.model={'sha256':'mock'}
    rt.prompt=lambda *a:'prompt';rt.count=lambda prompt:10
    rt.request=lambda *a:{'content':'{"items":[]}', 'stop_type':'limit','tokens_predicted':800}
    with pytest.raises(OutputLimitError):rt.generate('policy',[],800)
    output=capsys.readouterr().out
    assert 'RAW MODEL RESPONSE call=1 finish=output_limit' in output
    assert '"runtime_finished_normally": false' in output


def test_heartbeat_during_request_stops_on_exit(monkeypatch):
    monkeypatch.setenv('NOTE_LOG_LEVEL','events')
    trace=TerminalTrace('heartbeat');noticed=threading.Event();finished=threading.Event()
    def event(name,**fields):
        assert not finished.is_set()
        if name=='model.waiting':noticed.set()
    trace.event=event
    with trace.waiting(1,interval=.01):
        assert noticed.wait(1)
    finished.set()


def test_source_terminal_escape_is_neutralized(monkeypatch,capsys):
    monkeypatch.setenv('NOTE_LOG_LEVEL','full')
    trace=TerminalTrace('escape');trace.block('PROMPT','text\x1b[2J\x9b2J\nnext line')
    output=capsys.readouterr().out
    assert '\x1b' not in output and '\x9b' not in output
    assert '\\u001b[2J' in output and '\\u009b2J' in output and '\nnext line' in output


def test_closed_terminal_does_not_break_generation_logs(monkeypatch):
    monkeypatch.setenv('NOTE_LOG_LEVEL','events')
    def broken(*args,**kwargs):raise BrokenPipeError()
    monkeypatch.setattr('builtins.print',broken)
    TerminalTrace('pipe').event('job.completed')
