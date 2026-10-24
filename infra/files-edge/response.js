// The build fixes this boolean per behavior; no viewer-controlled routing selects the policy.
const visual = __VISUAL__;

function value(headers, name) {
    const item = headers[name];
    return item && !item.multiValue && typeof item.value === 'string' ? item.value : '';
}

function sources(raw, pattern, limit) {
    return raw.split(/\s+/).filter(token => pattern.test(token)).slice(0, limit).join(' ') || "'none'";
}

function handler(event) {
    const response = event.response;
    const headers = response.headers;
    const version = value(headers, 'x-amz-meta-tico-visual-policy-version');
    const kind = value(headers, 'content-type').split(';')[0].trim().toLowerCase();
    const ancestors = sources(value(headers, 'x-amz-meta-tico-visual-ancestors'),
        /^(?:'self'|https:\/\/[a-z0-9.-]+(?::[0-9]{1,5})?|http:\/\/(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?)$/, 8);
    const scripts = sources(value(headers, 'x-amz-meta-tico-visual-script'), /^'sha256-[A-Za-z0-9+/]{43}='$/, 4);
    // CloudFront represents Set-Cookie separately from response.headers.
    response.cookies = {};
    Object.keys(headers).forEach(name => {
        if (name.startsWith('x-amz-meta-') || name === 'set-cookie' ||
            name === 'content-security-policy' || name === 'content-security-policy-report-only' ||
            name.startsWith('access-control-')) delete headers[name];
    });
    let policy = "default-src 'none'; sandbox";
    headers['x-content-type-options'] = {value: 'nosniff'};
    headers['referrer-policy'] = {value: 'no-referrer'};
    headers['cross-origin-resource-policy'] = {value: 'cross-origin'};
    if (visual) {
        headers['cache-control'] = {value: 'private, no-cache'};
        headers['content-disposition'] = {value: 'inline'};
        if (version !== '1' || (kind !== 'text/html' && kind !== 'image/svg+xml') ||
            (response.statusCode !== 200 && response.statusCode !== 304)) {
            response.statusCode = 404;
            response.statusDescription = 'Not Found';
            response.body = {encoding: 'text', data: ''};
            delete headers['content-encoding'];
            delete headers['content-length'];
            delete headers['content-range'];
            delete headers.location;
            headers['content-type'] = {value: 'text/plain; charset=utf-8'};
        } else if (kind === 'text/html') {
            headers['content-type'] = {value: 'text/html; charset=utf-8'};
            policy = "default-src 'none'; script-src " + scripts + "; img-src data:; font-src data:; media-src data:; " +
                "style-src 'unsafe-inline' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; " +
                'frame-ancestors ' + ancestors + '; sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox';
        } else {
            headers['content-type'] = {value: 'image/svg+xml'};
            policy = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline' data:; " +
                'frame-ancestors ' + ancestors + '; sandbox';
        }
    } else {
        headers['access-control-allow-origin'] = {value: '*'};
    }
    // Replacing the complete header drops origin values and multiValue CSP copies.
    headers['content-security-policy'] = {value: policy};
    return response;
}
