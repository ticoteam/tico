---
name: AWS
category: engineering
summary: "Amazon Web Services from the aws CLI: S3, EC2, logs, SSM and the rest"
commands: [aws]
---
## What it is for
Reading and changing the company's AWS resources: files in S3, servers, logs, parameters and costs.

## How to use it
The CLI reads its keys from the variables Tools gives your run (`AWS_ACCESS_KEY_ID` and its pair) and the region from
`AWS_REGION` or a setting on the tool. Use `--profile` only when the tool's guidance names one.

    aws sts get-caller-identity
    aws s3 ls s3://<bucket>/<prefix>/ --recursive --human-readable | tail -20
    aws logs tail /aws/<group> --since 1h
    aws ce get-cost-and-usage --time-period Start=2026-11-01,End=2026-11-24 --granularity DAILY --metrics UnblendedCost

## Good to know
- `--output json` with `--query` gives exact fields for a script.
- Ask before deleting anything or changing what costs money.
