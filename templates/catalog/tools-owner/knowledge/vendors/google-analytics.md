# Google Analytics 4 Data API

Docs: https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart-client-libraries, https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport, https://support.google.com/analytics/answer/9305788 (checked 2026-11-16)

## Credential
- Where: (1) Cloud Console: enable the Google Analytics Data API v1 on the project; IAM & Admin > Service Accounts > Create, then Keys > Add key > JSON. (2) Analytics > Admin > Property > Access Management > + > Add users: the service account's `client_email`, role Viewer.
- Kinds: service account JSON, or OAuth refresh token for one person.
- Read-only: Viewer role on the property; scope `https://www.googleapis.com/auth/analytics.readonly` (the doc lists `.../auth/analytics` as the other accepted scope).
- Shape: service account JSON file; the library signs a JWT and sends a Bearer token.
- Variable: `GOOGLE_APPLICATION_CREDENTIALS` (path to the file key).
- Expiry: key none; access tokens ~1 hour.

## Check
- Reports are `POST https://analyticsdata.googleapis.com/v1beta/properties/<id>:runReport`, so there is no cheap Data API GET. A bot checks by running a one-row report. Property listing: `GET https://analyticsadmin.googleapis.com/v1beta/accountSummaries` needs the Admin API enabled too (unconfirmed). 401: bad token. 403: the account has no access to that property or the API is off.

## In Tico
- Route: on computers, file key. Not on the proxy.
- Settings (not keys): property id (digits only, used as `properties/<id>`; find it in Analytics > Admin > Property settings (unconfirmed path)). Do not confuse it with the `G-XXXX` measurement id.
- MCP: Google's `googleanalytics/analytics-mcp` is local and read-only (ADC or service account); no remote server.

## Pitfalls
- Access must be granted on the property in Analytics, not in IAM.
- Users are identified by email, so the service account email must be typed exactly.
- Universal Analytics properties are gone; GA4 only.
