import cf from 'cloudfront';
import crypto from 'crypto';

const store = cf.kvs();
const domain = '__FILES_DOMAIN__';

function deny(status) {
    return {statusCode: status, headers: {
        'content-security-policy': {value: "default-src 'none'; sandbox"},
        'x-content-type-options': {value: 'nosniff'},
        'referrer-policy': {value: 'no-referrer'},
        'cache-control': {value: 'no-store'}
    }};
}

function one(query, name) {
    const item = query[name];
    return item && !item.multiValue && typeof item.value === 'string' ? item.value : '';
}

function equal(a, b) {
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
}

function matches(pattern, text) {
    // JS $ alone also accepts a final newline; wire fields must match in full.
    const match = pattern.exec(text);
    return match !== null && match[0] === text;
}

async function handler(event) {
    const request = event.request;
    const path = request.uri;
    if (!matches(/^\/(blobs|visuals)\/([0-9a-f]{2})\/(\2[0-9a-f]{62})$/, path)) return deny(404);
    if (request.method !== 'GET' && request.method !== 'HEAD') return deny(403);
    const hostHeader = request.headers.host;
    const host = hostHeader && !hostHeader.multiValue ? hostHeader.value.toLowerCase() : '';
    const label = host.slice(0, -(domain.length + 1));
    if (!host.endsWith('.' + domain) || !matches(/^[a-z0-9](?:[a-z0-9-]{0,55}[a-z0-9])?-files$/, label)) return deny(403);
    const query = request.querystring;
    const expiry = one(query, 'e');
    const key = one(query, 'k');
    const signature = one(query, 's');
    const now = Math.floor(Date.now() / 1000);
    if (!matches(/^[0-9]{1,12}$/, expiry) || Number(expiry) <= now || Number(expiry) > now + 300 ||
        key !== 'k1' || !matches(/^[A-Za-z0-9_-]{43}$/, signature)) return deny(403);
    try {
        // Only HQ writes this mapping; object writers never choose the tenant prefix or key.
        // CloudFront's runtime refuses `await` inside call arguments (SyntaxError at publish time).
        const stored = await store.get(label);
        const company = JSON.parse(stored);
        if (company.v !== 1 || typeof company.prefix !== 'string' ||
            !matches(/^c\/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\/$/, company.prefix) ||
            company.signing.key_id !== key || typeof company.signing.secret !== 'string' ||
            !matches(/^[0-9a-f]{64}$/, company.signing.secret)) return deny(403);
        const expected = crypto.createHmac('sha256', company.signing.secret)
            .update(host + '\n' + path + '\n' + expiry).digest('base64url');
        if (!equal(expected, signature)) return deny(403);
        request.uri = '/' + company.prefix.slice(0, -1) + path;
        // Do not forward cookies, signatures or S3 response-header override parameters.
        request.querystring = {};
        request.cookies = {};
        return request;
    } catch (error) {
        return deny(403);
    }
}
