"""Unattended controller of the temperature extension (owner, 2026-10-22: "expect a lot of churn ... power through that").

One pass, then exit: scrontab runs it every 15 minutes on a compute node (no long-lived loop on the login node), and it
can be run by hand. Under an exclusive lock it reconciles squeue/sacct with the ledger and the chunks' result files,
classifies every ended attempt, and tops the queue up to the shared cap with the chunks still incomplete.

  ledger.jsonl          append-only, fsynced: submit / end / complete / split / quarantine / exclude_node / pause / pass
  controller-config.json  cap, which chunks are released, memory, time, partition, exclusions (edited by the operator);
                        count_prefixes: only jobs named with one of these count against the cap (absent: every job
                        on the account counts)
  controller-latest.json  the last pass (rewritten each pass)
  STOP                  if present, the pass records itself and submits nothing

End states (sacct): COMPLETED with a verified complete.json ends the chunk. PREEMPTED, NODE_FAIL, BOOT_FAIL, DEADLINE and
CANCELLED (the controller never cancels) are transient: resubmit as is; the worker resumes from its JSONL. TIMEOUT:
resubmit while the attempt made progress; two TIMEOUTs without progress split the chunk at a subbatch boundary (the halves'
numbers equal the parent's). OUT_OF_MEMORY: resubmit once with more memory, then quarantine. FAILED: resubmit, unless the
last line of the error equals the previous failure's (quarantine). max_attempts attempts that ended without one new
block: quarantine (attempts that made progress do not count, so preemption churn never exhausts a chunk). Three
chunks failing with one error in a pass pause all submission (a systemic fault, not a chunk's). A node with two
NODE_FAIL/BOOT_FAIL ends is excluded from then on. A chunk with a job in squeue is never submitted again (one writer).

    python temperature_controller.py [--dry-run]

With --dry-run, TEMPCTL_DRY_CONFIG (a JSON object) overrides controller-config.json keys, to try a change on the live
state before making it.
"""
import collections
import datetime
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / 'plastchem-euler/temperature-v1'
PREFIX = 'tempext-'
TRANSIENT = {'PREEMPTED', 'NODE_FAIL', 'BOOT_FAIL', 'DEADLINE', 'CANCELLED', 'REVOKED'}
out = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), operations=[], events=[])


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def call(args, env=None):
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=120)
    out['operations'].append(dict(command=args, returncode=p.returncode, stdout=p.stdout[-2000:], stderr=p.stderr[-2000:]))
    if p.returncode:
        raise RuntimeError(f'{args[0]} failed: {p.stderr.strip()[:300]}')
    return p.stdout


def append(ledger, event, dry):
    event = dict(event, utc=now())
    out['events'].append(event)
    if dry:
        return
    with ledger.open('a') as handle:
        handle.write(json.dumps(event, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def chunk_size(chunks, cid, subbatch):
    """Number of keys of a chunk id; mirrors the worker's split rule (cut at a subbatch boundary)."""
    parts = cid.split('.')
    n = len(chunks[parts[0]]['keys'])
    for half in parts[1:]:
        cut = split_point(n, subbatch)
        n = cut if half == 'a' else n - cut
    return n


def split_point(n, subbatch):
    groups = -(-n // subbatch)
    return subbatch * (-(-groups // 2)) if groups > 1 else -(-n // 2)


def count_lines(path, cache):
    """Exact newline count, reading only what was appended since the last pass (a shorter file is recounted)."""
    if not path.exists():
        return 0
    size = path.stat().st_size
    offset, lines = cache.get(str(path), (0, 0))
    if size < offset:
        offset, lines = 0, 0
    with path.open('rb') as handle:
        handle.seek(offset)
        while True:
            block = handle.read(1 << 22)
            if not block:
                break
            lines += block.count(b'\n')
            offset += len(block)
    cache[str(path)] = (offset, lines)
    return lines


def last_error(log, job_id):
    """Last non-empty line printed by this attempt (its exception), from the chunk's appended log."""
    if not log.exists():
        return 'no log'
    text = log.read_text(errors='replace').splitlines()
    start = max((i for i, line in enumerate(text) if f'"job_id": "{job_id}"' in line), default=0)
    tail = [line.strip() for line in text[start:] if line.strip()]
    return tail[-1][:500] if tail else 'empty log'


def main(dry):
    lock = (ROOT / 'controller.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        out['status'] = 'another pass holds the lock'
        return
    ledger = ROOT / 'ledger.jsonl'
    config = json.loads((ROOT / 'controller-config.json').read_text())
    if dry and os.environ.get('TEMPCTL_DRY_CONFIG'):
        config.update(json.loads(os.environ['TEMPCTL_DRY_CONFIG']))
    campaign = json.loads((ROOT / 'inputs/campaign.json').read_text())
    chunks = json.loads((ROOT / 'inputs/chunks.json').read_text())['chunks']
    blocks = len(campaign['temperatures_c']) * (len(campaign['solvents']) + sum(len(p['conformers']) for p in campaign['polymers']))
    events = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    if (ROOT / 'STOP').exists():
        out['status'] = 'STOP present: nothing submitted'
        append(ledger, dict(event='pass', status='stopped'), dry)
        return

    attempts = collections.defaultdict(list)      # chunk -> submit events (in order)
    ended = {}                                     # job_id -> end event
    complete, quarantined, split, paused, excluded = set(), {}, {}, None, set(config['exclude_nodes'])
    for e in events:
        if e['event'] == 'submit':
            attempts[e['chunk']].append(e)
        elif e['event'] == 'end':
            ended[e['job_id']] = e
        elif e['event'] == 'complete':
            complete.add(e['chunk'])
        elif e['event'] == 'quarantine':
            quarantined[e['chunk']] = e['reason']
        elif e['event'] == 'split':
            split[e['chunk']] = e['children']
        elif e['event'] == 'exclude_node':
            excluded.add(e['node'])
        elif e['event'] == 'pause':
            paused = e['reason']
        elif e['event'] == 'resume':
            paused = None

    # every chunk id the campaign has now: released top-level ids, with split parents replaced by their children
    release = config['release']
    todo, stack = [], [c for c in chunks if release == 'all' or c in release]
    while stack:
        c = stack.pop(0)
        if c in split:
            stack = list(split[c]) + stack
        else:
            todo.append(c)

    # -r: one line per array task, so other projects' pending arrays count in full against the shared cap
    queue = [line.split('|') for line in call(['squeue', '-h', '-r', '-u', os.environ.get('USER', 'aaltamimi2'),
                                               '-o', '%i|%j|%T|%R|%N']).splitlines() if line.strip()]
    account_jobs = len(queue)
    prefixes = config.get('count_prefixes')
    counted = account_jobs if prefixes is None else sum(row[1].startswith(tuple(prefixes)) for row in queue)
    active = {row[1][len(PREFIX):]: row for row in queue if row[1].startswith(PREFIX)}
    out['queue'] = dict(account_jobs=account_jobs, counted=counted, ours=len(active))
    # a job submitted by a pass that died before its ledger write: record it, so its end is classified too
    known = {a['job_id'] for sub in attempts.values() for a in sub}
    for c, row in active.items():
        if row[0] not in known:
            event = dict(event='submit', chunk=c, job_id=row[0], attempt=len(attempts[c]) + 1, recovered=True)
            append(ledger, event, dry)
            attempts[c].append(dict(event, utc=now()))

    # classify attempts that left the queue
    open_jobs = [(c, a) for c, sub in attempts.items() for a in sub if a['job_id'] not in ended]
    gone = [(c, a) for c, a in open_jobs if active.get(c, [None])[0] != a['job_id']]
    states = {}
    if gone:
        raw = call(['sacct', '-X', '-n', '-P', '-j', ','.join(a['job_id'] for _, a in gone),
                    '-o', 'JobID,State,ExitCode,NodeList,Elapsed'])
        for line in raw.splitlines():
            f = line.split('|')
            if len(f) >= 5:
                states[f[0]] = dict(state=f[1].split()[0], exit_code=f[2], node=f[3], elapsed=f[4])
    cache_path = ROOT / 'controller-state.json'
    cache = {k: tuple(v) for k, v in json.loads(cache_path.read_text()).items()} if cache_path.exists() else {}
    failures = collections.Counter()
    for c, a in gone:
        s = states.get(a['job_id'])
        if not s or s['state'] in ('PENDING', 'RUNNING', 'COMPLETING', 'REQUEUED', 'SUSPENDED'):
            continue                                 # accounting lag: decide next pass
        lines = count_lines(ROOT / 'results' / f'{c}.jsonl', cache)
        end = dict(event='end', chunk=c, job_id=a['job_id'], attempt=a['attempt'], state=s['state'], exit_code=s['exit_code'],
                   node=s['node'], elapsed=s['elapsed'], blocks_done=lines)
        if s['state'] == 'COMPLETED':
            done = ROOT / 'results' / f'{c}.complete.json'
            ok = done.exists() and json.loads(done.read_text()).get('blocks') == blocks == lines
            end['class'] = 'completed' if ok else 'failed'
            if not ok:
                end['error'] = 'exit 0 without a verified complete.json'
        elif s['state'] in TRANSIENT:
            end['class'] = 'transient'
        elif s['state'] == 'TIMEOUT':
            end['class'] = 'timeout'
        elif s['state'] == 'OUT_OF_MEMORY':
            end['class'] = 'oom'
        else:
            end['class'] = 'failed'
            end['error'] = last_error(ROOT / 'logs' / f'{c}.log', a['job_id'])
        append(ledger, end, dry)
        ended[a['job_id']] = end
        if end['class'] == 'completed':
            append(ledger, dict(event='complete', chunk=c, blocks=lines), dry)
            complete.add(c)
        if end['class'] == 'failed':
            failures[end.get('error')] += 1
    if not paused:
        for error, n in failures.items():
            if n >= 3:
                paused = f'{n} chunks failed in one pass with: {error}'
                append(ledger, dict(event='pause', reason=paused), dry)

    # node hygiene
    node_failures = collections.Counter(e['node'] for e in ended.values() if e['state'] in ('NODE_FAIL', 'BOOT_FAIL'))
    for node, n in node_failures.items():
        if n >= 2 and node not in excluded and node not in ('', 'None assigned'):
            append(ledger, dict(event='exclude_node', node=node, reason=f'{n} NODE_FAIL/BOOT_FAIL ends'), dry)
            excluded.add(node)

    # next action per chunk
    plan = []
    for c in todo:
        if c in complete or c in quarantined or c in active:
            continue
        sub = attempts.get(c, [])
        ends = [ended[a['job_id']] for a in sub if a['job_id'] in ended]
        if len(ends) < len(sub):
            continue                                 # an attempt without a decided end: wait
        mem = config['mem_mb']
        if ends:
            last = ends[-1]
            # churn is expected: only attempts that ended without a single new block count against the cap
            stuck = sum(e['blocks_done'] <= (ends[i - 1]['blocks_done'] if i else 0) for i, e in enumerate(ends))
            if last['class'] != 'completed' and stuck >= config['max_attempts']:
                quarantined[c] = f"{stuck} attempts without progress, last {last['state']} {last.get('error', '')}".strip()
                append(ledger, dict(event='quarantine', chunk=c, reason=quarantined[c]), dry)
                continue
            if last['class'] == 'completed':
                continue
            if last['class'] == 'timeout':
                stalled = [e for e in ends if e['class'] == 'timeout']
                if len(stalled) >= 2 and stalled[-1]['blocks_done'] == stalled[-2]['blocks_done']:
                    n = chunk_size(chunks, c, campaign['subbatch'])
                    if n >= 2:
                        children = [c + '.a', c + '.b']
                        append(ledger, dict(event='split', chunk=c, children=children,
                                            reason='two TIMEOUTs without progress'), dry)
                        split[c] = children
                        todo.extend(children)
                    else:
                        quarantined[c] = 'two TIMEOUTs without progress on a single contaminant'
                        append(ledger, dict(event='quarantine', chunk=c, reason=quarantined[c]), dry)
                    continue
            if last['class'] == 'oom' or any(e['class'] == 'oom' for e in ends):
                if sum(e['class'] == 'oom' for e in ends) >= 2:
                    quarantined[c] = 'OUT_OF_MEMORY twice'
                    append(ledger, dict(event='quarantine', chunk=c, reason=quarantined[c]), dry)
                    continue
                mem = config['oom_mem_mb']
            if last['class'] == 'failed':
                earlier = [e for e in ends[:-1] if e['class'] == 'failed']
                if earlier and earlier[-1].get('error') == last.get('error'):
                    quarantined[c] = f"same error twice: {last.get('error')}"
                    append(ledger, dict(event='quarantine', chunk=c, reason=quarantined[c]), dry)
                    continue
        plan.append((0 if ends else 1, c, mem, len(sub) + 1))
    plan.sort(key=lambda p: p[0])                    # resubmissions before first attempts, else chunk order

    free = max(0, config['cap'] - counted)
    submitted = []
    if paused:
        out['paused'] = paused
    elif config.get('submit', True):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('SLURM_', 'SBATCH_'))}
        for _, c, mem, attempt in plan[:min(free, config['max_submit_per_pass'])]:
            command = ['sbatch', '--parsable', f'--job-name={PREFIX}{c}', f"--partition={config['partition']}",
                       f"--constraint={config['constraint']}", f"--exclude={','.join(sorted(excluded))}", '--nodes=1',
                       '--ntasks=1', '--cpus-per-task=1', f'--mem={mem}M', f"--time={config['time']}", '--no-requeue',
                       f"--output={ROOT / 'logs' / (c + '.log')}", '--open-mode=append',
                       str(ROOT / 'code/run_chunk.sh'), c]
            if dry:
                out['events'].append(dict(would_submit=c, mem_mb=mem, attempt=attempt))
                continue
            job = call(command, env=env).strip().split(';')[0]
            append(ledger, dict(event='submit', chunk=c, job_id=job, attempt=attempt, mem_mb=mem,
                                exclude=sorted(excluded)), dry)
            submitted.append(c)

    keys_total = sum(chunk_size(chunks, c, campaign['subbatch']) for c in todo)
    done_eval = sum(min(count_lines(ROOT / 'results' / f'{c}.jsonl', cache), blocks) * chunk_size(chunks, c, campaign['subbatch'])
                    for c in todo)
    if not dry:
        cache_path.write_text(json.dumps(cache))
    summary = dict(chunks=len(todo), complete=sum(c in complete for c in todo), quarantined=len(quarantined),
                   running=sum(r[2] == 'RUNNING' for r in active.values()), pending=sum(r[2] == 'PENDING' for r in active.values()),
                   submitted=len(submitted), waiting=len(plan) - len(submitted), account_jobs=account_jobs + len(submitted),
                   counted_jobs=counted + len(submitted), count_prefixes=prefixes,
                   evaluations_done=done_eval, evaluations_total=keys_total * blocks,
                   fraction=round(done_eval / (keys_total * blocks), 4) if keys_total else None,
                   excluded=sorted(excluded), paused=paused)
    append(ledger, dict(event='pass', **summary), dry)
    out.update(status='ok', summary=summary, quarantined=quarantined)


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    try:
        main(dry)
    except Exception as exc:
        out.update(status='failed', error=repr(exc))
    if not dry:
        (ROOT / 'controller-latest.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps({k: out[k] for k in out if k != 'operations'}))
