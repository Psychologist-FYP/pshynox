"""Owned llama.cpp server: loopback, local weights, exact template/token budget."""
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import socket
import struct
import subprocess
import threading
import time
from urllib.request import Request, urlopen

from .terminal import TerminalTrace

ROOT = Path(__file__).resolve().parents[2]


class GenerationError(RuntimeError):
    pass


class OutputLimitError(GenerationError):
    """Incomplete even when the response happens to parse as JSON."""


def finish_reason(result):
    reason = result.get('stop_type') or result.get('finish_reason')
    if result.get('stopped_limit') or result.get('truncated') or reason in {'limit', 'length', 'max_tokens'}:
        return 'output_limit'
    if reason in {'eos', 'stop', 'word'} or result.get('stopped_eos') or result.get('stopped_word'):
        return 'complete'
    return 'unknown'



def runtime_environment():
    """Keep optional CUDA libraries scoped to the owned inference process."""
    env = os.environ.copy()
    libraries = os.getenv('NOTE_LIBRARY_PATH', '')
    if libraries:
        env['LD_LIBRARY_PATH'] = libraries + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    return env


def offload_summary(log, requested):
    if requested == 0:
        return 'CPU (explicit configuration)', 0
    matches = re.findall(r'offloaded (\d+)/(\d+) layers to GPU', log)
    if not matches or int(matches[-1][0]) == 0:
        raise GenerationError('GPU offload was requested but no layers were confirmed on the GPU. Check runtime.log and NOTE_LLAMA_SERVER / NOTE_LIBRARY_PATH; CPU fallback was not accepted.')
    count, total = map(int, matches[-1])
    return f'GPU · {count}/{total} layers offloaded', count


def model_path(writer):
    defaults = {'qwen': 'qwen/Qwen3-4B-Instruct-2507-Q4_K_M.gguf', 'omi': 'omi/sum-small-Q4_K_M.gguf'}
    value=Path(os.getenv('NOTE_' + writer.upper() + '_MODEL', str(ROOT / 'models/note_generation' / defaults[writer])))
    if not value.is_absolute(): raise GenerationError('Alternate model paths must be absolute')
    return value.resolve()


def manifest(path):
    """Read GGUF v2/v3 metadata without loading tensors or third-party code."""
    path = Path(path)
    if not path.is_file():
        raise GenerationError(f'Missing model: {path}. Supply a local GGUF or NOTE_QWEN_MODEL / NOTE_OMI_MODEL absolute path.')
    with path.open('rb') as f:
        def num(fmt):
            return struct.unpack('<' + fmt, f.read(struct.calcsize(fmt)))[0]
        def string():
            size = num('Q')
            if size > path.stat().st_size:
                raise GenerationError('Invalid GGUF string length')
            return f.read(size).decode('utf-8')
        def value(t, keep=True):
            formats = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
            if t in formats:
                return num(formats[t])
            if t == 8:
                return string()
            if t == 9:
                subtype, count = num('I'), num('Q')
                if count > path.stat().st_size:
                    raise GenerationError('Invalid GGUF array')
                for _ in range(count):
                    value(subtype, False)
                return None
            raise GenerationError('Unsupported GGUF metadata type')
        try:
            if f.read(4) != b'GGUF' or num('I') not in (2, 3):
                raise GenerationError('Invalid GGUF: expected version 2 or 3')
            tensors, count = num('Q'), num('Q')
            if not tensors or count > 100000:
                raise GenerationError('Invalid GGUF header')
            metadata = {}
            for _ in range(count):
                key = string()
                result = value(num('I'))
                if key.startswith('general.') or key in {'tokenizer.chat_template','tokenizer.ggml.model','tokenizer.ggml.pre'}:
                    metadata[key] = result
            if not metadata.get('tokenizer.chat_template'):
                raise GenerationError('GGUF lacks a chat template; supply a model with its verified native template.')
        except (ValueError, struct.error, UnicodeError) as exc:
            raise GenerationError('Invalid or incomplete GGUF metadata') from exc
        f.seek(0)
        sha = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'path': str(path), 'sha256': sha, 'size_bytes': path.stat().st_size,
            'metadata': metadata, 'source_repository': metadata.get('general.source.huggingface.repository'),
            'revision': metadata.get('general.source.revision'), 'provenance_limit': 'Only embedded metadata verified; filename is not provenance.'}


class Runtime:
    def __init__(self, folder, cancel=None):
        self.folder = Path(folder)
        self.trace = TerminalTrace(self.folder.name)
        self.cancel = cancel or threading.Event()
        self.process = None
        self.deadline=time.monotonic()+float(os.getenv('NOTE_JOB_TIMEOUT','1800'))
        self.writer = None
        self.context = int(os.getenv('NOTE_CONTEXT', '4096'))
        self.safety_margin = int(os.getenv('NOTE_SAFETY_MARGIN', '64'))
        self.max_requests = int(os.getenv('NOTE_MAX_GENERATION_CALLS', '96'))
        if not 0 <= self.safety_margin < self.context or self.max_requests < 1:
            raise GenerationError('Invalid NOTE_SAFETY_MARGIN or NOTE_MAX_GENERATION_CALLS')
        if not 2048 <= self.context <= 32768:
            raise GenerationError('NOTE_CONTEXT must be 2048..32768; larger than 4096 requires explicit measured configuration.')
        self.metrics = {'calls': [], 'loads': [], 'runtime_peak_rss_kib': 0, 'vram': None,
                        'memory_scope': 'Owned llama-server process only; whole application not measured'}

    def check(self):
        if time.monotonic()>self.deadline:
            raise GenerationError('Generation job timeout exceeded; start a new version or explicitly configure NOTE_JOB_TIMEOUT')
        if self.cancel.is_set():
            raise GenerationError('Cancelled by user')

    def request(self, endpoint, payload=None):
        self.check()
        req = Request(self.url + endpoint, data=None if payload is None else json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=float(os.getenv('NOTE_CALL_TIMEOUT', '180'))) as response:
                return json.load(response)
        except Exception as exc:
            raise GenerationError(f'Local runtime request failed ({endpoint}): {exc}. Check runtime.log; reduce GPU layers/context for allocation failures.') from exc

    def load(self, writer):
        if self.writer == writer:
            return
        self.close()
        load_started = time.monotonic()
        manifest_started = time.monotonic()
        self.trace.event('model.loading', writer=writer, context=self.context)
        self.model = manifest(model_path(writer))
        manifest_seconds = time.monotonic()-manifest_started
        binary = Path(os.getenv('NOTE_LLAMA_SERVER', str(ROOT / 'models/llama.cpp/build/bin/llama-server')))
        if not binary.is_file():
            raise GenerationError(f'Missing llama-server: {binary}. Build the pinned runtime documented in docs/NOTE_GENERATION.md; set NOTE_LLAMA_SERVER.')
        env = runtime_environment()
        self.metrics['runtime_version'] = subprocess.check_output([str(binary), '--version'], stderr=subprocess.STDOUT, text=True, env=env).strip()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        layers = int(os.getenv('NOTE_GPU_LAYERS', '0'))
        if layers != 0:
            devices = subprocess.check_output([str(binary), '--list-devices'], stderr=subprocess.STDOUT, text=True, env=env, timeout=30)
            self.metrics['available_devices'] = devices.strip()
            if not re.search(r'CUDA\d+:', devices):
                raise GenerationError('CUDA GPU requested but this runtime cannot see a CUDA device. Check the GPU driver and NOTE_LIBRARY_PATH.')
        self.metrics['backend'] = 'CPU (explicit fallback/default)' if layers == 0 else f'GPU offload requested: {layers}; inspect runtime log for actual backend'
        start = time.monotonic()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log = (self.folder / 'runtime.log').open('a')
        log_offset = self.log.tell()
        self.process = subprocess.Popen([str(binary), '-m', self.model['path'], '--host', '127.0.0.1', '--port', str(port),
            '-c', str(self.context), '-np', '1', '-ngl', str(layers),
            '-b', os.getenv('NOTE_BATCH_SIZE', '256'), '-ub', os.getenv('NOTE_UBATCH_SIZE', '128'),
            '--no-context-shift', '--jinja', '--verbosity', '4'],
            stdout=self.log, stderr=self.log, start_new_session=True, env=env)
        try:
            while time.monotonic() - start < 120:
                self.check()
                if self.process.poll() is not None:
                    raise GenerationError('Model load failed: invalid tensors, unsupported architecture or out of memory. Inspect runtime.log; use NOTE_GPU_LAYERS=0 and context 4096.')
                try:
                    with urlopen(self.url + '/health', timeout=1) as r:
                        if r.status == 200:
                            props = self.request('/props')
                            effective = props.get('default_generation_settings', {}).get('n_ctx')
                            if effective != self.context:
                                raise GenerationError(f'Runtime context mismatch: configured {self.context}, effective {effective}')
                            self.metrics['effective_context_tokens'] = effective
                            self.writer = writer
                            current_load_log = (self.folder / 'runtime.log').read_bytes()[log_offset:].decode(errors='replace')
                            backend, count = offload_summary(current_load_log, layers)
                            self.metrics.update(backend=backend, gpu_layers_offloaded=count)
                            self.sample_memory()
                            self.trace.event('model.ready', writer=writer, backend=backend, context=effective, model_sha256=self.model['sha256'])
                            self.metrics['loads'].append({'writer': writer, 'seconds': time.monotonic()-start, 'total_seconds':time.monotonic()-load_started,
                                'manifest_seconds':manifest_seconds, 'preparation_seconds':start-load_started-manifest_seconds, 'model': self.model})
                            return
                except GenerationError:
                    raise
                except Exception:
                    time.sleep(.15)
            raise GenerationError('Model readiness timed out after 120 seconds')
        except BaseException:
            self.close()
            raise

    def prompt(self, system, data):
        return self.request('/apply-template', {'messages': [{'role':'system','content':system},
            {'role':'user','content':json.dumps(data, ensure_ascii=False)}], 'add_generation_prompt':True})['prompt']

    def count(self, text):
        return len(self.request('/tokenize', {'content':text, 'add_special':True, 'parse_special':True})['tokens'])

    def fits(self, system, data, output=700):
        return self.count(self.prompt(system, data)) + output + self.safety_margin <= self.context

    def generate(self, system, data, output=700):
        prompt = self.prompt(system, data)
        count = self.count(prompt)
        if count + output + self.safety_margin > self.context:
            raise GenerationError(f'Context budget exceeded: {count} input + {output} output + {self.safety_margin} margin > {self.context}; no input was truncated.')
        self.check()
        if len(self.metrics['calls']) >= self.max_requests:
            raise GenerationError('Total generation request budget exhausted')
        call = len(self.metrics['calls'])+1
        payload = {'prompt':prompt, 'n_predict':output, 'temperature':0,
            'seed':42, 'cache_prompt':True, 'json_schema':getattr(self,'response_schema',{'type':'object'}), 'stream':False}
        self.trace.event('model.request', call=call, writer=self.writer,
            prompt_version=getattr(self,'prompt_version','workflow-v4'),
            input_tokens=count, output_reserved=output, safety_margin=self.safety_margin, context=self.context)
        self.trace.block(f'MODEL PROMPT call={call}', prompt)
        self.trace.block(f'MODEL OPTIONS call={call}', {k:v for k,v in payload.items() if k != 'prompt'})
        started = time.monotonic()
        try:
            with self.trace.waiting(call):
                result = self.request('/completion', payload)
        except Exception as exc:
            self.trace.event('model.request_failed', call=call, error=str(exc))
            raise
        self.trace.event('model.response', call=call, seconds=round(time.monotonic()-started,2),
            generated_tokens=result.get('tokens_predicted'), finish_reason=finish_reason(result),
            runtime_stop=result.get('stop_type'), runtime_finished_normally=finish_reason(result)=='complete',
            input_processing_ms=(result.get('timings') or {}).get('prompt_ms'),
            output_generation_ms=(result.get('timings') or {}).get('predicted_ms'),
            prompt_tokens_reused=(result.get('timings') or {}).get('cache_n'))
        self.trace.block(f'RAW MODEL RESPONSE call={call} finish={finish_reason(result)}', result.get('content'))
        self.metrics['calls'].append({**{k:result.get(k) for k in ('timings','tokens_predicted','tokens_evaluated','stop_type','truncated')},
            'stage':self.trace.stage, 'request_seconds':time.monotonic()-started,
            'formatted_prompt_tokens':count, 'reserved_output_tokens':output,
            'safety_margin_tokens':self.safety_margin, 'effective_context_tokens':self.context,
            'finish_reason':finish_reason(result), 'model_sha256':self.model['sha256'],
            'prompt_version':getattr(self,'prompt_version','workflow-v4'),
            'prompt_sha256':hashlib.sha256(system.encode()).hexdigest()})
        calls=self.metrics['calls']
        self.metrics['inference_totals']={
            'calls':len(calls),
            'formatted_input_tokens':sum(c.get('formatted_prompt_tokens',0) for c in calls),
            'prefill_tokens':sum((c.get('timings') or {}).get('prompt_n',0) for c in calls),
            'prompt_tokens_reused':sum((c.get('timings') or {}).get('cache_n',0) for c in calls),
            'generated_tokens':sum(c.get('tokens_predicted') or 0 for c in calls),
            'input_processing_seconds':sum((c.get('timings') or {}).get('prompt_ms',0) for c in calls)/1000,
            'output_generation_seconds':sum((c.get('timings') or {}).get('predicted_ms',0) for c in calls)/1000}
        # Diagnostics contain counts and hashes, never generated patient text.
        from .storage import write
        write(self.folder/'request-metrics.json', self.metrics['calls'])
        self.sample_memory()
        self.check()
        if finish_reason(result) == 'output_limit':
            raise OutputLimitError('Incomplete output: generation reached its token limit; result withheld')
        if finish_reason(result) != 'complete':
            raise GenerationError('Unrecognized runtime finish reason; result withheld')
        try:
            return json.loads(result['content'])
        except (ValueError, KeyError) as exc:
            raise GenerationError('Malformed JSON from model; result withheld') from exc

    def sample_memory(self):
        if self.process:
            if self.metrics.get('gpu_layers_offloaded', 0):
                try:
                    output = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'], text=True, timeout=3)
                    for row in output.splitlines():
                        pid, memory = row.split(',', 1)
                        if int(pid.strip()) == self.process.pid:
                            previous = self.metrics.get('vram') or {}
                            self.metrics['vram'] = {'peak_sampled_mib': max(int(memory.strip()), previous.get('peak_sampled_mib', 0)), 'scope':'owned llama-server process'}
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            try:
                lines = Path(f'/proc/{self.process.pid}/status').read_text().splitlines()
                peak = next(int(s.split()[1]) for s in lines if s.startswith('VmHWM:'))
                self.metrics['runtime_peak_rss_kib'] = max(peak, self.metrics['runtime_peak_rss_kib'])
            except (OSError, StopIteration):
                pass

    def close(self):
        if self.process:
            self.sample_memory()
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait()
            self.trace.event('model.closed', writer=self.writer, released=self.process.poll() is not None)
            self.metrics['resource_release_verified'] = self.process.poll() is not None
            self.process = None
            self.log.close()
        self.writer = None
