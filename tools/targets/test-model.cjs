const assert=require('node:assert/strict');
const model=require('../../static/js/features/projects/targets-model.js');
process.env.TZ='America/New_York';
const date=(value)=>new Date(value).getTime();
const now=date('2026-11-04T12:00:00-05:00');
const goal=(id,metric='words',period='daily',amount=10)=>({id,metric,period,amount,created:now});
const doc=model.defaults();
assert.equal(doc.enabled,false);
assert.deepEqual(model.progress(doc,now),[]);
model.apply(doc,{id:'enable',at:now,settings:{...doc,enabled:true,goals:[goal('daily'),goal('weekly','characters','weekly',50),goal('monthly','active','monthly',60)]}});
assert.equal(model.period('weekly',now).key,'2026-11-02');
assert.equal(model.period('monthly',now).key,'2026-11-01');
const op={id:'write',at:now,delta:{words:10,characters:50,active:20}};
assert.equal(model.apply(doc,op).length,2);
assert.equal(model.apply(doc,op).length,0,'response-loss retry is idempotent');
assert.equal(model.progress(doc,now)[0].value,10);
model.apply(doc,{id:'delete',at:now,delta:{words:-30}});
assert.equal(model.progress(doc,now)[0].value,0);
assert.equal(model.progress(doc,now)[0].ratio,1,'achievement stays achieved after editing');
const tomorrow=date('2026-11-05T00:00:00-05:00');
assert.equal(model.progress(doc,tomorrow)[0].ratio,0,'daily rollover');
assert.equal(model.progress(doc,tomorrow)[1].ratio,1,'weekly survives midnight');
assert.equal(model.progress(doc,date('2026-11-09T00:00:00-05:00'))[1].ratio,0,'Monday resets week');
assert.equal(model.progress(doc,date('2026-12-01T00:00:00-05:00'))[2].value,0,'month rollover');
const dst=model.period('daily',date('2026-11-01T12:00:00-05:00'));
assert.equal(dst.end-dst.start,25*3600000,'calendar day honors daylight saving');
assert.equal(model.period('monthly',date('2028-02-20T12:00:00-05:00')).end-date('2028-02-01T00:00:00-05:00'),29*86400000,'leap February');
assert.deepEqual(model.elapsed(0,1000,{visible:false,focused:true,lastActivity:0}),{time:0,active:0});
assert.deepEqual(model.elapsed(0,1000,{visible:true,focused:false,lastActivity:0}),{time:0,active:0});
assert.deepEqual(model.elapsed(44000,46000,{visible:true,focused:true,lastActivity:0}),{time:2,active:1},'thinking grace stops exactly at idle boundary');
assert.deepEqual(model.elapsed(46000,47000,{visible:true,focused:true,lastActivity:0}),{time:1,active:0});
assert.deepEqual(model.elapsed(0,60000,{visible:true,focused:true,lastActivity:0}),{time:0,active:0},'sleep does not earn time');
assert.deepEqual(model.count('Hello café 👩🏽‍💻'),{words:2,characters:15});
assert.equal(model.count('你好世界','zh-CN').words,2);
assert.equal(model.count('e\u0301 😀').characters,4,'counts Unicode code points, including spaces');
const oldEntries=Object.keys(doc.entries).length;
model.apply(doc,{id:'off',at:now,settings:{...doc,enabled:false}});
model.apply(doc,{id:'disabled-work',at:now,delta:{words:500}});
assert.equal(Object.keys(doc.entries).length,oldEntries);
assert.equal(doc.entries['daily:2026-11-04'].value,-20);
assert.deepEqual(model.progress(doc,now),[]);
assert.equal(model.normalize({enabled:true,goals:[goal('../bad'),goal('bad','wrong'),goal('good')],entries:{evil:{}}}).goals.length,1);
const many=model.defaults();many.enabled=true;many.goals=[goal('history')];
for(let i=0;i<400;i++)model.apply(many,{id:'day-'+i,at:now+i*86400000,delta:{words:11}});
assert.equal(Object.keys(many.entries).length,240);
assert.ok(JSON.stringify(many).length<100000,'bounded history fits UI-state contract');
assert.deepEqual(model.normalize(many),many,'durable round trip');
const net=model.defaults();net.enabled=true;net.goals=[goal('net')];
model.apply(net,{id:'delete-original',at:now,delta:{words:-5}});
model.apply(net,{id:'restore-original',at:now,delta:{words:5}});
assert.equal(model.progress(net,now)[0].value,0,'rewriting deleted baseline is not new writing');
const sequenced=model.defaults();sequenced.enabled=true;sequenced.goals=[goal('receipt','words','daily',10000)];
for(let i=1;i<=120;i++)model.apply(sequenced,{id:'source-a-'+i,stream:'device-a',sequence:i,at:now,delta:{words:1}});
model.apply(sequenced,{id:'source-a-1',stream:'device-a',sequence:1,at:now,delta:{words:1}});
assert.equal(model.progress(sequenced,now)[0].value,120,'replay older than the legacy 80-ID window is still deduplicated');
assert.throws(()=>model.apply(sequenced,{id:'gap',stream:'device-a',sequence:122,at:now,delta:{words:1}}),/SEQUENCE_GAP/);
model.apply(sequenced,{id:'b1',stream:'device-b',sequence:1,at:now,delta:{words:5}});
assert.equal(model.progress(sequenced,now)[0].value,125,'independent device streams add without losing contributions');
const oldGoal=goal('old'),newGoal=goal('new');
const archived=model.defaults();archived.enabled=true;archived.goals=[newGoal];
model.apply(archived,{id:'old-offline-writing',stream:'old-device',sequence:1,at:now,goals:[oldGoal],delta:{words:3}});
assert.equal(model.progress(archived,now)[0].value,0,'offline writing is never redirected into a replacement goal');
assert.equal(archived.entries['old:2026-11-04'].value,3,'recorded progress survives goal replacement in history');
assert.deepEqual(model.normalize(sequenced),sequenced,'permanent sequence receipts survive storage round trip');
const union=model.defaults();union.enabled=true;union.goals=[goal('time-union','time','daily',10000),goal('active-union','active','daily',10000)];
const intervals={time:[now,now+1000],active:[now,now+1000]};
model.apply(union,{id:'clock-a',stream:'clock-a',sequence:1,at:now,delta:{time:1,active:1},intervals});
model.apply(union,{id:'clock-b',stream:'clock-b',sequence:1,at:now,delta:{time:1,active:1},intervals});
assert.deepEqual(model.progress(union,now).map(p=>p.value),[1,1],'simultaneous foreground/active intervals count once across devices');
model.apply(union,{id:'clock-b2',stream:'clock-b',sequence:2,at:now,delta:{time:1,active:1},intervals:{time:[now+500,now+1500],active:[now+500,now+1500]}});
assert.deepEqual(model.progress(union,now).map(p=>p.value),[1.5,1.5],'partially overlapping intervals use their union');
assert.deepEqual(model.normalize(union),union,'intervals survive durable round trip');
const complete=model.defaults();complete.enabled=true;complete.goals=[goal('short-clock','active','daily',1)];
model.apply(complete,{id:'short-clock',stream:'clock',sequence:1,at:now,intervals:{active:[now,now+1000]},delta:{active:1}});
assert.equal(complete.entries['short-clock:2026-11-04'].intervals,undefined,'completed time targets compact their interval history');
model.apply(complete,{id:'later-clock',stream:'clock',sequence:2,at:now,intervals:{active:[now+1000,now+2000]},delta:{active:1}});
assert.equal(model.progress(complete,now)[0].value,1,'completed time goals stop gathering unnecessary clock data');
console.log('Targets calendar, Unicode, progress, idempotency, idle, sleep and history checks passed');

// The existing period identity is its captured local date label. A timezone
// change can give that same label different UTC bounds; neither stream's valid
// intervals may be acknowledged then clipped out by the first stream's window.
for(const metric of ['time','active'])for(const [period,key,aStart,aEnd,bStart,bEnd] of [
 ['daily','2026-11-05','2026-11-04T15:00Z','2026-11-05T15:00Z','2026-11-05T08:00Z','2026-11-06T08:00Z'],
 ['weekly','2026-11-02','2026-11-01T15:00Z','2026-11-08T15:00Z','2026-11-02T08:00Z','2026-11-09T08:00Z'],
 ['monthly','2026-11-01','2026-10-31T15:00Z','2026-11-30T15:00Z','2026-11-01T08:00Z','2026-12-01T08:00Z'],
])for(const reverse of [false,true]){
 const g={...goal('offset-clock',metric,period,1000),created:date(aStart)-86400000};
 const state=model.defaults();state.enabled=true;state.goals=[g];
 const op=(stream,start,end,span)=>({id:stream,stream,sequence:1,at:span[1],goals:[g],calendar:{[g.id]:{key,start:date(start),end:date(end)}},delta:{[metric]:10},intervals:{[metric]:span}});
 const a=op('plus-nine',aStart,aEnd,[date(aStart)+3600000,date(aStart)+3610000]);
 const b=op('minus-eight',bStart,bEnd,[date(bEnd)-14400000,date(bEnd)-14390000]);
 for(const item of reverse?[b,a]:[a,b])model.apply(state,item);
 const stored=state.entries[`${g.id}:${key}`];
 assert.equal(stored.value,20,`${metric}/${period}: both captured windows count, in either arrival order`);
 assert.equal(stored.start,Math.min(date(aStart),date(bStart)));
 assert.equal(stored.end,Math.max(date(aEnd),date(bEnd)));
 assert.deepEqual(model.normalize(state),state,'expanded calendar bounds preserve intervals through storage');
 model.apply(state,{...a,id:'same-interval',stream:'same-interval'});
 assert.equal(stored.value,20,'overlapping intervals across offsets still count once');
 model.apply(state,b);
 assert.equal(stored.value,20,'the acknowledged original stream remains idempotent');
 model.apply(state,op('clipped',bStart,bEnd,[date(bStart)-5000,date(bStart)+5000]));
 assert.equal(stored.value,25,'clip against the contribution’s own captured window, not its expanded history envelope');
}
console.log('Targets captured-calendar offset and arrival-order checks passed');
