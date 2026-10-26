// Checks worker.js without any real account: signed test webhooks from each payment provider, web push encryption against
// RFC 8291's published example, the trend-alert run, the AI writer (with a fake Claude API), and the 15-minute refresh.
// Everything external (Google, Firestore, GitHub, push services, Claude) is faked in memory. Run: node web/pay/selftest.mjs
import {generateKeyPairSync, createHmac, createECDH, createDecipheriv, hkdfSync, verify as verifySig, sign as signRsa} from 'node:crypto';
import assert from 'node:assert/strict';
import worker from './worker.js';

const b64u = buf => Buffer.from(buf).toString('base64url');
const {privateKey} = generateKeyPairSync('rsa', {modulusLength: 2048});
const sa = {project_id: 'demo', client_email: 'sa@demo.iam.gserviceaccount.com', private_key: privateKey.export({type: 'pkcs8', format: 'pem'})};
const vapid = generateKeyPairSync('ec', {namedCurve: 'P-256'});
const vapidJwk = vapid.privateKey.export({format: 'jwk'});
const vapidPub = b64u(Buffer.concat([Buffer.from([4]), Buffer.from(vapidJwk.x, 'base64url'), Buffer.from(vapidJwk.y, 'base64url')]));
const env = {
  FIREBASE_SA: JSON.stringify(sa), LEMON_SECRET: 'lemon-test', STRIPE_SECRET: 'whsec_test', PADDLE_SECRET: 'paddle-test',
  GITHUB_TOKEN: 'ghp_test', GITHUB_REPO: 'me/site', SITE_URL: 'https://site.example/radar',
  VAPID_PUBLIC_KEY: vapidPub, VAPID_PRIVATE_JWK: JSON.stringify(vapidJwk), VAPID_SUBJECT: 'mailto:test@example.com',
  ANTHROPIC_API_KEY: 'sk-ant-test', AI_DAILY: '2'
};

// ---------- the outside world, in memory ----------
const db = new Map(), pushed = [], calls = [];
let trendsJson = {hot: {}}, claudeReply = null, idKey = null;
globalThis.fetch = async (url, opts = {}) => {
  url = String(url);
  calls.push(url);
  if (url.startsWith('https://oauth2.googleapis.com/token')) return new Response(JSON.stringify({access_token: 'tok', expires_in: 3600}));
  if (url.startsWith('https://firestore.googleapis.com/')) {
    assert.equal(opts.headers.authorization, 'Bearer tok');
    const path = decodeURIComponent(/documents\/([^?]+)/.exec(url)[1]);
    if (opts.method === 'GET' && !path.includes('/')) {   // a whole collection
      const documents = [...db.entries()].filter(([k]) => k.startsWith(path + '/')).map(([k, fields]) => ({name: `projects/demo/databases/(default)/documents/${k}`, fields}));
      return new Response(JSON.stringify({documents}));
    }
    if (opts.method === 'GET') return db.has(path) ? new Response(JSON.stringify({fields: db.get(path)})) : new Response('{}', {status: 404});
    if (opts.method === 'DELETE') { db.delete(path); return new Response('{}'); }
    db.set(path, Object.assign(db.get(path) || {}, JSON.parse(opts.body).fields));
    return new Response('{}');
  }
  if (url.startsWith('https://api.github.com/')) { calls.github = {url, auth: opts.headers.authorization, body: opts.body}; return new Response(null, {status: 204}); }
  if (url === 'https://site.example/radar/trends.json') return new Response(JSON.stringify(trendsJson));
  if (url.startsWith('https://push.example/')) { pushed.push({url, headers: opts.headers, body: opts.body}); return new Response(null, {status: url.endsWith('/gone') ? 410 : 201}); }
  if (url.startsWith('https://www.googleapis.com/service_accounts/')) return new Response(JSON.stringify({keys: [idKey.jwk]}));
  if (url === 'https://api.anthropic.com/v1/messages') { calls.claude = {headers: opts.headers, body: JSON.parse(opts.body)}; return new Response(JSON.stringify(claudeReply)); }
  throw new Error('unexpected fetch ' + url);
};
const hmac = (secret, text) => createHmac('sha256', secret).update(text).digest('hex');
const post = (path, body, headers) => worker.fetch(new Request('https://pay.example' + path, {method: 'POST', body, headers}), env);
const uid = 'AbCdEfGhIjKlMnOpQrSt12345678';
const soon = new Date(Date.now() + 20 * 86400e3).toISOString();

// ---------- 1. payments ----------
let body = JSON.stringify({meta: {event_name: 'subscription_created', custom_data: {uid}}, data: {attributes: {status: 'active', renews_at: soon, urls: {customer_portal: 'https://x.lemonsqueezy.com/billing'}}}});
assert.equal((await post('/lemonsqueezy', body, {'x-signature': hmac('lemon-test', body)})).status, 200);
assert.equal(db.get(`pro/${uid}`).until.timestampValue, soon);
assert.equal(db.get(`pro/${uid}`).portal.stringValue, 'https://x.lemonsqueezy.com/billing');
assert.equal((await post('/lemonsqueezy', body, {'x-signature': hmac('wrong', body)})).status, 401);
body = JSON.stringify({meta: {event_name: 'subscription_expired', custom_data: {uid}}, data: {attributes: {status: 'expired'}}});
await post('/lemonsqueezy', body, {'x-signature': hmac('lemon-test', body)});
assert.ok(Date.parse(db.get(`pro/${uid}`).until.timestampValue) <= Date.now(), 'an expired subscription ends Pro');
const t = Math.floor(Date.now() / 1000), sig = b => `t=${t},v1=${hmac('whsec_test', `${t}.${b}`)}`;
body = JSON.stringify({type: 'checkout.session.completed', data: {object: {mode: 'subscription', subscription: 'sub_1', client_reference_id: uid}}});
assert.equal((await post('/stripe', body, {'stripe-signature': sig(body)})).status, 200);
assert.equal(db.get('billing/stripe_sub_1').uid.stringValue, uid);
const end = t + 30 * 86400;
body = JSON.stringify({type: 'customer.subscription.updated', data: {object: {id: 'sub_1', status: 'active', current_period_end: end}}});
await post('/stripe', body, {'stripe-signature': sig(body)});
assert.equal(db.get(`pro/${uid}`).until.timestampValue, new Date(end * 1000).toISOString());
assert.equal((await post('/stripe', body, {'stripe-signature': `t=${t - 3600},v1=${hmac('whsec_test', `${t - 3600}.${body}`)}`})).status, 401, 'old signatures are refused');
const ts = String(t), psig = b => `ts=${ts};h1=${hmac('paddle-test', `${ts}:${b}`)}`;
body = JSON.stringify({event_type: 'subscription.activated', data: {id: 'pd_1', status: 'active', custom_data: {uid}, current_billing_period: {ends_at: soon}}});
assert.equal((await post('/paddle', body, {'paddle-signature': psig(body)})).status, 200);
assert.equal(db.get(`pro/${uid}`).until.timestampValue, soon);
body = JSON.stringify({event_type: 'subscription.updated', data: {id: 'pd_1', status: 'paused'}});
await post('/paddle', body, {'paddle-signature': psig(body)});
assert.ok(Date.parse(db.get(`pro/${uid}`).until.timestampValue) <= Date.now());
assert.equal((await post('/paddle', body, {'paddle-signature': 'ts=1;h1=00'})).status, 401);
console.log('payments OK (Lemon Squeezy, Stripe, Paddle)');

// ---------- 2. web push encryption: RFC 8291 Appendix A ----------
const rfc = {
  plaintext: 'V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24', asPrivate: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw',
  asPublic: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
  uaPublic: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4', uaPrivate: 'q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94',
  salt: 'DGv6ra1nlYgDCS1FRnbzlw', auth: 'BTBZMqHH6r4Tts7J_aSIgg',
  body: 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN'
};
const asPub = Buffer.from(rfc.asPublic, 'base64url');
const jwk = {kty: 'EC', crv: 'P-256', d: rfc.asPrivate, x: b64u(asPub.subarray(1, 33)), y: b64u(asPub.subarray(33))};
const out = await worker.encryptPush(rfc.uaPublic, rfc.auth, Buffer.from(rfc.plaintext, 'base64url'), {jwk, pub: rfc.asPublic, salt: rfc.salt});
assert.equal(Buffer.from(out).toString('hex'), Buffer.from(rfc.body, 'base64url').toString('hex'), 'matches the RFC 8291 example byte for byte');

// an independent decryption (Node's own crypto) of a fresh random message
function decrypt(bodyBuf, uaEcdh, authB64) {
  const salt = bodyBuf.subarray(0, 16), idlen = bodyBuf[20], keyid = bodyBuf.subarray(21, 21 + idlen), sealed = bodyBuf.subarray(21 + idlen);
  const shared = uaEcdh.computeSecret(keyid), ua = uaEcdh.getPublicKey();
  const ikm = Buffer.from(hkdfSync('sha256', shared, Buffer.from(authB64, 'base64url'), Buffer.concat([Buffer.from('WebPush: info\0'), ua, keyid]), 32));
  const cek = Buffer.from(hkdfSync('sha256', ikm, salt, Buffer.from('Content-Encoding: aes128gcm\0'), 16));
  const nonce = Buffer.from(hkdfSync('sha256', ikm, salt, Buffer.from('Content-Encoding: nonce\0'), 12));
  const d = createDecipheriv('aes-128-gcm', cek, nonce);
  d.setAuthTag(sealed.subarray(sealed.length - 16));
  const plain = Buffer.concat([d.update(sealed.subarray(0, sealed.length - 16)), d.final()]);
  assert.equal(plain[plain.length - 1], 2, 'last-record delimiter');
  return plain.subarray(0, plain.length - 1).toString();
}
const browser = createECDH('prime256v1'); browser.generateKeys();
const browserAuth = b64u(Buffer.from('0123456789abcdef'));
const fresh = await worker.encryptPush(b64u(browser.getPublicKey()), browserAuth, new TextEncoder().encode('hello from the radar'));
assert.equal(decrypt(Buffer.from(fresh), browser, browserAuth), 'hello from the radar');
console.log('web push encryption OK (RFC 8291 example + independent decryption)');

// ---------- 3. trend alerts ----------
const sub = endpoint => JSON.stringify({endpoint, keys: {p256dh: b64u(browser.getPublicKey()), auth: browserAuth}});
db.set('push/userA', {sub: {stringValue: sub('https://push.example/a')}, keys: {stringValue: JSON.stringify(['g:Roblox', 't:#mario'])}, lang: {stringValue: 'th'}});
db.set('push/userB', {sub: {stringValue: sub('https://push.example/gone')}, keys: {stringValue: JSON.stringify(['t:#mario'])}});
db.set('push/userC', {sub: {stringValue: sub('https://push.example/c')}, keys: {stringValue: JSON.stringify(['t:quiet'])}});
trendsJson = {hot: {'t:#mario': [9, 2.8, '#mario'], 'g:Roblox': [85, 2.1, 'Roblox']}};
let waits = [];
const ctx = {waitUntil: p => waits.push(p)};
await worker.scheduled({}, env, ctx); await Promise.all(waits);
assert.equal(calls.github.url, 'https://api.github.com/repos/me/site/actions/workflows/scan.yml/dispatches');
assert.equal(calls.github.auth, 'Bearer ghp_test');
assert.deepEqual(JSON.parse(calls.github.body), {ref: 'main'});
assert.equal(pushed.filter(x => x.url.endsWith('/a')).length, 1, 'one alert, even with two hot trends');
const toA = pushed.find(x => x.url.endsWith('/a'));
const msg = JSON.parse(decrypt(Buffer.from(toA.body), browser, browserAuth));
assert.equal(msg.tag, 'g:Roblox'); assert.match(msg.title, /Roblox/); assert.match(msg.body, /85/); assert.equal(msg.url, '#trend/g%3ARoblox');
const [, jwt, k] = /^vapid t=([^,]+), k=(.+)$/.exec(toA.headers.authorization);
assert.equal(k, vapidPub);
const [vh, vc, vs] = jwt.split('.');
assert.equal(JSON.parse(Buffer.from(vc, 'base64url')).aud, 'https://push.example');
assert.ok(verifySig('sha256', Buffer.from(`${vh}.${vc}`), {key: vapid.publicKey, dsaEncoding: 'ieee-p1363'}, Buffer.from(vs, 'base64url')), 'VAPID signature checks out');
assert.equal(toA.headers['content-encoding'], 'aes128gcm');
assert.ok(!db.has('push/userB'), 'a subscription the browser dropped (410) is deleted');
assert.equal(pushed.filter(x => x.url.endsWith('/c')).length, 0, 'nothing to say about a quiet trend');
const seen = JSON.parse(db.get('push/userA').sent.stringValue);
assert.ok(seen['g:Roblox'] && seen['t:#mario'], 'both hot trends are marked as told');
waits = []; pushed.length = 0;
await worker.scheduled({}, env, ctx); await Promise.all(waits);
assert.equal(pushed.length, 0, 'no repeat alert within 3 days');
console.log('trend alerts + 15-minute refresh OK');

// ---------- 4. AI writer ----------
const rsa = generateKeyPairSync('rsa', {modulusLength: 2048});
idKey = {jwk: {...rsa.publicKey.export({format: 'jwk'}), kid: 'k1', alg: 'RS256', use: 'sig'}};
const idToken = (claims, kid = 'k1') => {
  const h = b64u(JSON.stringify({alg: 'RS256', kid, typ: 'JWT'})), c = b64u(JSON.stringify(claims));
  return `${h}.${c}.${b64u(signRsa('sha256', Buffer.from(`${h}.${c}`), rsa.privateKey))}`;
};
const now = Math.floor(Date.now() / 1000);
const good = idToken({aud: 'demo', iss: 'https://securetoken.google.com/demo', sub: uid, iat: now - 10, exp: now + 3600});
claudeReply = {stop_reason: 'end_turn', content: [{type: 'text', text: JSON.stringify({ideas: [{title: 'Roblox but the floor is lava', hook: 'Wait for it', beats: ['a', 'b', 'c']}]})}]};
const askAi = (token, extraEnv = {}) => worker.fetch(new Request('https://pay.example/ai', {method: 'POST', headers: {authorization: 'Bearer ' + token, 'content-type': 'application/json', origin: 'https://site.example'},
  body: JSON.stringify({topic: 'Roblox', examples: ['Roblox obby', 'Roblox doors'], lang: 'en'})}), {...env, ...extraEnv});
let r = await askAi(good);
assert.equal(r.status, 200);
assert.equal((await r.json()).ideas[0].title, 'Roblox but the floor is lava');
assert.equal(r.headers.get('access-control-allow-origin'), '*');
assert.equal(calls.claude.headers['x-api-key'], 'sk-ant-test');
assert.equal(calls.claude.headers['anthropic-version'], '2026-10-21');
assert.equal(calls.claude.body.model, 'claude-opus-5-5');
assert.equal(calls.claude.body.output_config.format.type, 'json_schema');
assert.equal(calls.claude.body.fallbacks, 'default');
assert.equal((await askAi(idToken({aud: 'other', iss: 'https://securetoken.google.com/other', sub: uid, iat: now, exp: now + 60}))).status, 401, 'a token for another project is refused');
assert.equal((await askAi(idToken({aud: 'demo', iss: 'https://securetoken.google.com/demo', sub: uid, iat: now - 7200, exp: now - 3600}))).status, 401, 'an expired token is refused');
assert.equal((await askAi(good.slice(0, -4) + 'AAAA')).status, 401, 'a forged signature is refused');
assert.equal((await askAi(good, {PRO_GATE: '1'})).status, 402, 'with paid Pro on, the AI writer needs Pro');
assert.equal((await askAi(good)).status, 200);
assert.equal((await askAi(good)).status, 429, 'the daily limit (AI_DAILY) holds');
claudeReply = {stop_reason: 'refusal', content: []};
db.delete(`ailimit/${uid}`);
assert.equal((await askAi(good)).status, 422, 'a refusal is reported, not parsed');
const pre = await worker.fetch(new Request('https://pay.example/ai', {method: 'OPTIONS'}), env);
assert.equal(pre.status, 204); assert.match(pre.headers.get('access-control-allow-headers'), /authorization/);
console.log('AI writer OK (sign-in check, Pro gate, daily limit, refusals, CORS)');
console.log('ALL SERVER CHECKS PASSED');
