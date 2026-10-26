// Makes the web push (VAPID) key pair that trend alerts need. Run once: node web/pay/vapid.mjs
import {generateKeyPairSync} from 'node:crypto';

const jwk = generateKeyPairSync('ec', {namedCurve: 'P-256'}).privateKey.export({format: 'jwk'});
const pub = Buffer.concat([Buffer.from([4]), Buffer.from(jwk.x, 'base64url'), Buffer.from(jwk.y, 'base64url')]).toString('base64url');
console.log('VAPID_PUBLIC_KEY  (a GitHub repository variable AND a worker variable):\n' + pub + '\n');
console.log('VAPID_PRIVATE_JWK (a worker secret only; never put it anywhere public):\n' + JSON.stringify({kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y, d: jwk.d}));
