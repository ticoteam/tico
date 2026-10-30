// Synthetic fixtures only; execute exactly the function code rendered into CloudFormation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const template = JSON.parse(execFileSync('python3', [path.join(__dirname, 'template.py')]));
const resources = template.Resources;
const host = 'acme-files.files.example';
const label = 'acme-files';
const install = '0f1e2d3c-4b5a-4968-8776-655443322110';
const otherInstall = 'ffeeddcc-bbaa-4987-8765-443322110000';
const secret = crypto.createHmac('sha256', 'm'.repeat(32)).update('ticofiles:' + install).digest('hex');
const record = {v: 1, prefix: 'c/' + install + '/', signing: {key_id: 'k1', secret}};
const hash = crypto.createHash('sha256').update('clean bytes').digest('hex');
const visualPath = '/visuals/' + hash.slice(0, 2) + '/' + hash;
const blobPath = visualPath.replace('/visuals/', '/blobs/');
const now = 1799999700;
const copy = obj => JSON.parse(JSON.stringify(obj));

function load(name, mapping = {[label]: record}) {
    let source = resources[name].Properties.FunctionCode;
    if (typeof source !== 'string') source = source['Fn::Join'][1].map(x => typeof x === 'string' ? x : 'files.example').join('');
    assert.ok(Buffer.byteLength(source) < 10240, 'CloudFront function size');
    // CloudFront's runtime rejects `await` in call arguments at run time ("await in arguments not supported").
    assert.ok(!/[(,[]\s*await\b/.test(source), 'no await inside call arguments');
    source = source.replace("import cf from 'cloudfront';", '').replace("import crypto from 'crypto';", '');
    const context = vm.createContext({crypto, Date: {now: () => now * 1000}, cf: {kvs: () => ({get: async key => {
        if (!(key in mapping)) throw Error('unknown');
        return JSON.stringify(mapping[key]);
    }})}});
    vm.runInContext(source, context);
    return async event => copy(await context.handler(copy(event)));
}

function request(uri = visualPath, address = host, expiry = now + 300) {
    const e = String(expiry);
    const s = crypto.createHmac('sha256', secret).update(address + '\n' + uri + '\n' + e).digest('base64url');
    return {method: 'GET', uri, headers: {host: {value: address}}, cookies: {session: {value: 'do-not-forward'}},
        querystring: {e: {value: e}, k: {value: 'k1'}, s: {value: s}}};
}

const script = "'sha256-" + crypto.createHash('sha256').update('reporter()').digest('base64') + "'";
function origin(kind = 'text/html') {
    return {statusCode: 200, headers: {
        'content-type': {value: kind},
        'x-amz-meta-tico-visual-policy-version': {value: '1'},
        'x-amz-meta-tico-visual-ancestors': {value: "'self' https://app.example http://localhost:8765"},
        'x-amz-meta-tico-visual-script': {value: script}}, body: {encoding: 'text', data: '<p>clean visual</p>'}};
}
function csp(response) { return response.headers['content-security-policy'].value; }
function ceiling(response) {
    assert.match(csp(response), /(?:^|;)\s*sandbox(?:;| |$)/);
    assert.ok(!csp(response).includes('allow-same-origin'));
    assert.equal(response.headers['content-security-policy'].multiValue, undefined);
    assert.equal(response.headers['content-security-policy-report-only'], undefined);
    assert.equal(response.headers['set-cookie'], undefined);
    assert.deepEqual(response.cookies, {});
    assert.equal(response.headers['x-content-type-options'].value, 'nosniff');
    assert.equal(response.headers['referrer-policy'].value, 'no-referrer');
    assert.equal(response.headers['cross-origin-resource-policy'].value, 'cross-origin');
}

test('signed namespace and tenant boundaries hold before every origin or cache access', async () => {
    const gate = load('Gate');
    for (const uri of [visualPath, blobPath]) {
        const req = request(uri);
        req.querystring['response-content-type'] = {value: 'application/javascript'};
        const out = await gate({request: req});
        assert.equal(out.uri, '/' + record.prefix.slice(0, -1) + uri);
        assert.deepEqual(out.querystring, {});
        assert.deepEqual(out.cookies, {});
    }
    const attacks = [
        {...request(), querystring: {}}, request(visualPath, host, now), request(visualPath, host, now - 1),
        request(visualPath, host, now + 301), request(visualPath, 'other-files.files.example'),
        {...request(), method: 'POST'}, {...request(), uri: blobPath},
        request('/c/acme' + visualPath), request(visualPath.replace(hash.slice(0, 2), 'ff')),
        request(visualPath.toUpperCase()), request(visualPath + '/'), request('/visuals/../blobs/' + hash),
        request(visualPath + '\n'), request(visualPath, host + '\n'),
    ];
    for (const field of ['e', 'k', 's']) {
        const duplicate = request();
        duplicate.querystring[field].multiValue = [{value: duplicate.querystring[field].value}, {value: 'bad'}];
        attacks.push(duplicate);
        const tamper = request();
        tamper.querystring[field].value = field === 'k' ? 'v2' : 'bad';
        attacks.push(tamper);
        const newline = request(); newline.querystring[field].value += '\n'; attacks.push(newline);
    }
    for (const req of attacks) {
        const out = await gate({request: req});
        assert.ok([403, 404].includes(out.statusCode), JSON.stringify(req));
        assert.equal(csp(out), "default-src 'none'; sandbox");
        assert.equal(out.headers['x-content-type-options'].value, 'nosniff');
    }
    const otherHost = 'bravo-files.files.example';
    const both = load('Gate', {[label]: record, 'bravo-files': {...record, prefix: 'c/' + otherInstall + '/'}});
    assert.equal((await both({request: {...request(), headers: {host: {value: otherHost}}}})).statusCode, 403);
    assert.equal((await both({request: request(visualPath, otherHost)})).uri, '/c/' + otherInstall + visualPath);
    for (const invalid of [{...record, prefix: 'c/acme/../bravo/'}, {...record, v: 2}, {...record, v: undefined},
        {...record, signing: {key_id: 'k1', secret: secret + '\n'}}, {...record, prefix: record.prefix + '\n'}]) {
        assert.equal((await load('Gate', {[label]: invalid})({request: request()})).statusCode, 403);
    }
    const upperHost = request(); upperHost.headers.host.value = host.toUpperCase();
    assert.equal((await gate({request: upperHost})).uri, '/c/' + install + visualPath);
    const empty = crypto.createHash('sha256').update('').digest('hex');
    const vectorPath = '/blobs/' + empty.slice(0, 2) + '/' + empty;
    const vector = request(vectorPath);
    assert.equal(secret, 'de31dde3b05eca70b7810ba99571bdc191f64914414dc8541aa9d2909a3d8867');
    assert.equal(vector.querystring.s.value, 'OXzgmCxiDmWe8176epI0AeQpZJd2jEHtf76l_Y5PjDo');
    assert.equal((await gate({request: vector})).uri, '/c/' + install + vectorPath);
});

test('generated visual behavior serves signed cleaned HTML and SVG using fixed templates', async () => {
    const gate = load('Gate');
    const respond = load('VisualsResponse');
    for (const type of ['text/html', 'TEXT/HTML; charset=latin1', 'image/svg+xml']) {
        const req = await gate({request: request()});
        assert.equal(req.uri, '/c/' + install + visualPath);
        const stored = origin(type);
        const out = await respond({request: req, response: stored});
        assert.equal(out.statusCode, 200);
        assert.deepEqual(out.body, stored.body);
        ceiling(out);
        assert.match(csp(out), /frame-ancestors 'self' https:\/\/app.example http:\/\/localhost:8765;/);
        assert.equal(out.headers['access-control-allow-origin'], undefined);
        assert.equal(out.headers['content-disposition'].value, 'inline');
        assert.equal(out.headers['cache-control'].value, 'private, no-cache');
        assert.ok(!Object.keys(out.headers).some(x => x.startsWith('x-amz-meta-')));
        if (type === 'image/svg+xml') {
            assert.ok(!csp(out).includes('script-src'));
            assert.ok(csp(out).endsWith('; sandbox'));
        } else {
            assert.ok(csp(out).includes('script-src ' + script + ';'));
            assert.equal(out.headers['content-type'].value, 'text/html; charset=utf-8');
        }
    }
});

test('hostile object headers, metadata and body never widen the ceiling or set cookies', async () => {
    const respond = load('VisualsResponse');
    const stored = origin();
    const headers = stored.headers;
    headers['content-security-policy'] = {value: 'sandbox allow-same-origin', multiValue: [{value: 'default-src *'}]};
    headers['content-security-policy-report-only'] = {value: 'report-uri https://leak.example'};
    headers['set-cookie'] = {value: 's=1; Domain=files.example'};
    stored.cookies = {s: {value: '1', attributes: 'Domain=files.example', multiValue: [{value: '2'}]}};
    headers['access-control-allow-origin'] = {value: '*'};
    headers['x-amz-meta-tico-visual-csp'] = {value: 'sandbox allow-same-origin'};
    headers['x-amz-meta-tico-visual-ancestors'].value = '* https: https://a.example; sandbox allow-same-origin';
    headers['x-amz-meta-tico-visual-script'].value = "'unsafe-inline' 'unsafe-eval' 'strict-dynamic' https://bad.example";
    stored.body.data = '<meta http-equiv="Content-Security-Policy" content="sandbox allow-same-origin">' +
        '<meta http-equiv="Set-Cookie" content="s=1"><script>document.cookie="s=1"</script>';
    const out = await respond({response: stored});
    ceiling(out);
    assert.ok(csp(out).includes("frame-ancestors 'none';"));
    assert.ok(csp(out).includes("script-src 'none';"));
    assert.deepEqual(out.body, stored.body, 'edge enforces headers; app owns body cleaning');
    const capped = origin();
    capped.headers['x-amz-meta-tico-visual-ancestors'].value = Array(10).fill('https://app.example').join(' ');
    capped.headers['x-amz-meta-tico-visual-script'].value = Array(6).fill(script).join(' ');
    const limited = csp(await respond({response: capped}));
    assert.equal(limited.match(/https:\/\/app.example/g).length, 8);
    assert.equal(limited.match(/sha256-/g).length, 4);
    for (const mutate of [
        r => delete r.headers['x-amz-meta-tico-visual-policy-version'],
        r => r.headers['x-amz-meta-tico-visual-policy-version'].value = '2',
        r => r.headers['content-type'].value = 'application/javascript',
        r => r.headers['content-type'].multiValue = [{value: 'text/html'}],
        r => r.statusCode = 206,
        r => {r.statusCode = 302; r.headers.location = {value: 'https://bad.example'};},
    ]) {
        const invalid = origin(); mutate(invalid);
        const result = await respond({response: invalid});
        ceiling(result);
        assert.equal(result.statusCode, 404);
        assert.equal(result.body.data, '');
        assert.equal(result.headers.location, undefined);
        assert.equal(csp(result), "default-src 'none'; sandbox");
    }
});

test('distribution wiring preserves files and error fallback without stacking the visual CSP', async () => {
    const config = resources.Distribution.Properties.DistributionConfig;
    const behaviors = [config.DefaultCacheBehavior, ...config.CacheBehaviors];
    // Disabled unless the stand-up passes Enabled=true; standard logs only when given a log bucket.
    assert.deepEqual(config.Enabled, {'Fn::If': ['Activate', true, false]});
    assert.deepEqual(template.Conditions.Activate, {'Fn::Equals': [{Ref: 'Enabled'}, 'true']});
    assert.equal(template.Parameters.Enabled.Default, 'false');
    assert.deepEqual(config.Logging['Fn::If'][0], 'Logs');
    assert.deepEqual(config.Logging['Fn::If'][1], {Bucket: {Ref: 'LogBucketDomainName'}, Prefix: {Ref: 'LogPrefix'},
        IncludeCookies: false});
    const logBucket = new RegExp('^(?:' + template.Parameters.LogBucketDomainName.AllowedPattern + ')$');
    assert.ok(logBucket.test('ticofiles-logs-1.s3.amazonaws.com') && logBucket.test(''));
    assert.ok(!logBucket.test('ticofiles-logs-1') && !logBucket.test('logs.s3.us-east-1.amazonaws.com.evil'));
    assert.equal(config.CacheBehaviors[0].PathPattern, '/visuals/*');
    for (const [i, behavior] of behaviors.entries()) {
        const name = i ? 'Visuals' : 'Files';
        assert.deepEqual(behavior.FunctionAssociations.map(x => [x.EventType, x.FunctionARN['Fn::GetAtt'][0]]),
            [['viewer-request', 'Gate'], ['viewer-response', name + 'Response']]);
        const policy = resources[behavior.ResponseHeadersPolicyId.Ref].Properties.ResponseHeadersPolicyConfig;
        assert.deepEqual(policy.SecurityHeadersConfig.ContentSecurityPolicy,
            {ContentSecurityPolicy: "default-src 'none'; sandbox", Override: true});
        assert.ok(policy.RemoveHeadersConfig.Items.some(x => x.Header === 'Set-Cookie'));
        // CloudFront refuses CORS headers as custom headers (stand-up, Oct 29): files use its CORS config.
        assert.ok(!policy.CustomHeadersConfig.Items.some(x => /^access-control-/i.test(x.Header)));
        const removesCors = policy.RemoveHeadersConfig.Items.some(x => /^access-control-/i.test(x.Header));
        if (i) assert.ok(removesCors && !policy.CorsConfig);
        else {
            assert.ok(!removesCors);
            assert.deepEqual(policy.CorsConfig.AccessControlAllowOrigins.Items, ['*']);
            assert.equal(policy.CorsConfig.AccessControlAllowCredentials, false);
            assert.equal(policy.CorsConfig.OriginOverride, true);
        }
        assert.ok(policy.RemoveHeadersConfig.Items.some(x => x.Header === 'Content-Security-Policy-Report-Only'));
        assert.equal(policy.SecurityHeadersConfig.ContentTypeOptions.Override, true);
        assert.equal(policy.SecurityHeadersConfig.ReferrerPolicy.ReferrerPolicy, 'no-referrer');
        assert.equal(resources[behavior.CachePolicyId.Ref].Properties.CachePolicyConfig.MaxTTL, 0);
        // Policies run before viewer-response, including a restrictive CSP for skipped origin errors.
        const stored = origin();
        stored.headers['content-security-policy'] = {value: policy.SecurityHeadersConfig.ContentSecurityPolicy.ContentSecurityPolicy};
        const out = await load(name + 'Response')({response: stored});
        ceiling(out);
        assert.equal(Object.keys(out.headers).filter(x => x === 'content-security-policy').length, 1);
        if (i) assert.ok(csp(out).includes('script-src ' + script));
        else {
            assert.equal(csp(out), "default-src 'none'; sandbox");
            assert.equal(out.headers['access-control-allow-origin'].value, '*');
        }
    }
});
