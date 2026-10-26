// Viral Radar Pro: payment webhooks -> Firestore pro/{uid}
//
// A Cloudflare Worker (free plan) that receives subscription webhooks from Lemon Squeezy, Stripe or Paddle,
// checks each one's signature, and records who has Pro (and until when) in Firestore pro/{uid}.
// The website only reads that document; nobody can write it from the page.
//
// Endpoints:  POST /lemonsqueezy   POST /stripe   POST /paddle
// Secrets (Workers > Settings > Variables, as encrypted secrets):
//   FIREBASE_SA            the Firebase service account JSON (Project settings > Service accounts > Generate new private key)
//   LEMON_SECRET           Lemon Squeezy webhook signing secret      (only if you use Lemon Squeezy)
//   STRIPE_SECRET          Stripe webhook signing secret (whsec_...)  (only if you use Stripe)
//   PADDLE_SECRET          Paddle notification secret key             (only if you use Paddle)
// The checkout link must carry the visitor's Firebase uid (the site fills {uid} in CHECKOUT_URL):
//   Lemon Squeezy  ...?checkout[custom][uid]={uid}&checkout[email]={email}
//   Stripe         https://buy.stripe.com/...?client_reference_id={uid}&prefilled_email={email}
//   Paddle         pass customData: {uid} when opening the checkout
// See web/README.md, "Viral Radar Pro".

const enc = new TextEncoder();
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
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

export default {
  async fetch(req, env) {
    if (req.method !== 'POST') return new Response('Viral Radar payments: OK', {status: 200});
    const body = await req.text(), path = new URL(req.url).pathname.replace(/\/+$/, '');
    try {
      if (path === '/lemonsqueezy' && env.LEMON_SECRET) return await lemon(req, env, body);
      if (path === '/stripe' && env.STRIPE_SECRET) return await stripe(req, env, body);
      if (path === '/paddle' && env.PADDLE_SECRET) return await paddle(req, env, body);
      return json({error: 'unknown endpoint'}, 404);
    } catch (err) {
      return json({error: String(err.message || err).slice(0, 200)}, 500);   // the provider retries failed webhooks
    }
  }
};
