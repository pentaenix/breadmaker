import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const fixture = JSON.parse(open(__ENV.BREADMAKER_LOAD_MANIFEST));
if (!['local-memory-v1', 'local-firestore-v1'].includes(fixture.fixture) || !/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(fixture.base_url)) {
  throw new Error('Only the disposable loopback fixture is supported.');
}
const integer = (name, minimum, maximum) => {
  const value = Number(__ENV[name]);
  if (!Number.isInteger(value) || value < minimum || value > maximum) throw new Error(`Invalid ${name}`);
  return value;
};
const vus = integer('BREADMAKER_LOAD_VUS', 1, 20);
const seconds = integer('BREADMAKER_LOAD_SECONDS', 5, 120);
const arrivalRate = integer('BREADMAKER_LOAD_RATE', 1, 10);
const profile = __ENV.BREADMAKER_LOAD_PROFILE;
const workload = __ENV.BREADMAKER_LOAD_WORKLOAD || 'mixed';
if (!['mixed', 'read-only'].includes(workload)) throw new Error('Invalid workload');
if (!['smoke', 'concurrency', 'arrival'].includes(profile) || fixture.accounts.length < vus) {
  throw new Error('Invalid profile or insufficient distinct synthetic accounts.');
}
const errors = new Rate('application_errors');
const throttles = new Counter('throttled_requests');
const conflicts = new Counter('conflict_requests');
const actionTime = new Trend('action_duration', true);
const actionCount = new Counter('completed_actions');
const scenario = profile === 'arrival'
  ? { executor: 'constant-arrival-rate', rate: arrivalRate, timeUnit: '1s', duration: `${seconds}s`, preAllocatedVUs: vus, maxVUs: vus }
  : profile === 'smoke'
    ? { executor: 'per-vu-iterations', vus, iterations: 10, maxDuration: '60s' }
    : { executor: 'constant-vus', vus, duration: `${seconds}s` };
const thresholds = {
  application_errors: [{ threshold: 'rate<0.01', abortOnFail: true, delayAbortEval: '10s' }],
  http_req_failed: ['rate<0.01'],
  checks: ['rate==1'],
  completed_actions: ['count>0'],
  'http_req_duration{kind:read}': ['p(95)<500', 'p(99)<1000'],
};
if (workload === 'mixed') {
  thresholds['http_req_duration{op:chapter_save}'] = ['p(95)<1000', 'p(99)<2000'];
  thresholds['http_req_duration{op:map_create}'] = ['p(95)<1500', 'p(99)<3000'];
}
const operations = workload === 'read-only' ? ['chapter_read', 'project_navigation', 'maps_read']
  : ['chapter_read', 'project_navigation', 'maps_read', 'save_preread', 'save_verify', 'chapter_save', 'map_create', 'map_delete'];
for (const op of operations) {
  thresholds[`http_reqs{op:${op}}`] = ['count>0'];
  if (!thresholds[`http_req_duration{op:${op}}`]) {
    thresholds[`http_req_duration{op:${op}}`] = ['p(95)<500', 'p(99)<1000'];
  }
}
if (profile === 'arrival') thresholds.dropped_iterations = ['count==0'];
export const options = {
  scenarios: { breadmaker: { ...scenario, gracefulStop: '6s' } }, thresholds,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
  maxRedirects: 0, discardResponseBodies: false,
  userAgent: 'Breadmaker-local-loadtest/1',
};

function request(method, path, payload, account, op, kind = 'read', expected = 200) {
  const response = http.request(method, fixture.base_url + path,
    payload === null ? null : JSON.stringify(payload), {
      redirects: 0, timeout: '5s',
      headers: { 'Content-Type': 'application/json', Origin: fixture.base_url,
        Cookie: `${fixture.cookie_name}=${account.cookie}`,
        'X-Breadmaker-Project': account.project_id,
        'X-Breadmaker-Client': `load-${exec.vu.idInTest}` },
      tags: { name: op, op, kind },
    });
  const ok = check(response, { [`${op}: expected status`]: r => r.status === expected });
  errors.add(!ok);
  if (response.status === 429) throttles.add(1);
  if (response.status === 409) conflicts.add(1);
  if (!ok) fail(`${op} returned ${response.status}`); // No response bodies or cookies in logs.
  return response;
}

function json(response) {
  try { return response.json(); } catch (_) { errors.add(true); fail('Invalid JSON response'); }
}

export function setup() {
  const runtime = http.get(fixture.base_url + '/api/runtime', { redirects: 0, timeout: '5s', tags: { name: 'runtime_preflight' } });
  const data = json(runtime);
  if (runtime.status !== 200 || data.mode !== 'hosted_dev' || data.metadata.auth_mode !== 'test' ||
      data.metadata.environment !== 'test') throw new Error('Fixture runtime verification failed.');
  const anonymous = http.get(fixture.base_url + '/api/workspace', { redirects: 0, timeout: '5s',
    responseCallback: http.expectedStatuses(401), tags: { name: 'anonymous_denied' } });
  if (anonymous.status !== 401) throw new Error('Anonymous access was not denied.');
  if (fixture.accounts.length > 1) {
    const foreign = http.get(fixture.base_url + '/api/maps', { redirects: 0, timeout: '5s',
      headers: { Cookie: `${fixture.cookie_name}=${fixture.accounts[0].cookie}`,
        'X-Breadmaker-Project': fixture.accounts[1].project_id },
      responseCallback: http.expectedStatuses(403, 404), tags: { name: 'foreign_project_denied' } });
    if (![403, 404].includes(foreign.status)) throw new Error('Cross-user isolation failed.');
  }
}

export default function () {
  const start = Date.now();
  const account = fixture.accounts[exec.vu.idInTest - 1];
  const iteration = exec.vu.iterationInScenario;
  const cid = account.chapter_ids[iteration % account.chapter_ids.length];
  const slot = (iteration + exec.vu.idInTest - 1) % (workload === 'read-only' ? 7 : 10);
  let action;
  try {
    if (slot < 5) {
      action = 'chapter_read';
      const data = json(request('GET', `/api/chapters/${cid}`, null, account, action));
      const valid = check(data, { 'chapter belongs to synthetic project': d => d.chapter.id === cid && d.blocks.length > 0 });
      if (!valid) { errors.add(true); fail('Chapter content invariant failed'); }
    } else if (slot === 5) {
      action = 'project_navigation';
      request('GET', '/api/workspace', null, account, action);
    } else if (slot === 6) {
      action = 'maps_read';
      request('GET', '/api/maps', null, account, action);
    } else if (slot < 9) {
      action = 'chapter_save';
      const data = json(request('GET', `/api/chapters/${cid}`, null, account, 'save_preread'));
      const blocks = data.blocks.map(block => ({ id: block.id, block_type: 'paragraph',
        text_content: block.text_content, content_html: block.content_html }));
      const text = `Synthetic edit ${iteration} by virtual user ${exec.vu.idInTest}.`;
      blocks[0].text_content = text;
      blocks[0].content_html = text;
      request('PUT', `/api/chapters/${cid}/blocks`, { blocks, expected_revision: data.revision }, account, action, 'write');
      const saved = json(request('GET', `/api/chapters/${cid}`, null, account, 'save_verify'));
      const valid = check(saved, { 'read after write returns saved synthetic text': d => d.blocks[0].text_content === text });
      if (!valid) { errors.add(true); fail('Read after write invariant failed'); }
    } else {
      action = 'map_create';
      const made = json(request('POST', '/api/maps', { title: `Synthetic map ${iteration}` }, account, action, 'write'));
      if (!made.id) { errors.add(true); fail('Map ID missing'); }
      request('DELETE', `/api/maps/${made.id}`, null, account, 'map_delete', 'write');
    }
    actionCount.add(1, { action });
  } finally {
    actionTime.add(Date.now() - start, { action: action || 'unknown' });
    // Each VU starts at most one action per second, including arrival-rate mode.
    // At most 3 HTTP calls/action; <=60 workload requests/s at the 20 VU cap.
    sleep(Math.max(0, 1 - (Date.now() - start) / 1000));
  }
}

export function handleSummary(data) {
  return { [__ENV.BREADMAKER_LOAD_SUMMARY]: JSON.stringify(data, null, 2) };
}
