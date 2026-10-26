// Viral Radar's small server: one Cloudflare Worker (free plan) for everything the static site can't do by itself.
//
//   1. Payments      POST /lemonsqueezy  /stripe  /paddle   signed subscription webhooks -> Firestore pro/{uid}
//   2. Fresh data    every 15 minutes (a Cron Trigger) it starts the GitHub workflow that rescans and republishes the site
//   3. Trend alerts  on the same timer, sends a web push to everyone whose tracked trends just took off
//   4. AI writer     POST /ai   Short ideas for a trend, written by Claude, for signed-in (Pro) users
//
// Secrets and variables (Workers > Settings > Variables; mark keys and secrets as encrypted). Each part switches on
// only when its own settings are present:
//   FIREBASE_SA            Firebase service account JSON (Project settings > Service accounts > Generate new private key)   [1, 3, 4]
//   LEMON_SECRET / STRIPE_SECRET / PADDLE_SECRET   webhook signing secret of each payment provider you use               [1]
//   GITHUB_TOKEN           fine-grained token with "Actions: read and write" on the site's repository                     [2]
//   GITHUB_REPO            owner/name, for example sparyryan-collab/outlier-radar                                           [2]
//   SITE_URL               the site's address, for example https://sparyryan-collab.github.io/outlier-radar                 [3]
//   VAPID_PUBLIC_KEY, VAPID_PRIVATE_JWK, VAPID_SUBJECT   web push keys (node web/pay/vapid.mjs makes them) and a mailto: [3]
//   ANTHROPIC_API_KEY      Claude API key                                                                                    [4]
//   AI_MODEL (optional, default claude-opus-5-5), AI_DAILY (optional, ideas requests per user per day, default 20),
//   PRO_GATE ("1" when Pro is paid; the AI writer then needs Pro), ALLOWED_ORIGIN (the site's origin, for CORS)        [4]
// The checkout link must carry the visitor's Firebase uid (the site fills {uid} in CHECKOUT_URL):
//   Lemon Squeezy  ...?checkout[custom][uid]={uid}&checkout[email]={email}
//   Stripe         https://buy.stripe.com/...?client_reference_id={uid}&prefilled_email={email}
//   Paddle         pass customData: {uid} when opening the checkout
// See web/README.md ("Viral Radar Pro" and "The server worker").

const enc = new TextEncoder();
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64url = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - s.length % 4) % 4)), c => c.charCodeAt(0));
const concat = (...parts) => { const out = new Uint8Array(parts.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of parts) { out.set(x, i); i += x.length; } return out; };
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {status, headers: {'content-type': 'application/json'}});

async function hmacHex(secret, text) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode(text)));
}
function sameText(a, b) {   // constant-time comparison of two signatures
  if (!a || !b || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

/* ---------- Firestore through a service account (bypasses the security rules, so only this worker can grant Pro) ---------- */
let cachedToken = null;
async function googleToken(sa) {
  if (cachedToken && cachedToken.exp > Date.now() + 60e3) return cachedToken.token;
  const now = Math.floor(Date.now() / 1000);
  const head = b64url(enc.encode(JSON.stringify({alg: 'RS256', typ: 'JWT'})));
  const claim = b64url(enc.encode(JSON.stringify({iss: sa.client_email, scope: 'https://www.googleapis.com/auth/datastore',
    aud: 'https://oauth2.googleapis.com/token', iat: now, exp: now + 3600})));
  const pem = sa.private_key.replace(/-----[^-]+-----/g, '').replace(/\s+/g, '');
  const der = Uint8Array.from(atob(pem), c => c.charCodeAt(0));
  const key = await crypto.subtle.importKey('pkcs8', der, {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['sign']);
  const sig = b64url(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, enc.encode(`${head}.${claim}`)));
  const r = await fetch('https://oauth2.googleapis.com/token', {method: 'POST', headers: {'content-type': 'application/x-www-form-urlencoded'},
    body: `grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=${head}.${claim}.${sig}`});
  const data = await r.json();
  if (!data.access_token) throw new Error('could not get a Google token');
  cachedToken = {token: data.access_token, exp: Date.now() + (data.expires_in || 3600) * 1000};
  return cachedToken.token;
}
async function firestore(env, method, path, fields) {
  const sa = JSON.parse(env.FIREBASE_SA);
  const base = `https://firestore.googleapis.com/v1/projects/${sa.project_id}/databases/(default)/documents/${path}`;
  const url = fields ? base + '?' + Object.keys(fields).map(f => 'updateMask.fieldPaths=' + encodeURIComponent(f)).join('&') : base;
  const r = await fetch(url, {method, headers: {authorization: 'Bearer ' + (await googleToken(sa)), 'content-type': 'application/json'},
    body: fields ? JSON.stringify({fields}) : undefined});
  if (r.status === 404 && method === 'GET') return null;
  if (!r.ok) throw new Error(`Firestore ${method} ${path}: ${r.status}`);
  return r.json();
}
const str = v => ({stringValue: String(v || '')});
async function setPro(env, uid, untilMs, provider, extra = {}) {
  if (!/^[A-Za-z0-9]{10,64}$/.test(uid || '')) throw new Error('missing or odd uid');
  const fields = {until: {timestampValue: new Date(untilMs).toISOString()}, provider: str(provider), updated: {timestampValue: new Date().toISOString()}};
  for (const [k, v] of Object.entries(extra)) if (v) fields[k] = str(v);
  await firestore(env, 'PATCH', `pro/${uid}`, fields);
}
const remember = (env, key, uid) => firestore(env, 'PATCH', `billing/${key}`, {uid: str(uid)});   // which account a subscription belongs to
async function recall(env, key) { const d = await firestore(env, 'GET', `billing/${key}`); return d && d.fields && d.fields.uid ? d.fields.uid.stringValue : ''; }
const day = 86400e3;

/* ---------- Lemon Squeezy ---------- */
async function lemon(req, env, body) {
  if (!sameText(req.headers.get('x-signature') || '', await hmacHex(env.LEMON_SECRET, body))) return json({error: 'bad signature'}, 401);
  const e = JSON.parse(body), a = (e.data || {}).attributes || {}, uid = ((e.meta || {}).custom_data || {}).uid;
  const name = (e.meta || {}).event_name || '';
  if (!name.startsWith('subscription_')) return json({ok: true, ignored: name});
  const active = ['active', 'on_trial', 'past_due', 'cancelled'].includes(a.status);   // cancelled keeps Pro until the paid period ends
  const until = active ? Date.parse(a.ends_at || a.renews_at) || Date.now() + 31 * day : Date.now();
  await setPro(env, uid, until, 'lemonsqueezy', {portal: (a.urls || {}).customer_portal, status: a.status});
  return json({ok: true});
}

/* ---------- Stripe ---------- */
async function stripe(req, env, body) {
  const parts = Object.fromEntries((req.headers.get('stripe-signature') || '').split(',').map(x => x.split('=')));
  if (!parts.t || Math.abs(Date.now() / 1000 - +parts.t) > 300 || !sameText(parts.v1 || '', await hmacHex(env.STRIPE_SECRET, `${parts.t}.${body}`))) return json({error: 'bad signature'}, 401);
  const e = JSON.parse(body), o = (e.data || {}).object || {};
  if (e.type === 'checkout.session.completed' && o.mode === 'subscription') {
    await remember(env, 'stripe_' + o.subscription, o.client_reference_id);
    await setPro(env, o.client_reference_id, Date.now() + 31 * day, 'stripe', {status: 'active'});
  } else if (e.type === 'customer.subscription.updated' || e.type === 'customer.subscription.deleted') {
    const uid = await recall(env, 'stripe_' + o.id);
    if (uid) {
      const live = ['active', 'trialing', 'past_due'].includes(o.status);
      await setPro(env, uid, live ? (o.current_period_end || o.items?.data?.[0]?.current_period_end || 0) * 1000 || Date.now() + 31 * day : Date.now(), 'stripe', {status: o.status});
    }
  }
  return json({ok: true});
}

/* ---------- Paddle ---------- */
async function paddle(req, env, body) {
  const parts = Object.fromEntries((req.headers.get('paddle-signature') || '').split(';').map(x => x.split('=')));
  if (!parts.ts || !sameText(parts.h1 || '', await hmacHex(env.PADDLE_SECRET, `${parts.ts}:${body}`))) return json({error: 'bad signature'}, 401);
  const e = JSON.parse(body), d = e.data || {};
  if (!String(e.event_type || '').startsWith('subscription.')) return json({ok: true, ignored: e.event_type});
  let uid = (d.custom_data || {}).uid;
  if (uid) await remember(env, 'paddle_' + d.id, uid); else uid = await recall(env, 'paddle_' + d.id);
  const live = ['active', 'trialing', 'past_due'].includes(d.status) || (d.status === 'canceled' && d.current_billing_period);
  const until = live ? Date.parse((d.current_billing_period || {}).ends_at) || Date.now() + 31 * day : Date.now();
  await setPro(env, uid, until, 'paddle', {status: d.status});
  return json({ok: true});
}


/* ---------- 2. fresh data: start the GitHub workflow (GitHub's own timer only runs it about every 2 hours) ---------- */
async function refreshSite(env) {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/scan.yml/dispatches`, {method: 'POST',
    headers: {authorization: 'Bearer ' + env.GITHUB_TOKEN, accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28', 'user-agent': 'viral-radar-worker'},
    body: JSON.stringify({ref: 'main'})});
  if (r.status !== 204) throw new Error('GitHub said ' + r.status);
}

/* ---------- 3. trend alerts: web push (RFC 8291 message encryption, RFC 8292 VAPID) ---------- */
async function hkdf(salt, ikm, info, bytes) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({name: 'HKDF', hash: 'SHA-256', salt, info}, key, bytes * 8));
}
async function encryptPush(p256dh, auth, plaintext, testKeys) {   // testKeys (fixed sender key and salt) only for the self-test
  const ua = unb64url(p256dh), secret = unb64url(auth);
  let sender, senderPub, salt;
  if (testKeys) {
    sender = await crypto.subtle.importKey('jwk', testKeys.jwk, {name: 'ECDH', namedCurve: 'P-256'}, false, ['deriveBits']);
    senderPub = unb64url(testKeys.pub); salt = unb64url(testKeys.salt);
  } else {
    const pair = await crypto.subtle.generateKey({name: 'ECDH', namedCurve: 'P-256'}, true, ['deriveBits']);
    sender = pair.privateKey; senderPub = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey)); salt = crypto.getRandomValues(new Uint8Array(16));
  }
  const uaKey = await crypto.subtle.importKey('raw', ua, {name: 'ECDH', namedCurve: 'P-256'}, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({name: 'ECDH', public: uaKey}, sender, 256));
  const ikm = await hkdf(secret, shared, concat(enc.encode('WebPush: info\0'), ua, senderPub), 32);
  const cek = await hkdf(salt, ikm, enc.encode('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, enc.encode('Content-Encoding: nonce\0'), 12);
  const key = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const sealed = new Uint8Array(await crypto.subtle.encrypt({name: 'AES-GCM', iv: nonce}, key, concat(plaintext, new Uint8Array([2]))));   // 2 = last record
  const header = new Uint8Array(21); header.set(salt); new DataView(header.buffer).setUint32(16, 4096); header[20] = senderPub.length;
  return concat(header, senderPub, sealed);
}
async function vapidAuth(endpoint, env) {
  const key = await crypto.subtle.importKey('jwk', JSON.parse(env.VAPID_PRIVATE_JWK), {name: 'ECDSA', namedCurve: 'P-256'}, false, ['sign']);
  const head = b64url(enc.encode(JSON.stringify({typ: 'JWT', alg: 'ES256'})));
  const claim = b64url(enc.encode(JSON.stringify({aud: new URL(endpoint).origin, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: env.VAPID_SUBJECT || 'mailto:alerts@example.com'})));
  const sig = b64url(await crypto.subtle.sign({name: 'ECDSA', hash: 'SHA-256'}, key, enc.encode(`${head}.${claim}`)));
  return `vapid t=${head}.${claim}.${sig}, k=${env.VAPID_PUBLIC_KEY}`;
}
async function sendPush(sub, message, env) {
  const body = await encryptPush(sub.keys.p256dh, sub.keys.auth, enc.encode(JSON.stringify(message)));
  return fetch(sub.endpoint, {method: 'POST', body, headers: {authorization: await vapidAuth(sub.endpoint, env), ttl: '86400', urgency: 'normal',
    'content-encoding': 'aes128gcm', 'content-type': 'application/octet-stream'}});
}
const ALERT_TEXT = {
  en: (label, h) => ({title: `${label} is taking off`, body: `${h[0]} new Shorts in 3 days, ${h[1]}× more than usual. Ride it now.`}),
  th: (label, h) => ({title: `${label} กำลังมาแรง`, body: `คลิปใหม่ ${h[0]} คลิปใน 3 วัน มากกว่าปกติ ${h[1]} เท่า รีบทำตามเทรนด์เลย`})
};
async function sendAlerts(env) {   // trends.json carries the scanner's list of hot trends; each subscriber hears about a trend once every 3 days at most
  const trends = await (await fetch(env.SITE_URL.replace(/\/$/, '') + '/trends.json', {cf: {cacheTtl: 0}})).json();
  const hot = trends.hot || {}, now = Date.now();
  let page = '', sent = 0;
  do {
    const list = await firestore(env, 'GET', `push?pageSize=200${page ? '&pageToken=' + encodeURIComponent(page) : ''}`);
    for (const d of (list && list.documents) || []) {
      const f = d.fields || {}, uid = d.name.split('/').pop();
      let sub, keys, seen;
      try { sub = JSON.parse(f.sub.stringValue); keys = JSON.parse((f.keys || {}).stringValue || '[]'); seen = JSON.parse((f.sent || {}).stringValue || '{}'); } catch (e) { continue; }
      const fresh = keys.filter(k => hot[k] && !(seen[k] > now - 3 * day));
      if (!fresh.length) continue;
      const k = fresh[0], h = hot[k], text = (ALERT_TEXT[(f.lang || {}).stringValue] || ALERT_TEXT.en)(h[2], h);
      const r = await sendPush(sub, {...text, tag: k, url: '#trend/' + encodeURIComponent(k)}, env);
      if (r.status === 404 || r.status === 410) { await firestore(env, 'DELETE', `push/${uid}`); continue; }   // the browser unsubscribed
      fresh.forEach(x => { seen[x] = now; });
      for (const x of Object.keys(seen)) if (seen[x] < now - 7 * day) delete seen[x];
      await firestore(env, 'PATCH', `push/${uid}`, {sent: str(JSON.stringify(seen))});
      sent++;
    }
    page = list && list.nextPageToken;
  } while (page);
  return sent;
}

/* ---------- 4. AI writer: Short ideas for a trend (Claude, through the Messages API) ---------- */
let googleKeys = null;
async function firebaseUser(req, env) {   // checks the visitor's Firebase sign-in token; returns their uid
  const token = (req.headers.get('authorization') || '').replace(/^Bearer\s+/i, '');
  const [h, c, sig] = token.split('.');
  if (!sig) return null;
  const head = JSON.parse(new TextDecoder().decode(unb64url(h))), claim = JSON.parse(new TextDecoder().decode(unb64url(c)));
  const project = JSON.parse(env.FIREBASE_SA).project_id, now = Date.now() / 1000;
  if (head.alg !== 'RS256' || claim.aud !== project || claim.iss !== 'https://securetoken.google.com/' + project || !(claim.exp > now) || !(claim.iat < now + 300) || !claim.sub) return null;
  if (!googleKeys || googleKeys.exp < Date.now()) {
    const r = await fetch('https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com');
    googleKeys = {keys: (await r.json()).keys || [], exp: Date.now() + 3600e3};
  }
  const jwk = googleKeys.keys.find(k => k.kid === head.kid);
  if (!jwk) return null;
  const key = await crypto.subtle.importKey('jwk', jwk, {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['verify']);
  return await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, unb64url(sig), enc.encode(`${h}.${c}`)) ? claim.sub : null;
}
const IDEAS_SCHEMA = {type: 'object', additionalProperties: false, required: ['ideas'], properties: {ideas: {type: 'array', items: {
  type: 'object', additionalProperties: false, required: ['title', 'hook', 'beats'],
  properties: {title: {type: 'string'}, hook: {type: 'string'}, beats: {type: 'array', items: {type: 'string'}}}}}}};
async function writeIdeas(input, env) {
  const lang = input.lang === 'th' ? 'Thai' : 'English';
  const examples = (Array.isArray(input.examples) ? input.examples : []).slice(0, 8).map(x => '- ' + String(x).slice(0, 140)).join('\n');
  const r = await fetch('https://api.anthropic.com/v1/messages', {method: 'POST',
    headers: {'content-type': 'application/json', 'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2026-10-21', 'anthropic-beta': 'server-side-fallback-2026-07-01'},
    body: JSON.stringify({
      model: env.AI_MODEL || 'claude-opus-5-5', max_tokens: 4000, fallbacks: 'default',
      output_config: {effort: 'low', format: {type: 'json_schema', schema: IDEAS_SCHEMA}},
      system: 'You help small YouTube Shorts creators ride trends. Write original ideas a solo creator can film cheaply with a phone, '
        + 'honest and safe for all ages. Never copy the example titles; use them only to understand the trend.',
      messages: [{role: 'user', content: `Trend: ${String(input.topic || '').slice(0, 120)}\n`
        + (examples ? `Shorts currently riding it (titles from YouTube, for context only):\n${examples}\n` : '')
        + `\nWrite 5 Short ideas in ${lang}. For each: a title under 70 characters, the hook line said in the first second, and 3 short beats for the rest of the video.`}]
    })});
  if (r.status === 429 || r.status >= 500) return {status: 503, body: {error: 'busy'}};
  const msg = await r.json();
  if (!r.ok) return {status: 502, body: {error: 'ai_failed'}};
  if (msg.stop_reason === 'refusal') return {status: 422, body: {error: 'refused'}};
  const text = (msg.content || []).filter(b => b.type === 'text').map(b => b.text).join('');
  try { return {status: 200, body: JSON.parse(text)}; } catch (e) { return {status: 502, body: {error: 'ai_failed'}}; }
}
async function ai(req, env) {
  const uid = await firebaseUser(req, env);
  if (!uid) return {status: 401, body: {error: 'sign_in'}};
  if (env.PRO_GATE === '1') {
    const pro = await firestore(env, 'GET', `pro/${uid}`);
    const until = pro && pro.fields && pro.fields.until ? Date.parse(pro.fields.until.timestampValue) : 0;
    if (!(until > Date.now())) return {status: 402, body: {error: 'pro'}};
  }
  const today = new Date().toISOString().slice(0, 10), lim = await firestore(env, 'GET', `ailimit/${uid}`);
  const used = lim && lim.fields && lim.fields.d && lim.fields.d.stringValue === today ? +lim.fields.n.integerValue : 0;
  if (used >= +(env.AI_DAILY || 20)) return {status: 429, body: {error: 'limit'}};
  const out = await writeIdeas(await req.json(), env);
  if (out.status === 200) await firestore(env, 'PATCH', `ailimit/${uid}`, {d: str(today), n: {integerValue: String(used + 1)}});
  return out;
}
const cors = env => ({'access-control-allow-origin': env.ALLOWED_ORIGIN || '*', 'access-control-allow-methods': 'POST, OPTIONS',
  'access-control-allow-headers': 'authorization, content-type', 'access-control-max-age': '86400', vary: 'origin'});

export default {
  async fetch(req, env) {
    const path = new URL(req.url).pathname.replace(/\/+$/, '');
    if (path === '/ai') {   // called from the site itself, so it needs CORS
      if (req.method === 'OPTIONS') return new Response(null, {status: 204, headers: cors(env)});
      if (req.method !== 'POST' || !env.ANTHROPIC_API_KEY) return new Response(null, {status: 404, headers: cors(env)});
      try {
        const out = await ai(req, env);
        return new Response(JSON.stringify(out.body), {status: out.status, headers: {...cors(env), 'content-type': 'application/json'}});
      } catch (err) {
        return new Response(JSON.stringify({error: 'server'}), {status: 500, headers: {...cors(env), 'content-type': 'application/json'}});
      }
    }
    if (req.method !== 'POST') return new Response('Viral Radar server: OK', {status: 200});
    const body = await req.text();
    try {
      if (path === '/lemonsqueezy' && env.LEMON_SECRET) return await lemon(req, env, body);
      if (path === '/stripe' && env.STRIPE_SECRET) return await stripe(req, env, body);
      if (path === '/paddle' && env.PADDLE_SECRET) return await paddle(req, env, body);
      return json({error: 'unknown endpoint'}, 404);
    } catch (err) {
      return json({error: String(err.message || err).slice(0, 200)}, 500);   // the provider retries failed webhooks
    }
  },
  encryptPush,   // exposed for web/pay/selftest.mjs
  async scheduled(event, env, ctx) {   // the Cron Trigger: every 15 minutes
    if (env.GITHUB_TOKEN && env.GITHUB_REPO) ctx.waitUntil(refreshSite(env).catch(e => console.log('refresh failed:', e.message)));
    if (env.VAPID_PRIVATE_JWK && env.FIREBASE_SA && env.SITE_URL) ctx.waitUntil(sendAlerts(env).catch(e => console.log('alerts failed:', e.message)));
  }
};
