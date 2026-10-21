"""Summarize a large Unity startup log with bounded diagnostic output."""
import argparse
import collections
import json
from pathlib import Path


def inspect(path):
    previous = collections.deque(maxlen=30)
    result = {'log': str(path.resolve()), 'bytes': path.stat().st_size,
              'telemetry_assembly_loaded': False, 'telemetry_main_menu_ready': False,
              'exception_samples': [], 'first_allocation_error_context': []}
    with path.open(encoding='utf-8-sig', errors='replace') as stream:
        line_number = 0
        for line_number, line in enumerate(stream, 1):
            text = line.rstrip()[:1000]
            if 'AssemblyLoader: Loading assembly' in text and 'C3MassTelemetry.dll' in text:
                result['telemetry_assembly_loaded'] = True
            if '[C3MassTelemetry] schema=2 loaded' in text:
                result['telemetry_main_menu_ready'] = True
            if 'Exception:' in text and len(result['exception_samples']) < 15:
                result['exception_samples'].append({'line': line_number, 'text': text})
            if text == 'is negative' and not result['first_allocation_error_context']:
                result['first_allocation_error_context'] = list(previous) + [{'line': line_number, 'text': text}]
            previous.append({'line': line_number, 'text': text})
    result['lines'] = line_number
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.log.resolve():
        parser.error('Preserve original log')
    result = inspect(args.log)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ['bytes', 'lines', 'telemetry_assembly_loaded', 'telemetry_main_menu_ready']}))
