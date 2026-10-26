// Checks worker.js without any real account: signed test webhooks from each provider, a fake Google token server
// and a fake Firestore. Run: node web/pay/selftest.mjs
import {generateKeyPairSync, createHmac} from 'node:crypto';
import assert from 'node:assert/strict';
import worker from './worker.js';

const {privateKey} = generateKeyPairSync('rsa', {modulusLength: 2048});
const env = {
  FIREBASE_SA: JSON.stringify({project_id: 'demo', client_email: 'sa@demo.iam.gserviceaccount.com', private_key: privateKey.export({type: 'pkcs8', format: 'pem'})}),
  LEMON_SECRET: 'lemon-test', STRIPE_SECRET: 'whsec_test', PADDLE_SECRET: 'paddle-test'
};
const db = new Map();
globalThis.fetch = async (url, opts = {}) => {   // Google's token endpoint and Firestore, in memory
  if (url.startsWith('https://oauth2.googleapis.com/token')) return new Response(JSON.stringify({access_token: 'tok', expires_in: 3600}));
  const m = /documents\/([^?]+)/.exec(url);
  assert.equal(opts.headers.authorization, 'Bearer tok');
  if (opts.method === 'GET') return db.has(m[1]) ? new Response(JSON.stringify({fields: db.get(m[1])})) : new Response('{}', {status: 404});
  db.set(m[1], Object.assign(db.get(m[1]) || {}, JSON.parse(opts.body).fields));
  return new Response('{}');
};
const hmac = (secret, text) => createHmac('sha256', secret).update(text).digest('hex');
const post = (path, body, headers) => worker.fetch(new Request('https://pay.example' + path, {method: 'POST', body, headers}), env);
const uid = 'AbCdEfGhIjKlMnOpQrSt12345678';
const soon = new Date(Date.now() + 20 * 86400e3).toISOString();

// Lemon Squeezy: subscription created, then a forged copy is refused
let body = JSON.stringify({meta: {event_name: 'subscription_created', custom_data: {uid}}, data: {attributes: {status: 'active', renews_at: soon, urls: {customer_portal: 'https://x.lemonsqueezy.com/billing'}}}});
assert.equal((await post('/lemonsqueezy', body, {'x-signature': hmac('lemon-test', body)})).status, 200);
assert.equal(db.get(`pro/${uid}`).until.timestampValue, soon);
assert.equal(db.get(`pro/${uid}`).portal.stringValue, 'https://x.lemonsqueezy.com/billing');
assert.equal((await post('/lemonsqueezy', body, {'x-signature': hmac('wrong', body)})).status, 401);
body = JSON.stringify({meta: {event_name: 'subscription_expired', custom_data: {uid}}, data: {attributes: {status: 'expired'}}});
await post('/lemonsqueezy', body, {'x-signature': hmac('lemon-test', body)});
assert.ok(Date.parse(db.get(`pro/${uid}`).until.timestampValue) <= Date.now(), 'an expired subscription ends Pro');
console.log('lemon squeezy OK');

// Stripe: checkout links the subscription to the account, later updates find it
const t = Math.floor(Date.now() / 1000), sig = b => `t=${t},v1=${hmac('whsec_test', `${t}.${b}`)}`;
body = JSON.stringify({type: 'checkout.session.completed', data: {object: {mode: 'subscription', subscription: 'sub_1', client_reference_id: uid}}});
assert.equal((await post('/stripe', body, {'stripe-signature': sig(body)})).status, 200);
assert.equal(db.get('billing/stripe_sub_1').uid.stringValue, uid);
const end = t + 30 * 86400;
body = JSON.stringify({type: 'customer.subscription.updated', data: {object: {id: 'sub_1', status: 'active', current_period_end: end}}});
await post('/stripe', body, {'stripe-signature': sig(body)});
assert.equal(db.get(`pro/${uid}`).until.timestampValue, new Date(end * 1000).toISOString());
assert.equal((await post('/stripe', body, {'stripe-signature': `t=${t - 3600},v1=${hmac('whsec_test', `${t - 3600}.${body}`)}`})).status, 401, 'old signatures are refused');
console.log('stripe OK');

// Paddle
const ts = String(t), psig = b => `ts=${ts};h1=${hmac('paddle-test', `${ts}:${b}`)}`;
body = JSON.stringify({event_type: 'subscription.activated', data: {id: 'pd_1', status: 'active', custom_data: {uid}, current_billing_period: {ends_at: soon}}});
assert.equal((await post('/paddle', body, {'paddle-signature': psig(body)})).status, 200);
assert.equal(db.get(`pro/${uid}`).until.timestampValue, soon);
body = JSON.stringify({event_type: 'subscription.updated', data: {id: 'pd_1', status: 'paused'}});   // no uid in the event: found through the subscription
await post('/paddle', body, {'paddle-signature': psig(body)});
assert.ok(Date.parse(db.get(`pro/${uid}`).until.timestampValue) <= Date.now());
assert.equal((await post('/paddle', body, {'paddle-signature': 'ts=1;h1=00'})).status, 401);
console.log('paddle OK');
console.log('ALL PAYMENT CHECKS PASSED');
