"""Opt-in real STT gate using macOS generated speech. No ambient input or playback.

Use TICO_REAL_RECORDER_STT_KEY or --keychain; credentials never enter arguments.
This verifies the real STT transport/recognition, not physical audio capture.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--keychain', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Generated-speech acceptance currently requires macOS say')
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    phrase = ('The recorder pilot starts Friday. The meeting transcript stays private until '
              'I choose to send it to Tico. Please create a task to review the launch plan.')
    audio = args.output / 'generated-speech.wav'
    subprocess.run(['/usr/bin/say', '--file-format=WAVE', '--data-format=LEI16@16000',
                    '-o', str(audio), phrase], check=True)
    audio.chmod(0o600)
    command = [str(args.cli.resolve()), 'verify-transcription', str(audio),
               '--expected', phrase, '--report', str(args.output / 'real-stt-receipt.json')]
    if args.keychain:
        command.append('--keychain')
    result = subprocess.run(command, env={**os.environ}, timeout=90)
    receipt = json.loads((args.output / 'real-stt-receipt.json').read_text())
    print(json.dumps({'status': receipt['status'],
                      'inference_attempted': receipt['inference_attempted'],
                      'physical_capture_verified': False}))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
