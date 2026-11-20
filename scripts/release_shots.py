#!/usr/bin/env python3
"""Releases have no before and after pictures, so this does nothing. .github/workflows/release-shots.yml still runs it
as a step (called by train.yml); delete this file with that workflow and train.yml's shots and approval-shots jobs."""

if __name__ == "__main__":
    print("release shots: releases have no pictures")
