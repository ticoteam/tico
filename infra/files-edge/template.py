"""Render the edge-only CloudFormation template to stdout. Never calls AWS.

The private bucket, its policy and the certificate are supplied by the stand-up. The distribution stays disabled
unless the stand-up passes Enabled=true, and writes CloudFront standard logs (HQ meters delivery from them,
tico-services billing/files_aws.py) when it is given LogBucketDomainName.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def template():
    resources = {}
    resources['Keys'] = {'Type': 'AWS::CloudFront::KeyValueStore', 'Properties': {
        'Name': {'Fn::Sub': '${AWS::StackName}-keys'}}}
    request_parts = (ROOT / 'request.js').read_text().split('__FILES_DOMAIN__')
    resources['Gate'] = {'Type': 'AWS::CloudFront::Function', 'Properties': {
        'Name': {'Fn::Sub': '${AWS::StackName}-gate'}, 'AutoPublish': True,
        'FunctionConfig': {'Comment': 'Tenant-bound signed file and visual requests',
                           'Runtime': 'cloudfront-js-2.0',
                           'KeyValueStoreAssociations': [{'KeyValueStoreARN': {'Fn::GetAtt': ['Keys', 'Arn']}}]},
        'FunctionCode': {'Fn::Join': ['', [request_parts[0], {'Ref': 'FilesDomain'}, request_parts[1]]]}}}
    for name, visual in [('Files', False), ('Visuals', True)]:
        resources[name + 'Response'] = {'Type': 'AWS::CloudFront::Function', 'Properties': {
            'Name': {'Fn::Sub': '${AWS::StackName}-' + name.lower()}, 'AutoPublish': True,
            'FunctionConfig': {'Comment': 'Fixed sandbox ceiling for ' + name.lower(), 'Runtime': 'cloudfront-js-2.0'},
            'FunctionCode': (ROOT / 'response.js').read_text().replace('__VISUAL__', str(visual).lower())}}
        custom = [{'Header': 'Cross-Origin-Resource-Policy', 'Value': 'cross-origin', 'Override': True}]
        cors = {}
        if visual:
            custom.append({'Header': 'Cache-Control', 'Value': 'private, no-cache', 'Override': True})
        else:
            # CloudFront refuses CORS headers as custom headers; its CORS config is the policy-layer fallback
            # (origin errors skip viewer-response, which sets Access-Control-Allow-Origin: * itself).
            cors = {'CorsConfig': {'AccessControlAllowOrigins': {'Items': ['*']},
                                   'AccessControlAllowMethods': {'Items': ['GET', 'HEAD']},
                                   'AccessControlAllowHeaders': {'Items': ['*']},
                                   'AccessControlAllowCredentials': False, 'OriginOverride': True}}
        resources[name + 'Headers'] = {'Type': 'AWS::CloudFront::ResponseHeadersPolicy', 'Properties': {
            'ResponseHeadersPolicyConfig': {
                **cors,
                'Name': {'Fn::Sub': '${AWS::StackName}-' + name.lower()},
                'SecurityHeadersConfig': {
                    'ContentSecurityPolicy': {'ContentSecurityPolicy': "default-src 'none'; sandbox", 'Override': True},
                    'ContentTypeOptions': {'Override': True},
                    'ReferrerPolicy': {'ReferrerPolicy': 'no-referrer', 'Override': True}},
                'CustomHeadersConfig': {'Items': custom},
                # Metadata is needed by viewer-response; S3 errors have no object metadata.
                # Files' CORS config overrides origin CORS headers; visuals have none, so remove them.
                'RemoveHeadersConfig': {'Items': [{'Header': h} for h in [
                    'Set-Cookie', 'Content-Security-Policy-Report-Only'] + ([
                        'Access-Control-Allow-Origin', 'Access-Control-Allow-Credentials',
                        'Access-Control-Allow-Headers', 'Access-Control-Allow-Methods',
                        'Access-Control-Expose-Headers', 'Access-Control-Max-Age'] if visual else [])]}}}}
    # Zero TTL prevents mutable metadata or company Cache-Control from freezing a stale policy.
    resources['Cache'] = {'Type': 'AWS::CloudFront::CachePolicy', 'Properties': {'CachePolicyConfig': {
        'Name': {'Fn::Sub': '${AWS::StackName}-no-cache'}, 'MinTTL': 0, 'DefaultTTL': 0, 'MaxTTL': 0,
        'ParametersInCacheKeyAndForwardedToOrigin': {
            'EnableAcceptEncodingGzip': False, 'EnableAcceptEncodingBrotli': False,
            'HeadersConfig': {'HeaderBehavior': 'none'}, 'CookiesConfig': {'CookieBehavior': 'none'},
            'QueryStringsConfig': {'QueryStringBehavior': 'none'}}}}}
    resources['OriginAccess'] = {'Type': 'AWS::CloudFront::OriginAccessControl', 'Properties': {
        'OriginAccessControlConfig': {'Name': {'Fn::Sub': '${AWS::StackName}-s3'},
                                     'OriginAccessControlOriginType': 's3',
                                     'SigningBehavior': 'always', 'SigningProtocol': 'sigv4'}}}

    def behavior(name):
        return {'TargetOriginId': 'Objects', 'ViewerProtocolPolicy': 'https-only',
                'AllowedMethods': ['GET', 'HEAD'], 'CachedMethods': ['GET', 'HEAD'], 'Compress': False,
                'CachePolicyId': {'Ref': 'Cache'}, 'ResponseHeadersPolicyId': {'Ref': name + 'Headers'},
                'FunctionAssociations': [
                    {'EventType': 'viewer-request', 'FunctionARN': {'Fn::GetAtt': ['Gate', 'FunctionARN']}},
                    {'EventType': 'viewer-response', 'FunctionARN': {'Fn::GetAtt': [name + 'Response', 'FunctionARN']}}]}

    visual_behavior = dict(behavior('Visuals'), PathPattern='/visuals/*')
    resources['Distribution'] = {'Type': 'AWS::CloudFront::Distribution', 'Properties': {'DistributionConfig': {
        'Enabled': {'Fn::If': ['Activate', True, False]},
        'Comment': 'Tico file edge; enable only after the bucket policy and certificate are in place',
        'Logging': {'Fn::If': ['Logs', {'Bucket': {'Ref': 'LogBucketDomainName'}, 'Prefix': {'Ref': 'LogPrefix'},
                                        'IncludeCookies': False}, {'Ref': 'AWS::NoValue'}]},
        'Aliases': [{'Fn::Sub': '*.${FilesDomain}'}], 'PriceClass': 'PriceClass_100',
        'ViewerCertificate': {'AcmCertificateArn': {'Ref': 'CertificateArn'},
                              'SslSupportMethod': 'sni-only', 'MinimumProtocolVersion': 'TLSv1.2_2021'},
        'Origins': [{'Id': 'Objects', 'DomainName': {'Ref': 'BucketRegionalDomainName'},
                     'OriginAccessControlId': {'Ref': 'OriginAccess'}, 'S3OriginConfig': {'OriginAccessIdentity': ''}}],
        'DefaultCacheBehavior': behavior('Files'), 'CacheBehaviors': [visual_behavior],
        'CustomErrorResponses': [{'ErrorCode': code, 'ErrorCachingMinTTL': 0} for code in [400, 403, 404, 500, 502, 503, 504]]}}}
    return {'AWSTemplateFormatVersion': '2010-09-09', 'Description': 'Tico file edge (signed company files and visuals)',
            'Parameters': {
                'FilesDomain': {'Type': 'String', 'AllowedPattern': '[a-z0-9]+([.-][a-z0-9]+)*'},
                'CertificateArn': {'Type': 'String'}, 'BucketRegionalDomainName': {'Type': 'String'},
                'Enabled': {'Type': 'String', 'AllowedValues': ['false', 'true'], 'Default': 'false'},
                # Standard (legacy) logs: the bucket needs ACLs enabled (ObjectOwnership BucketOwnerPreferred).
                'LogBucketDomainName': {'Type': 'String', 'Default': '',
                                        'AllowedPattern': '([a-z0-9][a-z0-9.-]*\\.s3\\.amazonaws\\.com)?'},
                'LogPrefix': {'Type': 'String', 'Default': 'cloudfront/'}},
            'Conditions': {'Activate': {'Fn::Equals': [{'Ref': 'Enabled'}, 'true']},
                           'Logs': {'Fn::Not': [{'Fn::Equals': [{'Ref': 'LogBucketDomainName'}, '']}]}},
            'Resources': resources,
            'Outputs': {'DistributionId': {'Value': {'Ref': 'Distribution'}},
                        'KeysArn': {'Value': {'Fn::GetAtt': ['Keys', 'Arn']}}}}


if __name__ == '__main__':
    print(json.dumps(template(), indent=2))
