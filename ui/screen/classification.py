"""User-selected PhiPS2 numerical grades, and the separate NNI literature rule.

PhiPS2 cutoffs were specified by the operator on 2026-10-14.
NNI source: https://doi.org/10.3390/agronomy9100619
The NNI study uses NNI < 1, 1 <= NNI <= 1.25, and NNI > 1.25.
"""

LOW_THRESHOLD = 0.45
HIGH_THRESHOLD = 0.7


def classify_phips2(value):
    if value < LOW_THRESHOLD:
        return 'low'
    if value > HIGH_THRESHOLD:
        return 'high'
    return 'medium'


def classify_nni(nni):
    if nni < 1.0:
        return 'deficiency'
    if nni > 1.25:
        return 'excess'
    return 'normal'
