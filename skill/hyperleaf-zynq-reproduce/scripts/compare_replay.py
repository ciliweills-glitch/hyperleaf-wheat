"""按ImageId比较板端PhiPS2与整数/F48参考，输出逐样本偏差。"""
import argparse
import csv
import json
from pathlib import Path


def load(path, column):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    values = {row['ImageId'].zfill(5): float(row[column]) for row in rows}
    if len(values) != len(rows):
        raise ValueError(f'{path}: ImageId有重复，无法一一对应')
    return values


def compare(reference, prediction, output, reference_column='integer_F48', prediction_column='PhiPS2'):
    expected, actual = load(reference, reference_column), load(prediction, prediction_column)
    ids = sorted(expected.keys() & actual.keys())
    if not ids:
        raise ValueError('两份外部CSV没有共同ImageId')
    output.mkdir(parents=True, exist_ok=True)
    rows = [dict(ImageId=sid, reference=expected[sid], prediction=actual[sid],
                 difference=actual[sid]-expected[sid], abs_diff=abs(actual[sid]-expected[sid])) for sid in ids]
    with (output / 'differences.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(reference=str(reference), prediction=str(prediction),
                   reference_column=reference_column, prediction_column=prediction_column,
                   reference_count=len(expected), prediction_count=len(actual), compared_count=len(ids),
                   missing_prediction=sorted(expected.keys()-actual.keys()),
                   outside_reference=sorted(actual.keys()-expected.keys()),
                   max_abs_diff=max(row['abs_diff'] for row in rows),
                   mean_abs_diff=sum(row['abs_diff'] for row in rows)/len(rows))
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--prediction', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-column', default='integer_F48')
    parser.add_argument('--prediction-column', default='PhiPS2')
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.prediction, args.output,
                             args.reference_column, args.prediction_column), ensure_ascii=False))


if __name__ == '__main__':
    main()
