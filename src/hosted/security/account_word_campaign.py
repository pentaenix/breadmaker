"""Local durable admission for one inspected current-manuscript campaign.

No cloud client, credential, metric counter or manuscript content is stored here.
Counters are charged before attempted work and never refunded. Actual response
bytes are recorded before decoding, including the one response that exceeds a
limit. Every error/unknown outcome stops this campaign; crash-left `running`
state requires explicit reconciliation, never an automatic fresh allowance.
"""
from contextlib import contextmanager
from copy import deepcopy
import json,os,re,stat,tempfile,time
from pathlib import Path
from .account_word_backfill_store import BackfillUnavailable,BackfillBudgetExceeded,BackfillOutcomeUnknown

MAX_RESPONSE_CHUNK=4*1024*1024
DEFAULT_LIMITS=dict(reads=500_000,writes=25_000,rpcs=200_000,content_bytes=16*1024**3,
    download_bytes=64*1024**2,object_rpcs=35_000,response_bytes=1024**3)
MAX_LIMITS=dict(reads=1_000_000,writes=100_000,rpcs=1_000_000,content_bytes=64*1024**3,
    download_bytes=512*1024**2,object_rpcs=100_000,response_bytes=2*1024**3)


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise BackfillUnavailable('WORD_CAMPAIGN_CHECKPOINT_INVALID')
        result[key]=value
    return result


def _plan(plan):
    from .account_word_backfill import fingerprint
    from .saved_word_identity import _stamp
    from hosted.firestore.ids import require_user_id,require_project_id
    if type(plan) is not dict or set(plan)!={'schema_version','user_id','user_created_at','projects','fingerprint'} or type(plan['schema_version']) is not int or plan['schema_version']!=1:
        raise BackfillUnavailable('WORD_CAMPAIGN_PLAN_INVALID')
    require_user_id(plan['user_id']);_stamp(plan['user_created_at'])
    if type(plan['projects']) is not dict or len(plan['projects'])>100:raise BackfillUnavailable('WORD_CAMPAIGN_PLAN_INVALID')
    for pid,pin in plan['projects'].items():
        require_project_id(pid)
        if type(pin) is not dict or set(pin)!={'created_at','generation'} or type(pin['generation']) is not int or not 1<=pin['generation']<=2**53-1:raise BackfillUnavailable('WORD_CAMPAIGN_PLAN_INVALID')
        _stamp(pin['created_at'])
    if fingerprint({k:v for k,v in plan.items() if k!='fingerprint'})!=plan['fingerprint']:raise BackfillUnavailable('WORD_CAMPAIGN_PLAN_INVALID')
    return deepcopy(plan)


class BackfillCampaign:
    def __init__(self,path,*,project,bucket,approved_plans,until,limits=None,clock=time.time):
        from .account_word_backfill import fingerprint
        self.path=Path(path);self.clock=clock;self._active=False;self._state=None
        if (not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]',project) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]',bucket)
                or type(until) is not int or not clock()<until<=clock()+86400
                or type(approved_plans) not in (list,tuple) or not 1<=len(approved_plans)<=100):raise BackfillUnavailable('WORD_CAMPAIGN_SCOPE_INVALID')
        self._project,self._bucket,self._until=project,bucket,until
        plans=[_plan(p) for p in approved_plans];self._plans={p['user_id']:p for p in plans}
        if len(self._plans)!=len(plans):raise BackfillUnavailable('WORD_CAMPAIGN_SCOPE_INVALID')
        self._scope=fingerprint(dict(project=project,bucket=bucket,plans=sorted(p['fingerprint'] for p in plans)))
        self._limits=dict(DEFAULT_LIMITS if limits is None else limits)
        if set(self._limits)!=set(MAX_LIMITS) or any(type(v) is not int or not 0<v<=MAX_LIMITS[k] for k,v in self._limits.items()):raise BackfillUnavailable('WORD_CAMPAIGN_LIMIT_INVALID')
        parent=self.path.parent
        if parent.is_symlink() or not parent.is_dir():raise BackfillUnavailable('WORD_CAMPAIGN_PRIVATE_DIRECTORY_REQUIRED')
        info=parent.stat()
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700 or not self.path.name or self.path.name in ('.','..'):raise BackfillUnavailable('WORD_CAMPAIGN_PRIVATE_DIRECTORY_REQUIRED')
        with self._lock():
            if self.path.is_symlink():raise BackfillUnavailable('WORD_CAMPAIGN_PRIVATE_FILE_REQUIRED')
            if self.path.exists():self._load()
            else:
                self._state=dict(schema_version=1,scope=self.scope,limits=dict(self._limits),until=self.until,
                    status='active',stop_reason=None,used=dict.fromkeys(self._limits,0))
                self._save()

    @property
    def project(self):return self._project
    @property
    def bucket(self):return self._bucket
    @property
    def until(self):return self._until
    @property
    def scope(self):return self._scope
    @property
    def limits(self):return dict(self._limits)
    def plan_for(self,uid):
        if uid not in self._plans:raise BackfillUnavailable('WORD_CAMPAIGN_OWNER_SCOPE')
        return deepcopy(self._plans[uid])

    @contextmanager
    def _lock(self):
        try:import fcntl
        except ImportError:raise BackfillUnavailable('WORD_CAMPAIGN_LOCAL_LOCK_REQUIRED') from None
        lock=self.path.with_name(self.path.name+'.lock')
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise BackfillUnavailable('WORD_CAMPAIGN_PRIVATE_FILE_REQUIRED')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise BackfillUnavailable('WORD_CAMPAIGN_BUSY') from None
            yield
        finally:os.close(fd)

    def _load(self):
        fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>8192:raise BackfillUnavailable('WORD_CAMPAIGN_PRIVATE_FILE_REQUIRED')
            with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(8193)
        finally:os.close(fd)
        state=json.loads(raw,object_pairs_hook=_object)
        if (type(state) is not dict or set(state)!={'schema_version','scope','limits','until','status','stop_reason','used'}
                or type(state['schema_version']) is not int or state['schema_version']!=1 or state['scope']!=self.scope
                or type(state['limits']) is not dict or any(type(v) is not int for v in state['limits'].values())
                or state['limits']!=self._limits or type(state['until']) is not int or state['until']!=self.until
                or state['status'] not in {'active','running','stopped','complete'}
                or state['stop_reason'] not in {None,'error','budget','deadline','unknown_commit','interrupted'}
                or type(state['used']) is not dict or set(state['used'])!=set(self._limits)):
            raise BackfillUnavailable('WORD_CAMPAIGN_CHECKPOINT_INVALID')
        for key,value in state['used'].items():
            extra=MAX_RESPONSE_CHUNK if key=='response_bytes' and state['status']=='stopped' else 0
            if type(value) is not int or not 0<=value<=self._limits[key]+extra:raise BackfillUnavailable('WORD_CAMPAIGN_CHECKPOINT_INVALID')
        if (state['status']=='stopped')!=(state['stop_reason'] is not None):raise BackfillUnavailable('WORD_CAMPAIGN_CHECKPOINT_INVALID')
        self._state=state

    def _save(self):
        raw=json.dumps(self._state,sort_keys=True,separators=(',',':')).encode('ascii')
        fd,name=tempfile.mkstemp(prefix='.word-campaign-',dir=self.path.parent)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
            directory=os.open(self.path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(name):os.unlink(name)

    def _ready(self):
        if not self._active or self._state['status']!='running':raise BackfillBudgetExceeded('WORD_CAMPAIGN_NOT_RUNNING')
        if self.clock()>=self.until:raise BackfillBudgetExceeded('WORD_CAMPAIGN_DEADLINE')

    def reserve(self,**charges):
        self._ready()
        if any(k not in self._limits or k=='response_bytes' or type(v) is not int or v<0 for k,v in charges.items()):raise BackfillUnavailable('WORD_CAMPAIGN_CHARGE_INVALID')
        if any(self._state['used'][k]+v>self._limits[k] for k,v in charges.items()):raise BackfillBudgetExceeded('WORD_CAMPAIGN_BUDGET')
        if any(charges.values()):
            for key,value in charges.items():self._state['used'][key]+=value
            self._save() # Must land before the corresponding provider call.

    def observe_response(self,size):
        if not self._active or self._state['status']!='running':raise BackfillBudgetExceeded('WORD_CAMPAIGN_NOT_RUNNING')
        if type(size) is not int or not 0<=size<=MAX_RESPONSE_CHUNK:raise BackfillUnavailable('WORD_CAMPAIGN_RESPONSE_INVALID')
        self._state['used']['response_bytes']+=size
        exceeded=self._state['used']['response_bytes']>self._limits['response_bytes']
        late=self.clock()>=self.until
        if exceeded or late:self._state.update(status='stopped',stop_reason='budget' if exceeded else 'deadline')
        self._save() # Includes the final received response; never reset/refund.
        if exceeded or late:raise BackfillBudgetExceeded('WORD_CAMPAIGN_RESPONSE_BUDGET')

    def run(self,action):
        if self._active:raise BackfillUnavailable('WORD_CAMPAIGN_REENTRANCY')
        with self._lock():
            self._load()
            if self._state['status']!='active':raise BackfillBudgetExceeded('WORD_CAMPAIGN_STOPPED_OR_UNCERTAIN')
            if self.clock()>=self.until:
                self._state.update(status='stopped',stop_reason='deadline');self._save();raise BackfillBudgetExceeded('WORD_CAMPAIGN_DEADLINE')
            self._state['status']='running';self._save();self._active=True
            try:
                result=action();self._ready();self._state['status']='active';self._save();return result
            except BaseException as error:
                unknown=isinstance(error,BackfillOutcomeUnknown) or getattr(error,'_word_backfill_commit_unknown',False) is True
                if self._state['status']!='stopped' or unknown:
                    reason=('unknown_commit' if unknown else 'budget' if isinstance(error,BackfillBudgetExceeded)
                        else 'interrupted' if not isinstance(error,Exception) else 'error')
                    self._state.update(status='stopped',stop_reason=reason)
                self._save();raise
            finally:self._active=False

    def snapshot(self):
        with self._lock():self._load();return deepcopy(self._state)
